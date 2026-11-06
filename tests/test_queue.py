"""Concurrency tests use CPU pixel fixtures, with no model downloads or CUDA."""

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image

from flux2_klein_studio.generation_service import generate_image
from flux2_klein_studio.presets import Resolution
from flux2_klein_studio.schemas import Generation
from flux2_klein_studio.server import create_app


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = create_app(Path(self.directory.name))
        self.jobs = self.app.state.jobs
        self.client = TestClient(self.app).__enter__()
        self.calls = []
        self.worker = patch.object(self.jobs.model, "generate", side_effect=self.render)
        self.worker.start()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.worker.stop()
        self.directory.cleanup()

    def render(self, settings, phase, cancelled):
        self.calls.append(settings["seed"])
        image = Image.new(
            "RGB",
            (settings["width"], settings["height"]),
            (settings["seed"] % 256, 91, 143),
        )
        return dict(
            pixels=image.tobytes(), size=image.size, recipe=settings, seconds=0.01
        )

    def post(self, seed=1, **options):
        return self.client.post(
            "/v1/images/generations",
            json={
                "prompt": "queued fixture",
                "size": "square_small",
                "seed": seed,
                **options,
            },
        )

    def wait_for(self, condition, timeout=3):
        deadline = time.monotonic() + timeout
        while not condition():
            if time.monotonic() > deadline:
                self.fail("Timed out waiting for queue state")
            time.sleep(0.01)

    def test_five_simultaneous_requests_are_fifo_and_use_one_gpu_slot(self):
        entered, release = threading.Event(), threading.Event()
        active, maximum = 0, 0
        guard = threading.Lock()

        def render(settings, phase, cancelled):
            nonlocal active, maximum
            with guard:
                active += 1
                maximum = max(maximum, active)
            try:
                if settings["seed"] == 10:
                    entered.set()
                    if not release.wait(4):
                        raise RuntimeError("Fixture was not released")
                return self.render(settings, phase, cancelled)
            finally:
                with guard:
                    active -= 1

        with (
            patch.object(self.jobs.model, "generate", side_effect=render),
            ThreadPoolExecutor(max_workers=5) as pool,
        ):
            first = pool.submit(self.post, 10)
            try:
                self.assertTrue(entered.wait(2))
                others = [pool.submit(self.post, seed) for seed in range(11, 15)]
                self.wait_for(lambda: self.jobs.queue_status()["queued"] == 4)
                ready = self.client.get("/health/ready")
                self.assertEqual(ready.status_code, 200)
                self.assertEqual(ready.json()["queue"]["outstanding"], 5)
                expected = [
                    item["settings"]["seed"] for item in self.jobs.items.values()
                ]
            finally:
                release.set()
            for future in [first, *others]:
                self.assertEqual(future.result(timeout=4).status_code, 200)
        self.assertEqual(self.calls, expected)
        self.assertEqual(maximum, 1)
        self.assertEqual(self.jobs.queue_status()["outstanding"], 0)

    def test_http_batch_returns_each_seed_image_and_recipe(self):
        response = self.post(12345, n=3, output_format="webp")
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(len(data["data"]), 3)
        self.assertEqual(self.calls, [12345, 12346, 12347])
        self.assertEqual(
            [item["seed"] for item in data["studio"]["images"]], self.calls
        )
        for index, entry in enumerate(data["data"]):
            with Image.open(BytesIO(base64.b64decode(entry["b64_json"]))) as image:
                self.assertEqual(image.size, (512, 512))
                self.assertEqual(image.getpixel((0, 0))[0], (12345 + index) % 256)
        self.assertEqual(self.jobs.protected, set())

    def test_mcp_batch_returns_multiple_native_images(self):
        response = self.client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "generate_image",
                    "arguments": {
                        "prompt": "MCP queue",
                        "resolution": "square_small",
                        "seed": 5,
                        "n": 2,
                    },
                },
            },
            headers={"Accept": "application/json, text/event-stream"},
        )
        result = response.json()["result"]
        self.assertFalse(result.get("isError"), result)
        self.assertEqual(
            len([item for item in result["content"] if item["type"] == "image"]), 2
        )
        self.assertEqual(
            [item["seed"] for item in result["structuredContent"]["images"]], [5, 6]
        )

    def test_next_generation_runs_while_previous_cpu_output_is_blocked(self):
        finishing, release = threading.Event(), threading.Event()
        original = self.jobs.finish

        def finish(identifier, result):
            if self.jobs.items[identifier]["settings"]["seed"] == 1:
                finishing.set()
                release.wait(4)
            return original(identifier, result)

        with (
            patch.object(self.jobs, "finish", side_effect=finish),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            first = pool.submit(self.post, 1)
            try:
                self.assertTrue(finishing.wait(2))
                second = pool.submit(self.post, 2)
                self.assertEqual(second.result(timeout=2).status_code, 200)
                self.assertFalse(first.done())
                self.assertEqual(self.calls, [1, 2])
            finally:
                release.set()
            self.assertEqual(first.result(timeout=2).status_code, 200)

    def test_full_queue_rejects_whole_batch_without_partial_admission(self):
        self.jobs.queue_limit = 2
        with patch("threading.Thread.start"):
            item = self.jobs.submit(Generation(prompt="browser", width=512, height=512))
            self.jobs.worker = None
        before = set(self.jobs.items)
        response = self.post(n=2)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(set(self.jobs.items), before)
        self.assertEqual(self.jobs.active, item["id"])
        self.assertFalse(self.jobs.events[item["id"]].is_set())
        self.assertEqual(self.jobs.protected, set())

    def test_cancelled_or_expired_waiter_does_not_unload_active_image(self):
        entered, release = threading.Event(), threading.Event()

        def blocked(settings, phase, cancelled):
            entered.set()
            release.wait(4)
            return self.render(settings, phase, cancelled)

        async def scenario(cancel):
            task = asyncio.create_task(
                generate_image(self.jobs, "waiting", Resolution.square_small)
            )
            while not self.jobs.pending:
                await asyncio.sleep(0.005)
            if cancel:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            else:
                with self.assertRaises(HTTPException) as error:
                    await task
                self.assertEqual(error.exception.status_code, 504)
            for _ in range(100):
                if not self.jobs.pending:
                    break
                await asyncio.sleep(0.005)

        with (
            patch.object(self.jobs.model, "generate", side_effect=blocked),
            patch.object(self.jobs.model, "unload") as unload,
        ):
            active = self.jobs.submit(
                Generation(prompt="active", width=512, height=512)
            )
            try:
                self.assertTrue(entered.wait(2))
                with patch("flux2_klein_studio.generation_service.QUEUE_TIMEOUT", 0.05):
                    for cancel in (True, False):
                        asyncio.run(scenario(cancel))
                        self.assertEqual(len(self.jobs.pending), 0)
                        self.assertEqual(self.jobs.active, active["id"])
                        self.assertFalse(self.jobs.events[active["id"]].is_set())
                        unload.assert_not_called()
            finally:
                release.set()
                self.jobs.worker.join(timeout=3)

    def test_generation_deadline_starts_after_queue_wait(self):
        entered, release = threading.Event(), threading.Event()

        def blocked(settings, phase, cancelled):
            if settings["prompt"] == "occupier":
                entered.set()
                release.wait(4)
            return self.render(settings, phase, cancelled)

        with patch.object(self.jobs.model, "generate", side_effect=blocked):
            self.jobs.submit(Generation(prompt="occupier", width=512, height=512))
            self.assertTrue(entered.wait(2))
            timer = threading.Timer(0.4, release.set)
            timer.start()
            try:
                with patch(
                    "flux2_klein_studio.generation_service.GENERATION_TIMEOUT", 0.15
                ):
                    self.assertEqual(self.post(2).status_code, 200)
            finally:
                release.set()
                timer.join(timeout=2)

    def test_cpu_backlog_bounds_raw_buffers_before_more_gpu_work(self):
        release = threading.Event()
        original = self.jobs.finish

        def finish(identifier, result):
            release.wait(4)
            return original(identifier, result)

        with (
            patch.object(self.jobs, "finish", side_effect=finish),
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            request = pool.submit(self.post, 1, n=5)
            try:
                self.wait_for(lambda: len(self.calls) == 4)
                self.assertEqual(self.jobs.queue_status()["processing"], 4)
                self.assertEqual(self.calls, [1, 2, 3, 4])
            finally:
                release.set()
            self.assertEqual(request.result(timeout=3).status_code, 200)
        self.assertEqual(self.calls, [1, 2, 3, 4, 5])

    def test_worker_start_failure_clears_admission_and_owned_pins(self):
        original = threading.Thread.start

        def start(thread):
            if thread.name == "klein-gpu":
                raise RuntimeError("cannot start")
            return original(thread)

        with patch("threading.Thread.start", new=start):
            response = self.post(1, n=2)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.jobs.queue_status()["outstanding"], 0)
        self.assertEqual(self.jobs.protected, set())
        self.assertIsNone(self.jobs.active)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.post(3).status_code, 200)

    def test_worker_failure_does_not_stop_the_next_call(self):
        entered, release = threading.Event(), threading.Event()

        def render(settings, phase, cancelled):
            if settings["seed"] == 1:
                entered.set()
                release.wait(4)
                raise RuntimeError("fixture failure")
            return self.render(settings, phase, cancelled)

        with (
            patch.object(self.jobs.model, "generate", side_effect=render),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            first = pool.submit(self.post, 1)
            try:
                self.assertTrue(entered.wait(2))
                second = pool.submit(self.post, 2)
                self.wait_for(lambda: len(self.jobs.pending) == 1)
            finally:
                release.set()
            self.assertEqual(first.result(timeout=3).status_code, 502)
            self.assertEqual(second.result(timeout=3).status_code, 200)
        self.assertEqual(self.jobs.protected, set())

    def test_locked_old_preview_does_not_fail_a_new_admission_or_response(self):
        for index in range(100):
            identifier = f"{index:032x}"
            self.jobs.items[identifier] = dict(
                id=identifier,
                created=index,
                status="completed",
                settings={"save_gallery": False},
            )
        with patch.object(
            self.jobs, "delete", side_effect=HTTPException(409, "locked preview")
        ):
            response = self.post(7)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.jobs.protected, set())


if __name__ == "__main__":
    unittest.main()
