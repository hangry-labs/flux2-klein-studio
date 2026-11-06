"""Stateless MCP over the same worker and named sizes as the Images API."""

import base64
import json
from typing import Annotated, Literal, Any

from fastapi import HTTPException
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, ImageContent, TextContent, ToolAnnotations
from pydantic import Field

from .generation_service import MODEL_ALIAS, generate_image as run_generation
from .presets import Resolution, catalog
from .transport import client_disconnected


def create_mcp_server(jobs):
    server = MCPServer(
        "flux2-klein-studio",
        version="0.1.0-dev",
        log_level="WARNING",
        subscriptions=False,
        instructions="Generate local images with generate_image. Choose a named resolution; "
        "square_big is the 1024-square default. list_resolutions describes all sizes. "
        "Omit seed for a random image; reuse the returned seed and settings to reproduce it. "
        "Results include inline image content and a recipe. Gallery storage is opt-in. "
        "Busy instances reject requests; retry or select another instance.",
    )
    read = ToolAnnotations(
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    )

    @server.tool(annotations=read, structured_output=True)
    async def list_resolutions() -> dict[str, Any]:
        """List exact named resolutions and aspect ratios; no GPU/model load."""
        return {"default": "square_big", "resolutions": catalog()}

    @server.tool(annotations=read, structured_output=True)
    async def get_status() -> dict[str, Any]:
        """Read whether this instance can accept a generation, without loading weights."""
        with jobs.lock:
            return {
                "model": MODEL_ALIAS,
                "accepting_requests": not jobs.closing and jobs.active is None,
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
        resolution: Resolution = Resolution.square_big,
        seed: Annotated[
            int | None,
            Field(
                ge=0,
                le=2**53 - 1,
                description="Omit for random; supply a returned seed to reproduce an image.",
            ),
        ] = None,
        output_format: Literal["png", "webp", "jpeg"] = "png",
        save_gallery: bool = False,
    ) -> CallToolResult:
        """Generate one image at a named size. Return image content and its exact recipe.

        Square: small512, medium768, big1024. Portrait3:4, landscape4:3,
        widescreen16:9 and panorama2:1 also have small/medium/big choices.
        Keep save_gallery=false unless the user wants persistent gallery storage.
        """
        try:
            data, metadata = await run_generation(
                jobs,
                prompt,
                resolution,
                seed,
                output_format,
                save_gallery,
                disconnected=lambda: client_disconnected(
                    context.request_context.request
                ),
            )
        except HTTPException as exc:
            raise ToolError(f"{exc.detail} (HTTP {exc.status_code})") from exc
        return CallToolResult(
            content=[
                TextContent(text=json.dumps(metadata)),
                ImageContent(
                    data=base64.b64encode(data).decode("ascii"),
                    mime_type=f"image/{output_format}",
                ),
            ],
            structured_content=metadata,
        )

    return server
