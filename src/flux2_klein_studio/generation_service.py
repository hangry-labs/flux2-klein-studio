"""Agent requests share the browser's admission, worker and storage lifecycle."""

import asyncio
import secrets

from fastapi import HTTPException

from .cli import MODEL_ID, MODEL_REVISION
from .images import encode_image
from .presets import SIZES
from .schemas import Generation

MODEL_ALIAS = "flux2-klein-4b"
GENERATION_TIMEOUT = 310


async def generate_image(
    jobs,
    prompt,
    resolution,
    seed=None,
    output_format="png",
    save_gallery=False,
    disconnected=None,
):
    try:
        with jobs.lock:
            resolution = jobs.resolutions.choose(resolution)
            width, height = SIZES[resolution]
            options = Generation(
                prompt=prompt,
                width=width,
                height=height,
                seed=secrets.randbits(53) if seed is None else seed,
                save_gallery=save_gallery,
            )
            item = jobs.submit(options)
    except HTTPException as exc:
        if exc.status_code == 409:
            raise HTTPException(
                429,
                "This instance is busy. Retry or choose another instance.",
                headers={"Retry-After": "1"},
            ) from exc
        raise
    identifier = item["id"]
    try:
        async with asyncio.timeout(GENERATION_TIMEOUT):
            while True:
                if disconnected and await disconnected():
                    raise HTTPException(
                        499, "Client disconnected; generation cancelled"
                    )
                item = jobs.get(identifier)
                if item["status"] == "completed":
                    break
                if item["status"] == "failed":
                    raise HTTPException(502, item["error"])
                if item["status"] == "cancelled":
                    raise HTTPException(409, "Generation was cancelled")
                await asyncio.sleep(0.1)
            _, path = jobs.image_path(identifier)
            data = await asyncio.to_thread(
                encode_image, path, output_format, item["settings"]
            )
            return data, {
                "job_id": identifier,
                "model": MODEL_ALIAS,
                "model_revision": MODEL_REVISION,
                "checkpoint": MODEL_ID,
                "resolution": resolution.value,
                **item["settings"],
                "output_format": output_format,
                "seconds": item["seconds"],
                "pixel_sha256": item["pixel_sha256"],
                "reduction": "ordered",
                "created": int(item["created"]),
            }
    except TimeoutError as exc:
        raise HTTPException(504, "Generation exceeded the request time limit") from exc
    finally:
        if jobs.get(identifier)["status"] not in {"completed", "failed", "cancelled"}:
            await asyncio.shield(asyncio.to_thread(jobs.cancel, identifier))
