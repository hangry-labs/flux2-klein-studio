"""Exercise the real CLI inference loop with a CPU fake pipeline and telemetry."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import torch
from PIL import Image

from flux2_klein_studio.inference import run


class Generator:
    def __init__(self, _device):
        self.seed = None

    def manual_seed(self, seed):
        self.seed = seed
        return self


class Sampler:
    samples = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def summary(self):
        return {"baseline_mib": 100, "peak_mib": 300, "minimum_free_mib": 4000}


class ExecutionTests(unittest.TestCase):
    def execute(self, mode):
        calls = []

        def pipe(**options):
            calls.append(options)
            offset = (options["generator"].seed + len(options["prompt"])) % 256
            pixels = bytes((i + offset) % 256 for i in range(64 * 64 * 3))
            return SimpleNamespace(images=[Image.frombytes("RGB", (64, 64), pixels)])

        with tempfile.TemporaryDirectory() as folder:
            arguments = SimpleNamespace(
                command=mode,
                offline=True,
                output_dir=Path(folder),
                width=64,
                height=64,
                seed=12345,
                steps=4,
                prompt="primary",
                reduction="ordered",
                warmups=1,
                runs=3,
                save_images=False,
            )
            memory_suite = mode == "memory"
            with contextlib.ExitStack() as stack:
                stack.enter_context(
                    patch("flux2_klein_studio.inference.doctor", return_value={})
                )
                stack.enter_context(
                    patch(
                        "flux2_klein_studio.inference.load_pipeline", return_value=pipe
                    )
                )
                stack.enter_context(
                    patch(
                        "flux2_klein_studio.inference.check_residency", return_value={}
                    )
                )
                stack.enter_context(
                    patch(
                        "flux2_klein_studio.inference.memory",
                        return_value={
                            "allocated_peak_bytes": 100,
                            "reserved_peak_bytes": 200,
                        },
                    )
                )
                stack.enter_context(
                    patch(
                        "flux2_klein_studio.inference.DeviceMemorySampler",
                        side_effect=Sampler
                        if memory_suite
                        else AssertionError("Polling outside memory suite"),
                    )
                )
                stack.enter_context(patch.object(torch.cuda, "synchronize"))
                stack.enter_context(
                    patch.object(
                        torch.cuda,
                        "reset_peak_memory_stats",
                        side_effect=None
                        if memory_suite
                        else AssertionError("Peak reset outside memory suite"),
                    )
                )
                stack.enter_context(patch.object(torch, "Generator", Generator))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                run(arguments)
            report = json.loads(next(Path(folder).rglob("report.json")).read_text())
            images = list(Path(folder).rglob("*.png"))
            return report, calls, len(images)

    def test_speed_has_timing_without_memory_polling_controls_or_pngs(self):
        report, calls, images = self.execute("benchmark")
        self.assertEqual(report["status"], "passed")
        self.assertEqual(len(calls), 4)
        self.assertIn("timing", report)
        self.assertNotIn("loading_memory", report)
        self.assertNotIn("seed_checks", report)
        self.assertTrue(all("callback_on_step_end" in call for call in calls))
        self.assertEqual(images, 0)

    def test_memory_has_stage_peaks_without_timing_or_seed_assertions(self):
        report, _calls, images = self.execute("memory")
        self.assertIn("loading_memory", report)
        self.assertTrue(
            all("memory" in row and "seconds" not in row for row in report["runs"])
        )
        self.assertNotIn("timing", report)
        self.assertNotIn("seed_checks", report)
        self.assertEqual(images, 0)

    def test_seed_loop_runs_controls_and_saves_images_without_performance_claims(self):
        report, calls, images = self.execute("seed-test")
        self.assertEqual(report["status"], "passed")
        self.assertTrue(report["seed_checks"]["changed_seed_changes_pixels"])
        self.assertNotIn("timing", report)
        self.assertNotIn("loading_memory", report)
        self.assertEqual(images, len(calls))
