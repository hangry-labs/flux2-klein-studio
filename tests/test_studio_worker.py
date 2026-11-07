"""Exercise the resident loop with CPU pixels and a fake CUDA allocator."""

import copy
from contextlib import nullcontext
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from flux2_klein_studio.studio_worker import serve


class WorkspaceTests(unittest.TestCase):
    def run_worker(self, commands, polls=()):
        trace, replies = [], []
        memory = {"allocated": 10, "reserved": 10}

        def empty_cache():
            trace.append(("trim", memory["reserved"]))
            memory["reserved"] = memory["allocated"]

        cuda = SimpleNamespace(
            synchronize=lambda: None,
            empty_cache=empty_cache,
            memory_allocated=lambda: memory["allocated"],
            memory_reserved=lambda: memory["reserved"],
        )
        torch = SimpleNamespace(cuda=cuda, inference_mode=nullcontext, Generator=Mock())

        class Pipeline:
            def __call__(self, **settings):
                width, height = settings["width"], settings["height"]
                trace.append(("generate", width, height, memory["reserved"]))
                memory["reserved"] = max(memory["reserved"], width * height)
                image = (
                    Image.linear_gradient("L").resize((width, height)).convert("RGB")
                )
                return SimpleNamespace(images=[image])

        commands, polls = iter(commands), iter(polls)

        def receive():
            try:
                return next(commands)
            except StopIteration:
                raise EOFError from None

        connection = Mock()
        connection.recv.side_effect = receive
        connection.poll.side_effect = lambda timeout: next(polls, True)
        connection.send.side_effect = lambda event: replies.append(copy.deepcopy(event))
        with (
            patch.dict(sys.modules, {"torch": torch}),
            patch("flux2_klein_studio.cli.doctor", return_value={}),
            patch(
                "flux2_klein_studio.inference.load_pipeline", return_value=Pipeline()
            ) as load,
            patch("flux2_klein_studio.inference.check_residency", return_value={}),
        ):
            serve(connection)
        self.assertEqual(load.call_count, 1)
        self.assertFalse(any(event["phase"] == "failed" for event in replies))
        return trace, [event for event in replies if event["phase"] == "completed"]

    def job(self, size):
        return {
            "prompt": "an orchid",
            "width": size,
            "height": size,
            "steps": 4,
            "seed": 12345,
        }

    def test_continuous_same_size_work_reuses_cached_buffers(self):
        trace, replies = self.run_worker([self.job(128), self.job(128)])
        self.assertEqual(
            trace, [("generate", 128, 128, 10), ("generate", 128, 128, 16384)]
        )
        self.assertEqual(len(replies), 2)

    def test_downsizing_releases_large_buffers_before_next_generation(self):
        trace, replies = self.run_worker([self.job(128), self.job(64)])
        self.assertEqual(
            trace,
            [("generate", 128, 128, 10), ("trim", 16384), ("generate", 64, 64, 10)],
        )
        trimmed = replies[-1]["report"]["last_workspace_trim"]
        self.assertEqual(trimmed["reason"], "smaller-workload")
        self.assertEqual(trimmed["after"]["allocated_bytes"], 10)
        self.assertEqual(
            replies[-1]["report"]["allocator_after_generation"]["reserved_bytes"], 4096
        )

    def test_idle_release_keeps_pipeline_ready_and_only_trims_once(self):
        trace, replies = self.run_worker([self.job(128), self.job(128)], polls=[False])
        self.assertEqual(
            trace,
            [("generate", 128, 128, 10), ("trim", 16384), ("generate", 128, 128, 10)],
        )
        self.assertEqual(replies[-1]["report"]["last_workspace_trim"]["reason"], "idle")

    def test_manual_release_reuses_loaded_pipeline_without_image_options(self):
        trace, replies = self.run_worker(
            [self.job(128), {"_operation": "trim"}, self.job(64)]
        )
        self.assertEqual(
            trace,
            [("generate", 128, 128, 10), ("trim", 16384), ("generate", 64, 64, 10)],
        )
        self.assertEqual(replies[1]["report"]["last_workspace_trim"]["reason"], "trim")
