"""Qualify 1024 edits, multi-image HTTP/MCP and iterative reuse; delete own jobs."""

import argparse
import asyncio
import base64
import json
import os

import httpx2
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from probe_agent_api import PROMPT, verify

INSTRUCTION = (
    "Keep the glass robot and orchids, but change the lighting to warm golden sunset."
)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:7860")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    key = os.getenv("STUDIO_API_KEY", "")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    owned = []
    async with httpx2.AsyncClient(headers=headers, timeout=1240) as http:

        async def request(method, path, **options):
            response = await http.request(method, base + path, **options)
            response.raise_for_status()
            return response.json()

        def collect(result):
            metadata = result["studio"].get("images", [result["studio"]])
            owned.extend(item["job_id"] for item in metadata)
            for image, recipe in zip(result["data"], metadata, strict=True):
                verify(base64.b64decode(image["b64_json"]), recipe)
            return metadata

        try:
            original = await request(
                "POST",
                "/v1/images/generations",
                json={"prompt": PROMPT, "size": "square_big", "seed": 12345},
            )
            source = collect(original)[0]
            reference = base64.b64decode(original["data"][0]["b64_json"])
            edited = await request(
                "POST",
                "/v1/images/edits",
                data={
                    "prompt": INSTRUCTION,
                    "size": "square_big",
                    "seed": "12345",
                    "n": "2",
                },
                files={"image": ("reference.png", reference, "image/png")},
            )
            left = collect(edited)
            async with (
                streamable_http_client(base + "/mcp/", http_client=http) as (
                    read,
                    write,
                ),
                ClientSession(read, write, read_timeout_seconds=1240) as session,
            ):
                await session.initialize()
                result = await session.call_tool(
                    "refine_image",
                    {
                        "prompt": INSTRUCTION,
                        "resolution": "square_big",
                        "seed": 12345,
                        "n": 2,
                        "image_base64": base64.b64encode(reference).decode("ascii"),
                    },
                )
                assert not result.is_error, result
                right = result.structured_content["images"]
                owned.extend(item["job_id"] for item in right)
                for image, recipe in zip(
                    [item for item in result.content if item.type == "image"],
                    right,
                    strict=True,
                ):
                    verify(base64.b64decode(image.data), recipe)
                assert [item["pixel_sha256"] for item in left] == [
                    item["pixel_sha256"] for item in right
                ]
            for item in left + right:
                assert item["reference"]["pixel_sha256"] == source["pixel_sha256"]
            again = await request(
                "POST",
                "/v1/images/edits",
                json={
                    "prompt": "Keep the robot and greenhouse, but change the orchids to blue.",
                    "source_job_id": left[0]["job_id"],
                    "size": "square_big",
                    "seed": 12345,
                },
            )
            final = collect(again)[0]
            assert final["reference"]["pixel_sha256"] == left[0]["pixel_sha256"]
            assert final["pixel_sha256"] != left[0]["pixel_sha256"]
            print(
                json.dumps(
                    dict(
                        status="passed",
                        images=len(owned),
                        text_pixel_sha256=source["pixel_sha256"],
                        edit_pixel_sha256=left[0]["pixel_sha256"],
                        iterative_pixel_sha256=final["pixel_sha256"],
                    )
                ),
                flush=True,
            )
        finally:
            for identifier in owned:
                await request("DELETE", "/api/jobs/" + identifier)


if __name__ == "__main__":
    asyncio.run(main())
