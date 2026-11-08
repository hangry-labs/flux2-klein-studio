"""Synchronous agent responses over a shared FIFO GPU queue."""

import asyncio
import os
import secrets

from fastapi import HTTPException

from .cli import MODEL_ID, MODEL_REVISION
from .images import encode_image
from .presets import SIZES
from .schemas import Generation

MODEL_ALIAS = "flux2-klein-4b"
GENERATION_TIMEOUT = 310
QUEUE_TIMEOUT = float(os.getenv("STUDIO_QUEUE_TIMEOUT_SECONDS", "900"))
MAX_BATCH = 16


def _admit(
    jobs,
    prompt,
    resolution,
    seed,
    save_gallery,
    count,
    reference_id=None,
    adapter=None,
    reference_ids=None,
):
    with jobs.lock:
        resolution = jobs.resolutions.choose(resolution)
        width, height = SIZES[resolution]
        options = [
            Generation(
                prompt=prompt,
                width=width,
                height=height,
                seed=secrets.randbits(53) if seed is None else (seed + index) % 2**53,
                save_gallery=save_gallery,
                reference_id=reference_id,
                reference_ids=reference_ids,
                adapter=adapter,
            )
            for index in range(count)
        ]
        return resolution, jobs.submit_many(options, owned=True)


async def _poll(jobs, identifier, disconnected, *, queued_only=False):
    while True:
        if disconnected and await disconnected():
            raise HTTPException(499, "Client disconnected; request jobs cancelled")
        item = jobs.get(identifier)
        if queued_only and item["status"] != "queued":
            return item
        if item["status"] == "completed":
            return item
        if item["status"] == "failed":
            raise HTTPException(502, item["error"])
        if item["status"] == "cancelled":
            raise HTTPException(409, "Generation was cancelled")
        await asyncio.sleep(0.05)


async def _result(jobs, identifier, resolution, output_format, disconnected):
    try:
        async with asyncio.timeout(QUEUE_TIMEOUT):
            await _poll(jobs, identifier, disconnected, queued_only=True)
    except TimeoutError as exc:
        raise HTTPException(504, "Image exceeded the queue waiting time limit") from exc
    try:
        async with asyncio.timeout(GENERATION_TIMEOUT):
            item = await _poll(jobs, identifier, disconnected)
            _, path = jobs.image_path(identifier)
            data = await asyncio.get_running_loop().run_in_executor(
                jobs.output_pool, encode_image, path, output_format, item["settings"]
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
                "queue_seconds": item.get("queue_seconds", 0),
                "pixel_sha256": item["pixel_sha256"],
                "reduction": "ordered",
                "created": int(item["created"]),
            }
    except TimeoutError as exc:
        raise HTTPException(504, "Generation exceeded the request time limit") from exc


async def generate_images(
    jobs,
    prompt,
    resolution,
    seed=None,
    output_format="png",
    save_gallery=False,
    disconnected=None,
    count=1,
    reference_id=None,
    adapter=None,
    reference_ids=None,
):
    if not 1 <= count <= MAX_BATCH:
        raise HTTPException(400, f"Request between 1 and {MAX_BATCH} images")
    submission = asyncio.create_task(
        asyncio.to_thread(
            _admit,
            jobs,
            prompt,
            resolution,
            seed,
            save_gallery,
            count,
            reference_id,
            adapter,
            reference_ids,
        )
    )
    items, tasks = [], []
    try:
        # Shield preparation so a disconnected caller cannot orphan a submission.
        resolution, items = await asyncio.shield(submission)
        tasks = [
            asyncio.create_task(
                _result(jobs, item["id"], resolution, output_format, disconnected)
            )
            for item in items
        ]
        return await asyncio.gather(*tasks)
    finally:

        async def cleanup():
            nonlocal items
            if not items:
                try:
                    _, items = await submission
                except Exception:
                    return
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

            def release():
                for item in items:
                    identifier = item["id"]
                    if jobs.get(identifier)["status"] not in {
                        "completed",
                        "failed",
                        "cancelled",
                    }:
                        jobs.cancel(identifier)
                    jobs.release(identifier)

            await asyncio.to_thread(release)

        await asyncio.shield(cleanup())


async def generate_image(
    jobs,
    prompt,
    resolution,
    seed=None,
    output_format="png",
    save_gallery=False,
    disconnected=None,
):
    return (
        await generate_images(
            jobs, prompt, resolution, seed, output_format, save_gallery, disconnected
        )
    )[0]
