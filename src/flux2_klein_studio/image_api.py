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
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from starlette.exceptions import HTTPException as StarletteHTTPException

from .cli import MODEL_ID
from .adapters import ADAPTERS, Adapter, EditAdapter
from .generation_service import MODEL_ALIAS, MAX_BATCH, generate_images
from .presets import catalog, resolve_size
from .schemas import Generation
from .transport import client_disconnected
from .reference_api import prepare_references, read_input
from .references import MAX_REQUEST_BYTES, ZoomRegion


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
    adapter: Adapter | None = Field(
        default=None,
        description="Curated style adapter; prompt is the description and fixed trigger text is added automatically.",
    )

    @field_validator("prompt")
    @classmethod
    def nonblank(cls, value):
        return value.strip()

    @model_validator(mode="after")
    def required_description(self):
        if ADAPTERS.get(self.adapter, {}).get("mode") != "edit":
            Generation.nonblank(self.prompt)
        return self

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


class ImageURL(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    image_url: str = Field(max_length=MAX_REQUEST_BYTES)


class EditRequest(ImageRequest):
    zoom_region: ZoomRegion | None = Field(
        default=None,
        description="For zoom only: rectangle x/y/width/height as source-image fractions from 0 to 1; the server draws the red outline.",
    )
    prompt: str = Field(
        default="",
        max_length=4000,
        description="Required instruction for base editing; optional extra details with outpaint or zoom. The selected adapter's fixed instruction is added automatically.",
    )
    adapter: EditAdapter | None = None
    image: str | None = Field(default=None, max_length=MAX_REQUEST_BYTES)
    images: list[ImageURL] | None = Field(default=None, min_length=1, max_length=4)
    reference_ids: list[str] | None = Field(default=None, min_length=1, max_length=4)
    reference_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    source_job_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")

    @model_validator(mode="after")
    def zoom_mode(self):
        if self.zoom_region is not None and self.adapter != "zoom":
            raise ValueError("zoom_region requires the zoom editing adapter")
        return self


async def image_response(images, output_format):
    metadata = [item[1] for item in images]
    data = await asyncio.to_thread(
        lambda: [
            {"b64_json": base64.b64encode(item[0]).decode("ascii")} for item in images
        ]
    )
    return {
        "created": metadata[0]["created"],
        "data": data,
        "size": f"{metadata[0]['width']}x{metadata[0]['height']}",
        "output_format": output_format,
        "studio": metadata[0]
        if len(metadata) == 1
        else {"count": len(metadata), "images": metadata},
    }


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
        edit = EditRequest.model_json_schema(
            ref_template="#/components/schemas/{model}"
        )
        schema["components"]["schemas"].update(edit.pop("$defs", {}))
        schema["components"]["schemas"]["EditRequest"] = edit
        field = schema["components"]["schemas"]["ImageRequest"]["properties"]["size"]
        field["enum"] = (
            ([None, "auto"] + [r["id"] for r in choices] + [r["size"] for r in choices])
            if choices
            else [None]
        )
        field["default"] = default
        schema["components"]["schemas"]["EditRequest"]["properties"]["size"] = dict(
            field
        )
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
            **({"adapter": options.adapter} if options.adapter else {}),
        )
        return await image_response(images, options.output_format)

    @app.post(
        "/v1/images/edits",
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/EditRequest"}
                    },
                    "multipart/form-data": {
                        "schema": {
                            "type": "object",
                            "anyOf": [
                                {"required": ["image"]},
                                {"required": ["image[]"]},
                            ],
                            "properties": {
                                "image": {"type": "string", "format": "binary"},
                                "image[]": {
                                    "type": "array",
                                    "minItems": 1,
                                    "maxItems": 4,
                                    "items": {"type": "string", "format": "binary"},
                                },
                                **ImageRequest.model_json_schema()["properties"],
                                "prompt": EditRequest.model_json_schema()["properties"][
                                    "prompt"
                                ],
                                "adapter": EditRequest.model_json_schema()[
                                    "properties"
                                ]["adapter"],
                                "zoom_region": EditRequest.model_json_schema(
                                    ref_template="#/components/schemas/{model}"
                                )["properties"]["zoom_region"],
                            },
                        }
                    },
                },
            }
        },
    )
    async def edit(request: Request):
        values, upload = await read_input(request)
        try:
            options = EditRequest.model_validate(values)
        except ValidationError as exc:
            error = exc.errors()[0]
            raise HTTPException(
                400, f"Invalid {'.'.join(map(str, error['loc']))}: {error['msg']}"
            ) from exc
        if options.image is not None and options.images is not None:
            raise HTTPException(400, "Send one reference image")
        image_data = upload if upload is not None else options.image
        if options.images is not None:
            if upload is not None:
                raise HTTPException(400, "Send one reference image")
            image_data = [item.image_url for item in options.images]
        if upload is not None and options.image is not None:
            raise HTTPException(400, "Send one reference image")
        identifiers, owned = await prepare_references(
            jobs,
            reference_ids=options.reference_ids,
            adapter=options.adapter,
            reference_id=options.reference_id,
            source_job_id=options.source_job_id,
            image_data=image_data,
            **({"preserve_green": True} if options.adapter == "outpaint" else {}),
            **(
                {"zoom_region": options.zoom_region}
                if options.zoom_region is not None
                else {}
            ),
        )
        try:
            images = await generate_images(
                jobs,
                options.prompt,
                resolve_size(options.size)
                if options.size not in (None, "auto")
                else None,
                seed=options.seed,
                output_format=options.output_format,
                save_gallery=options.save_gallery,
                count=options.n,
                disconnected=lambda: client_disconnected(request),
                **(
                    {"reference_id": identifiers[0]}
                    if len(identifiers) == 1
                    else {"reference_ids": identifiers}
                ),
                **({"adapter": options.adapter} if options.adapter else {}),
            )
            return await image_response(images, options.output_format)
        finally:
            for identifier in owned:
                jobs.references.delete(identifier)
