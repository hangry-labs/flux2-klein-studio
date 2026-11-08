"""Stateless MCP over the same worker and named sizes as the Images API."""

import base64
import json
from pathlib import Path
from typing import Annotated, Literal, Any

from fastapi import HTTPException
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, ImageContent, TextContent, ToolAnnotations
from pydantic import Field

from .generation_service import (
    MODEL_ALIAS,
    MAX_BATCH,
    generate_images as run_generation,
)
from .presets import Resolution
from .transport import client_disconnected
from . import __version__
from .reference_api import prepare_references
from .references import MAX_REQUEST_BYTES, ZoomRegion
from .adapters import Adapter, EditAdapter, catalog as adapter_catalog
from .build_info import offline_default

SKILL_URI = "flux2-klein-studio://skills/image-generation"
SKILL_PATH = "/skills/image-generation/SKILL.md"
SKILL_FILE = Path(__file__).with_name("skills") / "image-generation" / "SKILL.md"
SKILL_VERSION = "7"


class ResolutionServer(MCPServer):
    """Filter discovery through the public SDK hook; admission validates again."""

    async def list_tools(self):
        tools = await super().list_tools()
        with self.resolution_policy.lock:
            allowed = self.resolution_policy.allowed()
            default = self.resolution_policy.default()
        result = []
        for tool in tools:
            if tool.name in {"generate_image", "refine_image"}:
                if not allowed:
                    continue
                tool = tool.model_copy(deep=True)
                tool.input_schema["$defs"]["Resolution"]["enum"] = allowed
                tool.input_schema["properties"]["resolution"] = {
                    "type": "string",
                    "enum": allowed,
                    "default": default,
                    "description": "An enabled named resolution from System settings.",
                }
            result.append(tool)
        return result


def create_mcp_server(jobs):
    server = ResolutionServer(
        "flux2-klein-studio",
        version=__version__,
        log_level="WARNING",
        subscriptions=False,
        instructions="Call skill first if you have not read its guide in this conversation, before choosing an image tool or preparing its prompt. "
        "Once skill returns skill_loaded=true, continue; do not call it repeatedly unless switching servers or the guide version changes. "
        "No guide token is required. Use the currently advertised tools for local image generation. Choose an enabled named resolution; "
        "list_resolutions describes the current choices and default. "
        "For pixel-art character sprites, select adapter='pixel-art' in generate_image. "
        "For anime illustrations, select adapter='koni-anime'; the server adds anime_style without a fixed background suffix. "
        "For retro-futuristic space concept art, select adapter='nasa-concept'; the server adds NASA_CONCEPT with no fixed suffix. "
        "Supply only the image description in prompt: the server adds the selected adapter's trigger words and any fixed background suffix. "
        "Do not add the adapter template yourself. Omit adapter for ordinary base-model generation. "
        "Pixel Art outputs are RGB with a green background, not transparent; background removal is not provided. "
        "refine_image uses the base model by default. For green-area filling, select adapter='outpaint' with a reference painted pure #00FF00. Omit prompt for natural fill, or provide only optional fill details; the fixed instruction is added automatically. "
        "For a generated close-up, select adapter='zoom' and a zoom_region rectangle, or supply an image with a red outlined rectangle. Reuse each returned job_id as source_job_id with a fresh zoom_region to zoom further. "
        "Omit seed for a random image; reuse the returned seed and settings to reproduce it. "
        "Results include inline image content and a recipe. Gallery storage is opt-in. "
        "Concurrent requests wait in a shared queue; one image uses the GPU at a time. "
        "Use n for multiple images. Retry another instance only when the queue is full. "
        "Use refine_image with one to four ordered references and an edit instruction. For several views use images_base64 or reference_ids, and omit adapter; editing adapters still require one reference. Job/reference IDs are instance-local; "
        "use image_base64 when routing between replicas.",
    )
    server.resolution_policy = jobs.resolutions
    read = ToolAnnotations(
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    )

    @server.resource(SKILL_URI, mime_type="text/markdown")
    def image_generation_skill() -> str:
        """Image-generation guide. Call skill first for the guide and current choices."""
        return SKILL_FILE.read_text(encoding="utf-8")

    @server.tool(annotations=read, structured_output=True)
    async def skill() -> dict[str, Any]:
        """Read this skill first, once per conversation, before choosing an image tool.

        Covers Generate versus Refine, named sizes, Pixel Art and Koni Anime
        adapter prompts and backgrounds, seeds, queueing and recipes. A successful
        result means the skill is loaded: continue rather than retrieving again.
        No GPU/model load and no guide token required.
        """
        with jobs.lock:
            return {
                "skill_version": SKILL_VERSION,
                "skill_loaded": True,
                "skill": image_generation_skill(),
                "default_resolution": jobs.resolutions.default(),
                "resolutions": jobs.resolutions.catalog(),
                "adapters": adapter_catalog(offline_default(), mode="generate"),
                "edit_adapters": adapter_catalog(offline_default(), mode="edit"),
                "next_step": "The guide is loaded. Choose generate_image for a new image or refine_image with one to four references. Use the current enabled sizes and available adapter choices. Do not retrieve skill again for each call.",
            }

    @server.tool(annotations=read, structured_output=True)
    async def list_resolutions() -> dict[str, Any]:
        """List exact named resolutions and aspect ratios; no GPU/model load."""
        with jobs.lock:
            return {
                "default": jobs.resolutions.default(),
                "resolutions": jobs.resolutions.catalog(),
            }

    @server.tool(annotations=read, structured_output=True)
    async def get_status() -> dict[str, Any]:
        """Read whether this instance can accept a generation, without loading weights."""
        with jobs.lock:
            return {
                "model": MODEL_ALIAS,
                "accepting_requests": jobs.queue_status()["accepting_requests"]
                and bool(jobs.resolutions.allowed()),
                "queue": jobs.queue_status(),
                "default_resolution": jobs.resolutions.default(),
                "model_residency": jobs.model.snapshot(),
            }

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        )
    )
    async def generate_image(
        prompt: Annotated[
            str,
            Field(
                min_length=1,
                max_length=4000,
                description="Describe the image to generate.",
            ),
        ],
        context: Context,
        resolution: Resolution | None = None,
        seed: Annotated[
            int | None,
            Field(
                ge=0,
                le=2**53 - 1,
                description="Omit for random; supply a returned seed to reproduce an image.",
            ),
        ] = None,
        output_format: Literal["png", "webp", "jpeg"] = "png",
        adapter: Annotated[
            Adapter | None,
            Field(
                description="Select pixel-art for green-screen sprites, koni-anime for anime illustrations, or nasa-concept for retro-futuristic space concept art. Supply only the description; trigger words and any fixed suffix are added automatically. Omit for the base model."
            ),
        ] = None,
        save_gallery: bool = False,
        n: Annotated[
            int,
            Field(
                ge=1,
                le=MAX_BATCH,
                description="Number of images; generated sequentially. Fixed seeds increment per image.",
            ),
        ] = 1,
    ) -> CallToolResult:
        """Generate one image at a named size. Return image content and its exact recipe.

        Choose an enabled resolution from list_resolutions, or omit it to use
        the current server default. Disabled presets are rejected before generation.
        Read skill first if it has not been read in this conversation.
        For pixel-art sprites, set adapter='pixel-art' and provide only the subject
        description. The server adds the fixed sprite and green screen wording.
        For anime illustrations, set adapter='koni-anime'; anime_style is added
        automatically, with the scene and background supplied in your description.
        For retro space illustrations, set adapter='nasa-concept'; NASA_CONCEPT is
        added automatically. These are new illustrations, not historical NASA images.
        This produces RGB images; it does not remove the background or add alpha.
        Keep save_gallery=false unless the user wants persistent gallery storage.
        """
        try:
            images = await run_generation(
                jobs,
                prompt,
                resolution,
                seed,
                output_format,
                save_gallery,
                count=n,
                **({"adapter": adapter} if adapter else {}),
                disconnected=lambda: client_disconnected(
                    context.request_context.request
                ),
            )
        except HTTPException as exc:
            raise ToolError(f"{exc.detail} (HTTP {exc.status_code})") from exc
        metadata = [item[1] for item in images]
        recipe = metadata[0] if n == 1 else {"count": n, "images": metadata}
        return CallToolResult(
            content=[TextContent(text=json.dumps(recipe))]
            + [
                ImageContent(
                    data=base64.b64encode(data).decode("ascii"),
                    mime_type=f"image/{output_format}",
                )
                for data, _ in images
            ],
            structured_content=recipe,
        )

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        )
    )
    async def refine_image(
        context: Context,
        prompt: Annotated[
            str,
            Field(
                max_length=4000,
                description="Base editing: required change instruction. With outpaint or zoom: optional extra fill/focus details; omit for the adapter's default instruction. The fixed instruction is added automatically.",
            ),
        ] = "",
        resolution: Resolution | None = None,
        adapter: Annotated[
            EditAdapter | None,
            Field(
                description="Optional editing adapter: outpaint fills pure #00FF00 regions; zoom generates a close-up of a red rectangle. Use zoom_region to draw that rectangle on an unmarked source automatically. Fixed instructions are added by the server."
            ),
        ] = None,
        zoom_region: Annotated[
            ZoomRegion | None,
            Field(
                description="For zoom only: x/y/width/height fractions (0 to 1) within the source image. The server adds a red outlined rectangle. Reuse a result's job_id as source_job_id with a fresh rectangle to zoom again."
            ),
        ] = None,
        image_base64: Annotated[
            str | None,
            Field(
                max_length=MAX_REQUEST_BYTES,
                description="One image as base64 or a base64 data URL.",
            ),
        ] = None,
        source_job_id: Annotated[
            str | None,
            Field(
                pattern=r"^[a-f0-9]{32}$",
                description="Reuse a completed image from this instance.",
            ),
        ] = None,
        reference_id: Annotated[str | None, Field(pattern=r"^[a-f0-9]{32}$")] = None,
        reference_ids: Annotated[
            list[str] | None,
            Field(
                min_length=1,
                max_length=4,
                description="Ordered prepared reference IDs; choose this or one other image source. Base-model Refine supports up to four.",
            ),
        ] = None,
        images_base64: Annotated[
            list[str] | None,
            Field(
                min_length=1,
                max_length=4,
                description="One to four ordered base64 images of the subject or scene. Do not combine with other image source fields.",
            ),
        ] = None,
        seed: Annotated[int | None, Field(ge=0, le=2**53 - 1)] = None,
        output_format: Literal["png", "webp", "jpeg"] = "png",
        save_gallery: bool = False,
        n: Annotated[int, Field(ge=1, le=MAX_BATCH)] = 1,
    ) -> CallToolResult:
        """Refine with one to four ordered references. Choose exactly one source family.

        Omit adapter for ordinary base-model editing with a required prompt.
        Select adapter='outpaint' to fill #00FF00 areas, or adapter='zoom' for a
        generated close-up of a red outlined rectangle. Both editing adapters
        add their fixed instruction automatically; prompt is optional extra guidance.
        These are RGB reference markers, not pixel-preserving masks or faithful
        detail recovery. Strength controls are not exposed.
        Keep gallery saving off unless the user requests persistence.
        Read skill first if it has not been read in this conversation.
        For zoom, set adapter='zoom' and zoom_region or provide an already red-boxed
        reference. Reuse a returned job as the source with a new region to zoom again.
        """
        identifiers, owned = [], []
        try:
            if adapter is None and not prompt.strip():
                raise HTTPException(
                    400, "Describe what to change in the reference picture"
                )
            if zoom_region is not None and adapter != "zoom":
                raise HTTPException(
                    400, "zoom_region requires the zoom editing adapter"
                )
            if images_base64 is not None and image_base64 is not None:
                raise HTTPException(400, "Choose one image source family")
            identifiers, owned = await prepare_references(
                jobs,
                reference_ids=reference_ids,
                adapter=adapter,
                reference_id=reference_id,
                source_job_id=source_job_id,
                image_data=images_base64 if images_base64 is not None else image_base64,
                **({"preserve_green": True} if adapter == "outpaint" else {}),
                **({"zoom_region": zoom_region} if zoom_region is not None else {}),
            )
            images = await run_generation(
                jobs,
                prompt,
                resolution,
                seed=seed,
                output_format=output_format,
                save_gallery=save_gallery,
                count=n,
                **(
                    {"reference_id": identifiers[0]}
                    if len(identifiers) == 1
                    else {"reference_ids": identifiers}
                ),
                **({"adapter": adapter} if adapter else {}),
                disconnected=lambda: client_disconnected(
                    context.request_context.request
                ),
            )
            metadata = [item[1] for item in images]
            recipe = metadata[0] if n == 1 else {"count": n, "images": metadata}
            return CallToolResult(
                content=[TextContent(text=json.dumps(recipe))]
                + [
                    ImageContent(
                        data=base64.b64encode(data).decode("ascii"),
                        mime_type=f"image/{output_format}",
                    )
                    for data, _ in images
                ],
                structured_content=recipe,
            )
        except HTTPException as exc:
            raise ToolError(f"{exc.detail} (HTTP {exc.status_code})") from exc
        finally:
            for identifier in owned:
                jobs.references.delete(identifier)

    return server
