import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flux2_klein_studio.cli import dimension, resolutions
from flux2_klein_studio.inference import DeviceMemorySampler
from flux2_klein_studio.resolution_sweep import classify, run_sweep, summarize
from flux2_klein_studio.workloads import jobs, seed_checks, timing_summary
from tools.report_benchmarks import archive, portable
from tools.export_preset_memory import project
from flux2_klein_studio.presets import SIZES
from flux2_klein_studio.cli import MODEL_ID, MODEL_REVISION


class ResolutionTests(unittest.TestCase):
    def test_named_resolution_plan_uses_the_real_catalog(self):
        from flux2_klein_studio.cli import main

        with (
            patch(
                "sys.argv",
                [
                    "studio",
                    "resolution-sweep",
                    "--suite",
                    "memory",
                    "--named-resolutions",
                    "--plan",
                ],
            ),
            patch("flux2_klein_studio.resolution_sweep.run_sweep") as run,
        ):
            main()
        self.assertEqual(run.call_args.args[0].resolutions, list(SIZES.values()))

    def test_packaged_memory_projection_preserves_evidence_and_rejects_wrong_profiles(
        self,
    ):
        data = {
            "schema_version": 2,
            "suite": "memory",
            "status": "completed",
            "run_id": "20261106T000000Z",
            "environment": {
                "model": MODEL_ID,
                "revision": MODEL_REVISION,
                "allocator_config": "expandable_segments:True",
            },
            "reduction": "ordered",
            "steps": 4,
            "warmups": 2,
            "measured_calls_per_resolution": 5,
            "workload_sha256": "fixture",
            "method": "fresh worker",
            "measurement_context": "shared",
            "results": [
                {
                    "width": w,
                    "height": h,
                    "status": "passed",
                    "reserved_peak_bytes": 1234,
                }
                for w, h in SIZES.values()
            ],
        }
        output = project(data)
        self.assertEqual(len(output["results"]), 17)
        self.assertEqual(
            output["results"]["portrait_small"]["reserved_peak_bytes"], 1234
        )
        self.assertEqual(output["run_id"], data["run_id"])
        for change in (
            {"results": data["results"][:-1]},
            {"reduction": "native"},
            {"status": "running"},
        ):
            with self.subTest(change=next(iter(change))), self.assertRaises(ValueError):
                project({**data, **change})

    def test_rejects_silent_rounding_and_invalid_lists(self):
        for value in ("1080", "0", "-16", "32"):
            with (
                self.subTest(value=value),
                self.assertRaises(argparse.ArgumentTypeError),
            ):
                dimension(value)
        for value in ("1024x1080", "1024x1024,1024x1024", "1024x1024x1024", "1024x"):
            with (
                self.subTest(value=value),
                self.assertRaises(argparse.ArgumentTypeError),
            ):
                resolutions(value)
        self.assertEqual(
            resolutions("1920x1088,1088x1920"), [(1920, 1088), (1088, 1920)]
        )


class SweepTests(unittest.TestCase):
    def test_headroom_uses_free_memory_including_driver_reservations(self):
        sampler = DeviceMemorySampler()

        def sample(*_args, **_kwargs):
            sampler.stop_event.set()
            return SimpleNamespace(stdout="900, 16000, 14793")

        with patch("flux2_klein_studio.inference.subprocess.run", side_effect=sample):
            sampler._sample()
        self.assertEqual(sampler.peak_mib, 900)
        self.assertEqual(sampler.minimum_free_mib, 14793)
        self.assertNotEqual(
            sampler.minimum_free_mib, sampler.total_mib - sampler.peak_mib
        )

    def test_classifies_memory_failure_separately_from_other_errors(self):
        self.assertEqual(
            classify(1, {"error": "OutOfMemoryError: CUDA out of memory"}), "oom"
        )
        self.assertEqual(classify(-1, {}, timed_out=True), "timeout")
        self.assertEqual(classify(1, {"error": "Image equality failed"}), "failed")
        self.assertEqual(classify(0, {"status": "failed"}), "failed")

    def test_success_with_low_headroom_is_not_a_comfortable_fit(self):
        record = summarize(
            1792,
            1792,
            {"loading_device_memory": {"minimum_free_mib": 256}},
            "passed",
            512,
        )
        self.assertEqual(record["status"], "passed_low_headroom")

    def test_capacity_stops_after_oom_and_preserves_summary(self):
        calls = []

        def fake_child(command, **_kwargs):
            calls.append(command)
            destination = Path(command[command.index("--output-dir") + 1]) / "run"
            destination.mkdir()
            report = (
                {
                    "status": "passed",
                    "loading_device_memory": {"minimum_free_mib": 2048},
                }
                if len(calls) == 1
                else {"status": "failed", "error": "CUDA out of memory"}
            )
            (destination / "report.json").write_text(
                json.dumps(report), encoding="utf-8"
            )
            return SimpleNamespace(returncode=0 if len(calls) == 1 else 1)

        with tempfile.TemporaryDirectory() as folder:
            args = SimpleNamespace(
                output_dir=Path(folder),
                steps=4,
                seed=12345,
                prompt="test",
                reduction="ordered",
                warmups=2,
                runs=5,
                timeout=60,
                min_headroom_mib=512,
                stop_on_limit=True,
                offline=True,
                suite="memory",
                resolutions=[(1024, 1024), (2048, 2048), (2304, 2304)],
            )
            with (
                patch(
                    "flux2_klein_studio.resolution_sweep.subprocess.run",
                    side_effect=fake_child,
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                run_sweep(args)
            self.assertEqual(len(calls), 2)
            self.assertTrue(all("--offline" in command for command in calls))
            result = json.loads(next(Path(folder).rglob("summary.json")).read_text())
            self.assertEqual(
                [row["status"] for row in result["results"]], ["passed", "oom"]
            )
            self.assertEqual(result["status"], "completed")
            self.assertIn("2048x2048", result["stop_reason"])

    def test_memory_summary_keeps_loading_separate_and_excludes_warmups(self):
        report = {
            "loading_memory": {
                "allocated_peak_bytes": 900,
                "reserved_peak_bytes": 1000,
            },
            "loading_device_memory": {
                "baseline_mib": 100,
                "peak_mib": 500,
                "minimum_free_mib": 4000,
            },
            "runs": [
                {
                    "label": label,
                    "memory": {
                        "allocated_peak_bytes": allocated,
                        "reserved_peak_bytes": allocated + 10,
                    },
                    "device_memory": {"peak_mib": 600, "minimum_free_mib": 3000},
                }
                for label, allocated in (("warmup-1", 5000), ("measured-1", 2000))
            ],
        }
        row = summarize(1024, 1024, report, "passed", 512, "memory")
        self.assertEqual(row["allocated_peak_bytes"], 2000)
        self.assertEqual(row["loading_allocated_peak_bytes"], 900)
        self.assertEqual(row["generation_device_delta_mib"], 500)
        self.assertNotIn("median_seconds", row)

    def test_restart_mismatch_fails_seed_suite_and_retains_result(self):
        args = SimpleNamespace(
            suite="fixed-seed",
            steps=4,
            seed=12345,
            prompt="test",
            reduction="ordered",
            warmups=1,
            runs=2,
            timeout=60,
            min_headroom_mib=512,
            stop_on_limit=False,
            offline=True,
            resolutions=[(1024, 1024)],
        )
        with tempfile.TemporaryDirectory() as folder:
            args.output_dir = Path(folder)
            with (
                patch(
                    "flux2_klein_studio.resolution_sweep.child",
                    side_effect=[
                        (
                            {
                                "status": "passed",
                                "seed_checks": {"reference_pixel_sha256": "first"},
                            },
                            "passed",
                        ),
                        (
                            {
                                "status": "passed",
                                "runs": [{"pixel_sha256": "different"}],
                            },
                            "passed",
                        ),
                    ],
                ),
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                run_sweep(args)
            result = json.loads(next(Path(folder).rglob("summary.json")).read_text())
            self.assertEqual(result["status"], "failed")
            self.assertFalse(result["results"][0]["fresh_process_bit_exact"])


class WorkloadTests(unittest.TestCase):
    def test_speed_and_memory_use_matched_varied_inputs_without_seed_controls(self):
        from flux2_klein_studio.cli import DEFAULT_PROMPT

        args = SimpleNamespace(
            command="benchmark", prompt=DEFAULT_PROMPT, seed=12345, warmups=2, runs=5
        )
        speed = jobs(args)
        args.command = "memory"
        self.assertEqual(speed, jobs(args))
        measured = [r for r in speed if r["label"].startswith("measured-")]
        self.assertEqual(len({r["seed"] for r in measured}), 5)
        self.assertEqual(len({r["prompt"] for r in measured}), 3)
        self.assertEqual(len(speed), 7)

    def test_seed_checks_detect_drift_and_ignored_seed(self):
        args = SimpleNamespace(
            command="seed-test", prompt="primary", seed=12345, warmups=1, runs=3
        )
        records = [
            {**job, "pixel_sha256": str((job["prompt"], job["seed"]))}
            for job in jobs(args)
        ]
        good = seed_checks(records)
        self.assertTrue(
            all(value for key, value in good.items() if key != "reference_pixel_sha256")
        )
        records[2]["pixel_sha256"] = "drift"
        self.assertFalse(seed_checks(records)["same_seed_bit_exact"])
        for record in records:
            record["pixel_sha256"] = "ignored-input"
        self.assertFalse(seed_checks(records)["changed_seed_changes_pixels"])
        self.assertFalse(seed_checks(records)["changed_prompt_changes_pixels"])

    def test_timing_statistics_keep_throughput_and_percentile_meanings(self):
        summary = timing_summary([3, 1, 2])
        self.assertEqual(summary["median_seconds"], 2)
        self.assertAlmostEqual(summary["p95_seconds"], 2.9)
        self.assertEqual(summary["images_per_second"], 0.5)


class ReportingTests(unittest.TestCase):
    def test_public_projection_and_history_are_separate_and_immutable(self):
        data = {
            "schema_version": 2,
            "suite": "speed",
            "run_id": "20261105T000000Z",
            "status": "completed",
            "steps": 4,
            "warmups": 2,
            "measured_calls_per_resolution": 5,
            "workload_sha256": "test",
            "measurement_context": "shared live system",
            "environment": {"gpu": "test GPU", "private_path": "/private/cache"},
            "results": [
                {
                    "width": 1024,
                    "height": 1024,
                    "status": "passed",
                    "median_seconds": 1,
                    "allocated_peak_bytes": 9000,
                    "error": "problem in /private/cache",
                }
            ],
        }
        projected = portable(data)
        self.assertNotIn("/private/cache", json.dumps(projected))
        self.assertNotIn("allocated_peak_bytes", projected["results"][0])
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = archive(data, root)
            self.assertEqual(archive(data, root), path)
            data["results"][0]["median_seconds"] = 2
            with self.assertRaises(ValueError):
                archive(data, root)
            self.assertIn(
                "20261105T000000Z", (root / "speed/BENCHMARKS.md").read_text()
            )
            original = json.loads(path.read_text(encoding="utf-8"))
            data["run_id"] = "20261105T000001Z"
            archive(data, root)
            archive(
                {
                    **data,
                    "run_id": original["run_id"],
                    "results": [{**data["results"][0], "median_seconds": 1}],
                },
                root,
            )
            self.assertIn("20261105T000001Z", (root / "speed/DETAILS.md").read_text())
