"""Protocol contracts, shared admission and cancellation without model weights."""

import asyncio
import base64
import hashlib
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
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
            {"n": 17},
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

    def test_resolution_policy_filters_discovery_and_rejects_cached_or_numeric_choices(
        self,
    ):
        self.client.put("/api/settings", json={"idle_seconds": 77})
        response = self.client.put(
            "/api/resolutions",
            json={"enabled_resolutions": ["portrait_medium", "square_small"]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["default"], "square_small")
        choices = self.client.get("/v1/images/resolutions").json()
        self.assertEqual(
            [row["id"] for row in choices["data"]], ["square_small", "portrait_medium"]
        )
        tools = self.rpc("tools/list").json()["result"]["tools"]
        schema = next(t for t in tools if t["name"] == "generate_image")["inputSchema"]
        self.assertEqual(
            schema["properties"]["resolution"]["enum"],
            ["square_small", "portrait_medium"],
        )
        self.assertEqual(
            schema["$defs"]["Resolution"]["enum"], ["square_small", "portrait_medium"]
        )
        advertised = self.client.get("/openapi.json").json()["components"]["schemas"][
            "ImageRequest"
        ]["properties"]["size"]
        self.assertNotIn("square_big", advertised["enum"])
        self.assertNotIn("1024x1024", advertised["enum"])
        self.assertIn(None, advertised["enum"])
        for value in ("square_big", "1024x1024"):
            response = self.client.post(
                "/v1/images/generations", json={"prompt": "test", "size": value}
            )
            self.assertEqual(response.status_code, 400)
            self.assertIn("disabled", response.json()["error"]["message"])
        result = self.rpc(
            "tools/call",
            {
                "name": "generate_image",
                "arguments": {"prompt": "test", "resolution": "square_big"},
            },
        ).json()["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(self.requests, [])
        first = self.client.post(
            "/v1/images/generations", json={"prompt": "test"}
        ).json()
        self.assertEqual(first["studio"]["resolution"], "square_small")
        result = self.rpc(
            "tools/call", {"name": "generate_image", "arguments": {"prompt": "test"}}
        ).json()["result"]
        self.assertEqual(result["structuredContent"]["resolution"], "square_small")
        restored = create_app(Path(self.directory.name))
        try:
            self.assertEqual(
                restored.state.jobs.resolutions.allowed(),
                ["square_small", "portrait_medium"],
            )
            self.assertEqual(restored.state.jobs.model.idle_seconds, 77)
        finally:
            restored.state.jobs.close()

    def test_all_disabled_hides_generation_and_reenabling_restores_it(self):
        self.client.put("/api/resolutions", json={"enabled_resolutions": []})
        names = {t["name"] for t in self.rpc("tools/list").json()["result"]["tools"]}
        self.assertEqual(names, {"get_status", "list_resolutions"})
        self.assertEqual(
            self.client.get("/v1/images/resolutions").json(),
            {"default": None, "data": []},
        )
        self.assertEqual(self.client.get("/health/ready").status_code, 503)
        self.assertEqual(self.client.get("/health/live").status_code, 200)
        self.assertEqual(
            self.client.post(
                "/v1/images/generations", json={"prompt": "test"}
            ).status_code,
            503,
        )
        stale = self.rpc(
            "tools/call",
            {
                "name": "generate_image",
                "arguments": {"prompt": "test", "resolution": "square_big"},
            },
        ).json()["result"]
        self.assertTrue(stale["isError"])
        self.assertEqual(self.requests, [])
        self.client.put(
            "/api/resolutions", json={"enabled_resolutions": ["square_medium"]}
        )
        names = {t["name"] for t in self.rpc("tools/list").json()["result"]["tools"]}
        self.assertIn("generate_image", names)
        self.assertEqual(self.client.get("/health/ready").status_code, 200)
        self.assertEqual(
            self.client.get("/v1/images/resolutions").json()["default"], "square_medium"
        )

    def test_resolution_save_validation_origin_and_io_failure_preserve_choices(self):
        for value in (["unknown"], ["square_big", "square_big"], "square_big"):
            self.assertEqual(
                self.client.put(
                    "/api/resolutions", json={"enabled_resolutions": value}
                ).status_code,
                422,
            )
        self.client.put(
            "/api/resolutions", json={"enabled_resolutions": ["square_small"]}
        )
        before = self.jobs.resolutions.path.read_text(encoding="utf-8")
        with patch("pathlib.Path.replace", side_effect=PermissionError("locked")):
            self.assertEqual(
                self.client.put(
                    "/api/resolutions", json={"enabled_resolutions": []}
                ).status_code,
                409,
            )
        self.assertEqual(self.jobs.resolutions.allowed(), ["square_small"])
        self.assertEqual(self.jobs.resolutions.path.read_text(encoding="utf-8"), before)
        self.assertEqual(
            self.client.put(
                "/api/resolutions",
                json={"enabled_resolutions": []},
                headers={"Origin": "https://elsewhere.example"},
            ).status_code,
            403,
        )

    def test_invalid_persisted_policy_does_not_silently_enable_everything(self):
        self.jobs.resolutions.path.write_text(
            '{"enabled_resolutions":["unknown"]}', encoding="utf-8"
        )
        with self.assertRaises(ValueError):
            create_app(Path(self.directory.name))

    def test_policy_changes_apply_to_new_requests_and_allow_admitted_work_to_finish(
        self,
    ):
        entered, release = threading.Event(), threading.Event()
        original = self.jobs.model.generate.side_effect

        def blocked(settings, phase, cancelled):
            entered.set()
            if not release.wait(3) or cancelled.is_set():
                raise RuntimeError("Accepted request was unexpectedly stopped")
            return original(settings, phase, cancelled)

        with (
            patch.object(self.jobs.model, "generate", side_effect=blocked),
            ThreadPoolExecutor() as pool,
        ):
            future = pool.submit(
                self.client.post,
                "/v1/images/generations",
                json={"prompt": "admitted", "size": "square_big"},
            )
            try:
                self.assertTrue(entered.wait(2))
                self.client.put(
                    "/api/resolutions", json={"enabled_resolutions": ["square_small"]}
                )
                self.assertFalse(self.jobs.cancel_event.is_set())
            finally:
                release.set()
            self.assertEqual(future.result(timeout=3).status_code, 200)
        self.assertEqual(
            self.client.post(
                "/v1/images/generations", json={"prompt": "new", "size": "square_big"}
            ).status_code,
            400,
        )

    def test_packaged_reference_covers_the_catalog_and_preserves_measurement_scope(
        self,
    ):
        data = self.client.get("/api/resolutions").json()
        self.assertEqual(data["reference"]["steps"], 4)
        self.assertIn("shared", data["reference"]["measurement_context"])
        self.assertEqual(len(data["resolutions"]), len(SIZES))
        for row in data["resolutions"]:
            self.assertEqual(
                (row["memory"]["width"], row["memory"]["height"]),
                SIZES[Resolution(row["id"])],
            )
            self.assertIsNotNone(row["memory"]["reserved_peak_bytes"])

    def test_queue_full_is_shared_with_browser_and_does_not_cancel_another_job(self):
        self.jobs.queue_limit = 1
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
            self.assertEqual(response.json()["error"]["code"], "queue_full")
            result = self.rpc(
                "tools/call",
                {"name": "generate_image", "arguments": {"prompt": "agent"}},
            ).json()["result"]
            self.assertTrue(result["isError"])
            self.assertIn("queue is full", result["content"][0]["text"])
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
