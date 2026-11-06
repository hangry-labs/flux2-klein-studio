"""Protocol contracts, shared admission and cancellation without model weights."""

import asyncio
import base64
import hashlib
import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin

from flux2_klein_studio.generation_service import generate_image
from flux2_klein_studio.presets import Resolution, SIZES, catalog
from flux2_klein_studio.server import Generation, create_app
from flux2_klein_studio.transport import DisconnectMonitor, client_disconnected


class AgentAPITests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = create_app(Path(self.directory.name))
        self.jobs = self.app.state.jobs
        self.client = TestClient(self.app).__enter__()
        self.requests = []

        def fake(settings, phase, cancelled):
            self.requests.append(settings)
            image = Image.new(
                "RGB", (settings["width"], settings["height"]), (27, 91, 143)
            )
            metadata = PngImagePlugin.PngInfo()
            metadata.add_text("generation", json.dumps(settings))
            data = BytesIO()
            image.save(data, format="PNG", pnginfo=metadata)
            return {
                "png": data.getvalue(),
                "seconds": 0.1,
                "pixel_sha256": hashlib.sha256(image.tobytes()).hexdigest(),
            }

        self.worker = patch.object(self.jobs.model, "generate", side_effect=fake)
        self.worker.start()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.worker.stop()
        self.directory.cleanup()

    def rpc(self, method, params=None, identifier=1):
        return self.client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": identifier,
                "method": method,
                "params": params or {},
            },
            headers={"Accept": "application/json, text/event-stream"},
        )

    def test_discovery_and_health_do_not_generate_or_load_weights(self):
        with patch("subprocess.Popen", side_effect=AssertionError("Unexpected worker")):
            self.assertEqual(self.client.get("/health/live").status_code, 200)
            self.assertTrue(
                self.client.get("/health/ready").json()["accepting_requests"]
            )
            self.assertEqual(
                self.client.get("/v1/models").json()["data"][0]["id"], "flux2-klein-4b"
            )
            entries = self.client.get("/v1/images/resolutions").json()["data"]
            self.assertEqual(entries, catalog())
            for entry in entries:
                self.assertEqual(entry["width"] % 16, 0)
                self.assertEqual(entry["height"] % 16, 0)
            initialized = self.rpc(
                "initialize",
                {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            )
            self.assertEqual(initialized.status_code, 200)
            self.assertNotIn("mcp-session-id", initialized.headers)
            tools = {
                t["name"]: t for t in self.rpc("tools/list").json()["result"]["tools"]
            }
            self.assertEqual(
                set(tools), {"get_status", "list_resolutions", "generate_image"}
            )
            schema = tools["generate_image"]["inputSchema"]
            self.assertEqual(
                set(schema["$defs"]["Resolution"]["enum"]),
                {x.value for x in Resolution},
            )
            self.assertNotIn("width", schema["properties"])
            self.assertNotIn("context", schema["properties"])
            self.assertFalse(tools["generate_image"]["annotations"]["readOnlyHint"])
            self.assertEqual(
                self.rpc("tools/call", {"name": "list_resolutions"}).json()["result"][
                    "structuredContent"
                ]["resolutions"],
                entries,
            )
        self.assertEqual(self.requests, [])

    def test_every_named_size_and_standard_size_returns_decodable_inline_image(self):
        for name, size in SIZES.items():
            with self.subTest(resolution=name):
                response = self.client.post(
                    "/v1/images/generations",
                    json={
                        "prompt": "An orange sailboat",
                        "size": name.value,
                        "seed": 12345,
                    },
                )
                self.assertEqual(response.status_code, 200, response.text)
                data = response.json()
                with Image.open(
                    BytesIO(base64.b64decode(data["data"][0]["b64_json"]))
                ) as image:
                    self.assertEqual(image.size, size)
                self.assertEqual(data["studio"]["seed"], 12345)
                self.assertFalse(data["studio"]["save_gallery"])
                self.assertEqual(self.requests[-1]["steps"], 4)
        self.assertEqual(self.client.get("/api/jobs").json(), [])
        standard = self.client.post(
            "/v1/images/generations", json={"prompt": "test", "size": "1536x1024"}
        )
        self.assertEqual(standard.json()["studio"]["resolution"], "photo_landscape")

    def test_formats_random_seeds_and_explicit_gallery_opt_in(self):
        for format in ("png", "webp", "jpeg"):
            data = self.client.post(
                "/v1/images/generations",
                json={
                    "prompt": "test",
                    "size": "square_small",
                    "output_format": format,
                    "save_gallery": format == "jpeg",
                },
            ).json()
            with Image.open(
                BytesIO(base64.b64decode(data["data"][0]["b64_json"]))
            ) as image:
                self.assertEqual(image.format.lower(), format)
        self.assertEqual(len(self.client.get("/api/jobs").json()), 1)
        self.assertEqual(len({r["seed"] for r in self.requests}), 3)

    def test_unsupported_requests_are_explicit_openai_errors_before_inference(self):
        for bad in (
            {"size": "999x999"},
            {"size": "portrait"},
            {"width": 512},
            {"n": 2},
            {"n": True},
            {"model": "gpt-image-1"},
            {"prompt": " "},
            {"quality": "hd"},
            {"response_format": "url"},
            {"seed": -1},
            {"save_gallery": "false"},
            {"stream": True},
        ):
            with self.subTest(bad=bad):
                response = self.client.post(
                    "/v1/images/generations", json={"prompt": "test", **bad}
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("message", response.json()["error"])
        self.assertEqual(self.requests, [])
        self.assertEqual(self.client.get("/v1/models/unknown").status_code, 404)

    def test_worker_failure_is_an_openai_error_and_releases_admission(self):
        with patch.object(
            self.jobs.model, "generate", side_effect=OSError("worker failed")
        ):
            response = self.client.post(
                "/v1/images/generations", json={"prompt": "test"}
            )
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["type"], "server_error")
        self.assertTrue(self.client.get("/health/ready").json()["accepting_requests"])

    def test_busy_is_shared_with_browser_and_does_not_cancel_another_job(self):
        with patch("threading.Thread.start"):
            item = self.jobs.submit(Generation(prompt="browser"))
            self.jobs.worker = None
        try:
            self.assertEqual(self.client.get("/health/ready").status_code, 503)
            response = self.client.post(
                "/v1/images/generations", json={"prompt": "agent"}
            )
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.headers["retry-after"], "1")
            result = self.rpc(
                "tools/call",
                {"name": "generate_image", "arguments": {"prompt": "agent"}},
            ).json()["result"]
            self.assertTrue(result["isError"])
            self.assertIn("busy", result["content"][0]["text"])
            self.assertEqual(self.jobs.active, item["id"])
            self.assertFalse(self.jobs.cancel_event.is_set())
        finally:
            self.jobs.active = None

    def test_mcp_wire_generation_includes_image_and_exact_recipe(self):
        result = self.rpc(
            "tools/call",
            {
                "name": "generate_image",
                "arguments": {
                    "prompt": "A lighthouse",
                    "resolution": "portrait_small",
                    "seed": 7,
                    "output_format": "webp",
                },
            },
        ).json()["result"]
        self.assertFalse(result.get("isError"), result)
        metadata = result["structuredContent"]
        self.assertEqual(metadata["seed"], 7)
        self.assertEqual(metadata["resolution"], "portrait_small")
        block = next(c for c in result["content"] if c["type"] == "image")
        self.assertEqual(block["mimeType"], "image/webp")
        with Image.open(BytesIO(base64.b64decode(block["data"]))) as image:
            self.assertEqual(image.size, (576, 768))
        self.assertEqual(self.client.get("/api/jobs").json(), [])
        invalid = self.rpc(
            "tools/call",
            {
                "name": "generate_image",
                "arguments": {"prompt": "test", "resolution": "512x512"},
            },
        ).json()["result"]
        self.assertTrue(invalid["isError"])
        self.assertEqual(len(self.requests), 1)

    def test_bearer_auth_and_host_origin_policy_cover_both_protocols(self):
        with patch.dict(
            "os.environ",
            {
                "STUDIO_API_KEY": "unit-test-only",
                "STUDIO_ALLOWED_HOSTS": "testserver,worker.test",
            },
        ):
            app = create_app(Path(self.directory.name) / "auth")
        with TestClient(app) as client:
            self.assertEqual(client.get("/health/ready").status_code, 200)
            for path in ("/v1/models", "/mcp/"):
                self.assertEqual(client.get(path).status_code, 401)
                response = client.get(path, headers={"Authorization": "Bearer wrong"})
                self.assertEqual(response.status_code, 401)
            headers = {"Authorization": "Bearer unit-test-only"}
            self.assertEqual(
                client.get(
                    "/v1/models", headers={**headers, "Host": "worker.test"}
                ).status_code,
                200,
            )
            self.assertEqual(
                client.get(
                    "/v1/models", headers={**headers, "Host": "foreign.test"}
                ).status_code,
                400,
            )
            self.assertEqual(
                client.post(
                    "/mcp/",
                    json={},
                    headers={**headers, "Origin": "https://foreign.test"},
                ).status_code,
                403,
            )
            response = client.post(
                "/mcp/",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={**headers, "Accept": "application/json, text/event-stream"},
            )
            self.assertEqual(response.status_code, 200)

    def test_cancelled_disconnected_and_timed_out_call_release_only_owned_job(self):
        def blocked(settings, phase, cancelled):
            if not cancelled.wait(5):
                raise AssertionError("Cancellation never reached the worker")
            raise RuntimeError("cancelled")

        async def scenario(mode):
            async def disconnected():
                return True

            task = asyncio.create_task(
                generate_image(
                    self.jobs,
                    "test",
                    Resolution.square_small,
                    disconnected=disconnected if mode == "disconnect" else None,
                )
            )
            if mode == "cancel":
                await asyncio.sleep(0.02)
                task.cancel()
            try:
                await task
                self.fail("Expected request cancellation")
            except asyncio.CancelledError:
                self.assertEqual(mode, "cancel")
            except HTTPException as exc:
                self.assertEqual(exc.status_code, 499 if mode == "disconnect" else 504)
            await asyncio.to_thread(self.jobs.worker.join, 2)
            self.assertIsNone(self.jobs.active)
            self.assertTrue(self.jobs.cancel_event.is_set())

        with patch.object(self.jobs.model, "generate", side_effect=blocked):
            for mode in ("cancel", "disconnect", "timeout"):
                with (
                    self.subTest(mode=mode),
                    patch(
                        "flux2_klein_studio.generation_service.GENERATION_TIMEOUT", 0.05
                    ),
                ):
                    asyncio.run(scenario(mode))


class DisconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_body_chunks_are_preserved_and_disconnect_is_visible_to_mounted_requests(
        self,
    ):
        from starlette.requests import Request

        incoming = asyncio.Queue()
        await incoming.put(
            {"type": "http.request", "body": b"first", "more_body": True}
        )
        await incoming.put(
            {"type": "http.request", "body": b"last", "more_body": False}
        )
        body_read = asyncio.Event()
        scope = {"type": "http"}

        async def app(scope, receive, send):
            self.assertEqual((await receive())["body"], b"first")
            self.assertEqual((await receive())["body"], b"last")
            request = Request(scope)
            self.assertFalse(await client_disconnected(request))
            body_read.set()
            for _ in range(100):
                if await client_disconnected(request):
                    self.assertEqual(await receive(), {"type": "http.disconnect"})
                    return
                await asyncio.sleep(0.001)
            self.fail("Mounted request did not observe peer disconnect")

        running = asyncio.create_task(DisconnectMonitor(app)(scope, incoming.get, None))
        await body_read.wait()
        await incoming.put({"type": "http.disconnect"})
        await running
        self.assertTrue(scope["state"]["studio_disconnected"])

    async def test_normal_response_leaves_no_disconnect_watcher_running(self):
        incoming = asyncio.Queue()
        await incoming.put({"type": "http.request", "body": b"", "more_body": False})
        before = asyncio.all_tasks()

        async def app(scope, receive, send):
            await receive()

        await DisconnectMonitor(app)({"type": "http"}, incoming.get, None)
        self.assertEqual(asyncio.all_tasks(), before)
