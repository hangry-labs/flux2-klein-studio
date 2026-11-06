"""Exercise a running service; generation is explicit and own test jobs are removed."""

import argparse
import asyncio
import base64
import hashlib
import json
import os
from io import BytesIO

import httpx2
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from PIL import Image


PROMPT = "A glass robot tending orchids in a sunlit greenhouse, cinematic lighting, highly detailed"


def verify(data, metadata):
    with Image.open(BytesIO(data)) as image:
        assert image.size == (metadata["width"], metadata["height"])
        assert (
            hashlib.sha256(image.convert("RGB").tobytes()).hexdigest()
            == metadata["pixel_sha256"]
        )
    print(
        json.dumps(
            {
                key: metadata[key]
                for key in ("resolution", "width", "height", "seed", "pixel_sha256")
            }
        ),
        flush=True,
    )


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:7860")
    parser.add_argument(
        "--generate",
        action="store_true",
        help="Run matching 1024 HTTP and MCP generations",
    )
    parser.add_argument(
        "--all-resolutions",
        action="store_true",
        help="Also generate each advertised HTTP resolution",
    )
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    key = os.getenv("STUDIO_API_KEY", "")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    async with httpx2.AsyncClient(headers=headers, timeout=330) as http:

        async def request(method, path, **kwargs):
            response = await http.request(method, base + path, **kwargs)
            response.raise_for_status()
            return response.json()

        ready = await request("GET", "/health/ready")
        models = await request("GET", "/v1/models")
        resolutions = await request("GET", "/v1/images/resolutions")
        print(
            json.dumps({"ready": ready, "models": models, "resolutions": resolutions}),
            flush=True,
        )
        async with (
            streamable_http_client(base + "/mcp/", http_client=http) as (
                read,
                write,
            ),
            ClientSession(read, write, read_timeout_seconds=330) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
            print(
                json.dumps({"mcp_tools": [tool.name for tool in tools.tools]}),
                flush=True,
            )
            if not (args.generate or args.all_resolutions):
                return
            metadata = None
            try:
                result = await request(
                    "POST",
                    "/v1/images/generations",
                    json={
                        "model": "flux2-klein-4b",
                        "prompt": PROMPT,
                        "size": "square_big",
                        "seed": 12345,
                    },
                )
                metadata = result["studio"]
                verify(base64.b64decode(result["data"][0]["b64_json"]), metadata)
                reference = metadata["pixel_sha256"]
            finally:
                if metadata:
                    await request("DELETE", "/api/jobs/" + metadata["job_id"])
            metadata = None
            try:
                result = await session.call_tool(
                    "generate_image",
                    {"prompt": PROMPT, "resolution": "square_big", "seed": 12345},
                )
                assert not result.is_error, result
                metadata = result.structured_content
                image = next(c for c in result.content if c.type == "image")
                verify(base64.b64decode(image.data), metadata)
                assert metadata["pixel_sha256"] == reference, (
                    "HTTP/MCP fixed-seed pixels differ"
                )
            finally:
                if metadata:
                    await request("DELETE", "/api/jobs/" + metadata["job_id"])
            if args.all_resolutions:
                for preset in resolutions["data"]:
                    metadata = None
                    try:
                        result = await request(
                            "POST",
                            "/v1/images/generations",
                            json={
                                "prompt": PROMPT,
                                "size": preset["id"],
                                "seed": 12345,
                            },
                        )
                        metadata = result["studio"]
                        verify(
                            base64.b64decode(result["data"][0]["b64_json"]),
                            metadata,
                        )
                    finally:
                        if metadata:
                            await request("DELETE", "/api/jobs/" + metadata["job_id"])


if __name__ == "__main__":
    asyncio.run(main())
