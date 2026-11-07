import tempfile
import os
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from flux2_klein_studio.adapters import compose_prompt
from flux2_klein_studio.server import create_app


class AdapterTests(unittest.TestCase):
    def test_offline_uncached_adapter_is_disabled_and_rejected_before_worker(self):
        with (
            patch.dict(os.environ, {"STUDIO_OFFLINE": "1"}),
            patch("huggingface_hub.try_to_load_from_cache", return_value=None),
        ):
            profile = self.client.get("/api/status").json()["adapters"][0]
            self.assertFalse(profile["available"])
            response = self.client.post(
                "/api/jobs", json={"prompt": "robot", "adapter": "pixel-art"}
            )
            self.assertEqual(response.status_code, 503)
            self.assertIn("STUDIO_OFFLINE=0", response.json()["detail"])
        self.assertEqual(self.calls, [])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = create_app(Path(self.directory.name))
        self.jobs = self.app.state.jobs
        self.calls = []

        def generate(settings, phase, cancelled):
            self.calls.append(dict(settings))
            image = Image.new(
                "RGB", (settings["width"], settings["height"]), (41, 92, 113)
            )
            return {
                "pixels": image.tobytes(),
                "size": image.size,
                "recipe": settings,
                "seconds": 0.01,
            }

        self.worker = patch.object(self.jobs.model, "generate", side_effect=generate)
        self.worker.start()
        self.client = TestClient(self.app).__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.worker.stop()
        self.directory.cleanup()

    def completed(self, identifier):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            item = self.client.get("/api/jobs/" + identifier).json()
            if item["status"] == "completed":
                return item
            time.sleep(0.01)
        self.fail("Job did not complete")

    def test_catalog_and_template_do_not_load_a_worker(self):
        with patch(
            "subprocess.Popen", side_effect=AssertionError("Unexpected model load")
        ):
            profile = self.client.get("/api/status").json()["adapters"][0]
        self.assertEqual(profile["id"], "pixel-art")
        self.assertEqual(profile["prefix"], "pixel art sprite, ")
        self.assertEqual(profile["template_version"], 2)
        self.assertEqual(
            profile["suffix"],
            ", game asset, solid green screen background, flat uniform green background, isolated character",
        )
        self.assertEqual(self.calls, [])

    def test_browser_job_preserves_description_and_replay_does_not_double_wrap(self):
        request = {
            "prompt": "An orange robot",
            "adapter": "pixel-art",
            "save_gallery": True,
        }
        first = self.client.post("/api/jobs", json=request)
        self.assertEqual(first.status_code, 202)
        item = self.completed(first.json()["id"])
        settings = item["settings"]
        self.assertEqual(settings["prompt"], request["prompt"])
        self.assertEqual(
            settings["effective_prompt"],
            "pixel art sprite, An orange robot, game asset, solid green screen background, flat uniform green background, isolated character",
        )
        second = self.client.post(
            "/api/jobs",
            json={"prompt": settings["prompt"], "adapter": settings["adapter"]},
        )
        replay = self.completed(second.json()["id"])
        self.assertEqual(
            replay["settings"]["effective_prompt"], settings["effective_prompt"]
        )
        self.assertEqual(self.calls[0]["adapter_profile"], settings["adapter_profile"])
        plain = self.client.post("/api/jobs", json={"prompt": request["prompt"]})
        plain = self.completed(plain.json()["id"])
        self.assertNotIn("adapter", plain["settings"])
        self.assertNotIn("effective_prompt", plain["settings"])

    def test_invalid_adapter_and_client_template_overrides_fail_before_generation(self):
        for extra in (
            {"adapter": "unknown"},
            {"adapter": "pixel-art", "effective_prompt": "override"},
            {"adapter": "pixel-art", "adapter_profile": {}},
            {"adapter": "pixel-art", "reference_id": "a" * 32},
        ):
            with self.subTest(extra=extra):
                self.assertEqual(
                    self.client.post(
                        "/api/jobs", json={"prompt": "robot", **extra}
                    ).status_code,
                    422,
                )
        self.assertEqual(self.calls, [])

    def test_http_batch_uses_same_server_template_and_edit_rejects_adapter(self):
        result = self.client.post(
            "/v1/images/generations",
            json={
                "prompt": "A knight",
                "adapter": "pixel-art",
                "n": 2,
                "size": "square_small",
            },
        )
        self.assertEqual(result.status_code, 200, result.text)
        for item in result.json()["studio"]["images"]:
            self.assertEqual(item["prompt"], "A knight")
            self.assertEqual(
                item["effective_prompt"], compose_prompt("A knight", "pixel-art")
            )
        count = len(self.calls)
        self.assertEqual(
            self.client.post(
                "/v1/images/edits",
                json={
                    "prompt": "change it",
                    "adapter": "pixel-art",
                    "source_job_id": "a" * 32,
                },
            ).status_code,
            400,
        )
        self.assertEqual(len(self.calls), count)

    def test_mcp_adapter_uses_same_server_template(self):
        response = self.client.post(
            "/mcp/",
            headers={"Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "generate_image",
                    "arguments": {
                        "prompt": "A blue slime",
                        "adapter": "pixel-art",
                        "resolution": "square_small",
                    },
                },
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        recipe = response.json()["result"]["structuredContent"]
        self.assertEqual(recipe["prompt"], "A blue slime")
        self.assertEqual(
            recipe["effective_prompt"], compose_prompt("A blue slime", "pixel-art")
        )
