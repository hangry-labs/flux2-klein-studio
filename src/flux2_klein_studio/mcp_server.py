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
from .presets import Resolution
from .transport import client_disconnected
from . import __version__


class ResolutionServer(MCPServer):
    """Filter discovery through the public SDK hook; admission validates again."""

    async def list_tools(self):
        tools = await super().list_tools()
        with self.resolution_policy.lock:
            allowed = self.resolution_policy.allowed()
            default = self.resolution_policy.default()
        result = []
        for tool in tools:
            if tool.name == "generate_image":
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
        instructions="Use the currently advertised tools for local image generation. Choose an enabled named resolution; "
        "list_resolutions describes the current choices and default. "
        "Omit seed for a random image; reuse the returned seed and settings to reproduce it. "
        "Results include inline image content and a recipe. Gallery storage is opt-in. "
        "Busy instances reject requests; retry or select another instance.",
    )
    server.resolution_policy = jobs.resolutions
    read = ToolAnnotations(
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    )

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
                "accepting_requests": not jobs.closing
                and jobs.active is None
                and bool(jobs.resolutions.allowed()),
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
        save_gallery: bool = False,
    ) -> CallToolResult:
        """Generate one image at a named size. Return image content and its exact recipe.

        Choose an enabled resolution from list_resolutions, or omit it to use
        the current server default. Disabled presets are rejected before generation.
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
