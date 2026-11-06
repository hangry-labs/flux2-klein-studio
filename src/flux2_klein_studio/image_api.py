"""A documented OpenAI Images subset with discoverable local extensions."""

import asyncio
import base64
from typing import Literal

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from .cli import MODEL_ID
from .generation_service import MODEL_ALIAS, MAX_BATCH, generate_images
from .presets import catalog, resolve_size
from .schemas import Generation
from .transport import client_disconnected


class ImageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    prompt: str = Field(min_length=1, max_length=4000)
    model: str = MODEL_ALIAS
    size: str | None = Field(
        default=None,
        description="Named resolution; discover dimensions at /v1/images/resolutions.",
        json_schema_extra={
            "enum": ["auto"]
            + [r["id"] for r in catalog()]
            + [r["size"] for r in catalog()]
        },
    )
    n: int = Field(default=1, ge=1, le=MAX_BATCH)
    response_format: Literal["b64_json"] = "b64_json"
    output_format: Literal["png", "webp", "jpeg"] = "png"
    quality: Literal["auto", "standard"] = "auto"
    stream: Literal[False] = False
    seed: int | None = Field(default=None, ge=0, le=2**53 - 1)
    save_gallery: bool = False

    @field_validator("prompt")
    @classmethod
    def nonblank(cls, value):
        return Generation.nonblank(value)

    @field_validator("model")
    @classmethod
    def known_model(cls, value):
        if value not in (MODEL_ALIAS, MODEL_ID):
            raise ValueError(f"Use model {MODEL_ALIAS}")
        return value

    @field_validator("size")
    @classmethod
    def known_size(cls, value):
        if value is not None:
            resolve_size(value)
        return value


def error_response(status, message, param=None, headers=None):
    return JSONResponse(
        {
            "error": {
                "message": message,
                "type": "invalid_request_error" if status < 500 else "server_error",
                "param": param,
                "code": "queue_full" if status == 429 else str(status),
            }
        },
        status_code=status,
        headers=headers,
    )


def attach_image_api(app, jobs):
    original_openapi = app.openapi

    def openapi():
        schema = original_openapi()
        with jobs.lock:
            choices = jobs.resolutions.catalog()
            default = jobs.resolutions.default()
        field = schema["components"]["schemas"]["ImageRequest"]["properties"]["size"]
        field["enum"] = (
            ([None, "auto"] + [r["id"] for r in choices] + [r["size"] for r in choices])
            if choices
            else [None]
        )
        field["default"] = default
        return schema

    app.openapi = openapi

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        if request.url.path.startswith("/v1/"):
            first = exc.errors()[0]
            return error_response(400, first["msg"], str(first["loc"][-1]))
        return await request_validation_exception_handler(request, exc)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        if request.url.path.startswith("/v1/"):
            return error_response(exc.status_code, str(exc.detail), headers=exc.headers)
        return await http_exception_handler(request, exc)

    def model_entry():
        return {"id": MODEL_ALIAS, "object": "model", "created": 0, "owned_by": "local"}

    @app.get("/v1/models")
    def models():
        return {"object": "list", "data": [model_entry()]}

    @app.get("/v1/models/{identifier:path}")
    def model(identifier: str):
        if identifier not in (MODEL_ALIAS, MODEL_ID):
            raise HTTPException(404, "Model not found")
        return model_entry()

    @app.get("/v1/images/resolutions")
    def resolutions():
        with jobs.lock:
            return {
                "default": jobs.resolutions.default(),
                "data": jobs.resolutions.catalog(),
            }

    @app.get("/health/live")
    def live():
        return {"status": "alive"}

    @app.get("/health/ready")
    def ready():
        with jobs.lock:
            enabled = bool(jobs.resolutions.allowed())
            queue = jobs.queue_status()
            available = queue["accepting_requests"] and enabled
            return JSONResponse(
                {
                    "status": "ready"
                    if available
                    else "busy"
                    if enabled
                    else "disabled",
                    "accepting_requests": available,
                    "model": MODEL_ALIAS,
                    "model_state": jobs.model.snapshot()["state"],
                    "queue": queue,
                },
                status_code=200 if available else 503,
            )

    @app.post("/v1/images/generations")
    async def generate(options: ImageRequest, request: Request):
        images = await generate_images(
            jobs,
            options.prompt,
            resolve_size(options.size) if options.size not in (None, "auto") else None,
            seed=options.seed,
            output_format=options.output_format,
            save_gallery=options.save_gallery,
            disconnected=lambda: client_disconnected(request),
            count=options.n,
        )
        metadata = [item[1] for item in images]
        data = await asyncio.to_thread(
            lambda: [
                {"b64_json": base64.b64encode(item[0]).decode("ascii")}
                for item in images
            ]
        )
        return {
            "created": metadata[0]["created"],
            "data": data,
            "size": f"{metadata[0]['width']}x{metadata[0]['height']}",
            "output_format": options.output_format,
            "studio": metadata[0]
            if len(metadata) == 1
            else {"count": len(metadata), "images": metadata},
        }
