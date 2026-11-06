"""Generate concurrent 1024 HTTP/MCP calls and batches; remove only own jobs."""

import argparse
import asyncio
import base64
import json
import os

import httpx2
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from probe_agent_api import PROMPT, verify


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:7860")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    key = os.getenv("STUDIO_API_KEY", "")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    timeout = float(os.getenv("STUDIO_QUEUE_TIMEOUT_SECONDS", "900")) + 340
    owned, results, tasks = [], [], []
    async with httpx2.AsyncClient(headers=headers, timeout=timeout) as http:

        async def request(method, path, **kwargs):
            response = await http.request(method, base + path, **kwargs)
            response.raise_for_status()
            return response.json()

        async def images(n=1):
            result = await request(
                "POST",
                "/v1/images/generations",
                json={"prompt": PROMPT, "size": "square_big", "seed": 12345, "n": n},
            )
            metadata = [result["studio"]] if n == 1 else result["studio"]["images"]
            owned.extend(item["job_id"] for item in metadata)
            assert len(result["data"]) == n
            for data, recipe in zip(result["data"], metadata, strict=True):
                verify(base64.b64decode(data["b64_json"]), recipe)
            results.extend(metadata)
            return metadata

        async with (
            streamable_http_client(base + "/mcp/", http_client=http) as (read, write),
            ClientSession(read, write, read_timeout_seconds=timeout) as session,
        ):
            await session.initialize()

            async def mcp(n=1):
                result = await session.call_tool(
                    "generate_image",
                    {
                        "prompt": PROMPT,
                        "resolution": "square_big",
                        "seed": 12345,
                        "n": n,
                    },
                )
                assert not result.is_error, result
                metadata = (
                    [result.structured_content]
                    if n == 1
                    else result.structured_content["images"]
                )
                owned.extend(item["job_id"] for item in metadata)
                blocks = [item for item in result.content if item.type == "image"]
                assert len(blocks) == n
                for data, recipe in zip(blocks, metadata, strict=True):
                    verify(base64.b64decode(data.data), recipe)
                results.extend(metadata)
                return metadata

            try:
                ready = await request("GET", "/health/ready")
                assert ready["queue"]["available_slots"] >= 5, (
                    "Need five available image slots"
                )
                maximum_queued, maximum_outstanding = 0, 0
                tasks = [
                    asyncio.create_task(call())
                    for call in (images, mcp, images, mcp, images)
                ]
                while not all(task.done() for task in tasks):
                    ready = await request("GET", "/health/ready")
                    maximum_queued = max(maximum_queued, ready["queue"]["queued"])
                    maximum_outstanding = max(
                        maximum_outstanding, ready["queue"]["outstanding"]
                    )
                    await asyncio.sleep(0.2)
                await asyncio.gather(*tasks)
                first = [item for item in results if item["seed"] == 12345]
                assert len(first) == 5
                assert len({item["pixel_sha256"] for item in first}) == 1
                assert maximum_outstanding >= 2, "Concurrent requests were not observed"
                left, right = await images(2), await mcp(2)
                assert [item["seed"] for item in left] == [12345, 12346]
                assert [item["pixel_sha256"] for item in left] == [
                    item["pixel_sha256"] for item in right
                ]
                print(
                    json.dumps(
                        dict(
                            status="passed",
                            images=len(results),
                            max_queued=maximum_queued,
                            max_outstanding=maximum_outstanding,
                            reference_pixel_sha256=first[0]["pixel_sha256"],
                            queue_seconds=[item["queue_seconds"] for item in results],
                        )
                    ),
                    flush=True,
                )
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                for identifier in owned:
                    await request("DELETE", "/api/jobs/" + identifier)


if __name__ == "__main__":
    asyncio.run(main())
