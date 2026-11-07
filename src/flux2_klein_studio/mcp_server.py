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
from .reference_api import prepare_reference
from .references import MAX_REQUEST_BYTES
from .adapters import Adapter, catalog as adapter_catalog
from .build_info import offline_default

SKILL_URI = "flux2-klein-studio://skills/image-generation"
SKILL_PATH = "/skills/image-generation/SKILL.md"
SKILL_FILE = Path(__file__).with_name("skills") / "image-generation" / "SKILL.md"
SKILL_VERSION = "2"


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
        "Supply only the image description in prompt: the server adds the selected adapter's trigger words and any fixed background suffix. "
        "Do not add the adapter template yourself. Omit adapter for ordinary base-model generation. "
        "Pixel Art outputs are RGB with a green background, not transparent; background removal is not provided. "
        "refine_image uses the base model and does not accept an adapter. "
        "Omit seed for a random image; reuse the returned seed and settings to reproduce it. "
        "Results include inline image content and a recipe. Gallery storage is opt-in. "
        "Concurrent requests wait in a shared queue; one image uses the GPU at a time. "
        "Use n for multiple images. Retry another instance only when the queue is full. "
        "Use refine_image with one reference and an edit instruction. Job/reference IDs are instance-local; "
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
                "adapters": adapter_catalog(offline_default()),
                "next_step": "The guide is loaded. Choose generate_image for a new image or refine_image to edit one reference. Use the current enabled sizes and available adapter choices. Do not retrieve skill again for each call.",
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
                description="Select pixel-art for sprites on a green screen, or koni-anime for anime illustrations with your own scene and background. Supply only the image description; adapter trigger words and any fixed suffix are added automatically. Omit for the base model."
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
        prompt: Annotated[
            str,
            Field(
                min_length=1,
                max_length=4000,
                description="Describe what to change in the reference picture.",
            ),
        ],
        context: Context,
        resolution: Resolution | None = None,
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
        seed: Annotated[int | None, Field(ge=0, le=2**53 - 1)] = None,
        output_format: Literal["png", "webp", "jpeg"] = "png",
        save_gallery: bool = False,
        n: Annotated[int, Field(ge=1, le=MAX_BATCH)] = 1,
    ) -> CallToolResult:
        """Refine one picture using an instruction. Supply exactly one image source.

        This is reference-guided generation: masks and strength controls are not
        supported. Keep gallery saving off unless the user requests persistence.
        Read skill first if it has not been read in this conversation.
        """
        identifier, owned = None, False
        try:
            identifier, owned = await prepare_reference(
                jobs,
                reference_id=reference_id,
                source_job_id=source_job_id,
                image_data=image_base64,
            )
            images = await run_generation(
                jobs,
                prompt,
                resolution,
                seed=seed,
                output_format=output_format,
                save_gallery=save_gallery,
                count=n,
                reference_id=identifier,
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
            if owned:
                jobs.references.delete(identifier)

    return server
