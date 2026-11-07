"""Browser reference uploads and shared bounded edit-input preparation."""

import asyncio
import json
from functools import partial

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from .references import MAX_IMAGE_BYTES, MAX_REQUEST_BYTES, decode_base64, source_bytes


async def read_input(request):
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_REQUEST_BYTES:
            raise HTTPException(413, "Image requests must be at most 24 MiB")
    content_type = request.headers.get("content-type", "").lower()
    if content_type.startswith("application/json"):
        try:
            value = json.loads(body)
            if not isinstance(value, dict):
                raise ValueError("Expected an object")
            return value, None
        except (ValueError, RecursionError) as exc:
            raise HTTPException(
                400, "Send a JSON object with image input and edit instructions"
            ) from exc
    if not content_type.startswith("multipart/form-data"):
        raise HTTPException(415, "Use multipart/form-data or application/json")

    async def chunks():
        yield bytes(body)

    try:
        form = await MultiPartParser(
            request.headers,
            chunks(),
            max_files=1,
            max_fields=24,
            max_part_size=MAX_IMAGE_BYTES,
        ).parse()
    except (MultiPartException, ValueError) as exc:
        raise HTTPException(
            400, "Upload exactly one reference image with valid multipart fields"
        ) from exc
    try:
        values, image = {}, None
        for key, value in form.multi_items():
            if isinstance(value, UploadFile):
                if key not in {"image", "image[]"} or image is not None:
                    raise HTTPException(
                        400,
                        "Only one reference image is supported; masks are not supported",
                    )
                image = await value.read(MAX_IMAGE_BYTES + 1)
                if len(image) > MAX_IMAGE_BYTES:
                    raise HTTPException(413, "Reference images must be at most 16 MiB")
            else:
                if key in values:
                    raise HTTPException(400, "Duplicate image request field: " + key)
                values[key] = value
        for key in ("n", "seed"):
            if key in values:
                try:
                    values[key] = None if values[key] == "null" else int(values[key])
                except ValueError as exc:
                    raise HTTPException(400, key + " must be a whole number") from exc
        for key in ("save_gallery", "stream", "preserve_green"):
            if key in values:
                if values[key] not in {"true", "false"}:
                    raise HTTPException(400, key + " must be true or false")
                values[key] = values[key] == "true"
        return values, image
    finally:
        await form.close()


async def prepare_reference(
    jobs,
    *,
    reference_id=None,
    source_job_id=None,
    image_data=None,
    preserve_green=False,
):
    if (
        sum(value is not None for value in (reference_id, source_job_id, image_data))
        != 1
    ):
        raise HTTPException(
            400,
            "Choose exactly one reference: an image, a reference_id or a source_job_id",
        )
    if reference_id is not None:
        jobs.references.path(reference_id)
        return reference_id, False

    def prepare():
        data = image_data
        if source_job_id is not None:
            data = source_bytes(jobs, source_job_id)
        elif isinstance(data, str):
            data = decode_base64(data)
        return jobs.references.create(
            data, source_job_id, **({"preserve_green": True} if preserve_green else {})
        )

    result = await asyncio.get_running_loop().run_in_executor(jobs.output_pool, prepare)
    return result["id"], True


def attach_reference_api(app, jobs):
    @app.post("/api/references", status_code=201)
    async def upload(request: Request):
        values, image = await read_input(request)
        preserve_green = values.pop("preserve_green", False)
        if not isinstance(preserve_green, bool):
            raise HTTPException(400, "preserve_green must be true or false")
        if image is not None:
            if values:
                raise HTTPException(400, "Upload one image without additional fields")
            return await asyncio.get_running_loop().run_in_executor(
                jobs.output_pool,
                partial(jobs.references.create, preserve_green=preserve_green),
                image,
            )
        if set(values) != {"source_job_id"} or not isinstance(
            values["source_job_id"], str
        ):
            raise HTTPException(400, "Supply a source_job_id or upload one image")
        data = await asyncio.to_thread(source_bytes, jobs, values["source_job_id"])
        return await asyncio.get_running_loop().run_in_executor(
            jobs.output_pool,
            partial(jobs.references.create, preserve_green=preserve_green),
            data,
            values["source_job_id"],
        )

    @app.get("/api/references/{identifier}/image")
    def preview(identifier: str):
        return FileResponse(jobs.references.path(identifier), media_type="image/png")

    @app.delete("/api/references/{identifier}")
    def discard(identifier: str):
        jobs.references.delete(identifier)
        return {"id": identifier, "deleted": True}

    @app.get("/api/jobs/{identifier}/reference")
    def saved_reference(identifier: str):
        item = jobs.get(identifier)
        path = jobs.folder(item) / "reference.png"
        if not item["settings"].get("reference") or not path.is_file():
            raise HTTPException(404, "This job has no retained reference image")
        return FileResponse(path, media_type="image/png", filename="reference.png")
