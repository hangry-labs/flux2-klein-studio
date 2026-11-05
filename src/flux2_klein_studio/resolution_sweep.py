"""Sequential, isolated memory, speed and fixed-seed benchmark suites."""

import csv
import json
import subprocess
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

from .workloads import SUITE_COMMANDS, fingerprint, jobs


def classify(returncode, report, timed_out=False):
    if timed_out:
        return "timeout"
    if returncode == 0 and report.get("status") == "passed":
        return "passed"
    error = report.get("error", "").lower()
    return "oom" if "out of memory" in error or "outofmemory" in error else "failed"


def summarize(width, height, report, status, minimum_headroom, suite="memory"):
    row = {
        "width": width,
        "height": height,
        "megapixels": width * height / 1_000_000,
        "status": status,
        "completed_calls": len(report.get("runs", [])),
        "error": report.get("error"),
    }
    if suite == "speed":
        row.update(report.get("timing", {}))
        first = report.get("runs", [])
        row["first_call_seconds"] = first[0]["seconds"] if first else None
        row["loading_seconds"] = report.get("loading_seconds")
    elif suite == "fixed-seed":
        row.update(report.get("seed_checks", {}))
    else:
        measured = [
            r for r in report.get("runs", []) if r["label"].startswith("measured-")
        ]
        load = report.get("loading_device_memory", {})
        devices = [r["device_memory"] for r in measured]
        all_devices = [load] + [r["device_memory"] for r in report.get("runs", [])]
        failure_samples = report.get("failure_device_samples", [])
        free = [
            v["minimum_free_mib"]
            for v in all_devices
            if v.get("minimum_free_mib") is not None
        ]
        free += [v["free_mib"] for v in failure_samples]
        headroom = min(free, default=None)
        row.update(
            {
                "loading_allocated_peak_bytes": report.get("loading_memory", {}).get(
                    "allocated_peak_bytes"
                ),
                "loading_reserved_peak_bytes": report.get("loading_memory", {}).get(
                    "reserved_peak_bytes"
                ),
                "allocated_peak_bytes": max(
                    (r["memory"]["allocated_peak_bytes"] for r in measured),
                    default=None,
                ),
                "reserved_peak_bytes": max(
                    (r["memory"]["reserved_peak_bytes"] for r in measured), default=None
                ),
                "baseline_device_mib": load.get("baseline_mib"),
                "loading_device_peak_mib": load.get("peak_mib"),
                "generation_device_peak_mib": max(
                    (d["peak_mib"] for d in devices), default=None
                ),
                "sampled_device_headroom_mib": headroom,
                "vae_tiling": report.get("vae_tiling"),
                "vae_slicing": report.get("vae_slicing"),
                "failure_allocated_peak_bytes": report.get("failure_memory", {}).get(
                    "allocated_peak_bytes"
                ),
            }
        )
        baseline = row["baseline_device_mib"]
        for stage in ("loading", "generation"):
            peak = row[f"{stage}_device_peak_mib"]
            row[f"{stage}_device_delta_mib"] = (
                peak - baseline if peak is not None and baseline is not None else None
            )
        if status == "passed" and headroom is not None and headroom < minimum_headroom:
            row["status"] = "passed_low_headroom"
        if status == "passed" and headroom is None:
            row["status"] = "failed"
            row["error"] = "Memory suite completed without valid GPU telemetry"
    return row


def save(root, summary):
    temporary = root / "summary.json.tmp"
    temporary.write_text(json.dumps(summary, indent=2) + chr(10), encoding="utf-8")
    temporary.replace(root / "summary.json")
    if summary["results"]:
        fields = list(dict.fromkeys(key for row in summary["results"] for key in row))
        with (root / "summary.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(summary["results"])


def child(command, case, timeout):
    case.mkdir(parents=True, exist_ok=True)
    timed_out = False
    with (
        (case / "stdout.log").open("w", encoding="utf-8") as stdout,
        (case / "stderr.log").open("w", encoding="utf-8") as stderr,
    ):
        try:
            process = subprocess.run(
                command, stdout=stdout, stderr=stderr, timeout=timeout, check=False
            )
            returncode = process.returncode
        except subprocess.TimeoutExpired:
            returncode, timed_out = -1, True
    paths = sorted(case.glob("*/report.json"))
    report = json.loads(paths[-1].read_text(encoding="utf-8")) if paths else {}
    if not report.get("error") and (returncode or not paths):
        report["error"] = (
            "Case exceeded its time limit"
            if timed_out
            else "Child failed; inspect the case logs"
        )
    return report, classify(returncode, report, timed_out)


def command_for(arguments, width, height, case, *, restart=False):
    command = [
        sys.executable,
        "-m",
        "flux2_klein_studio",
        "generate" if restart else SUITE_COMMANDS[arguments.suite],
        "--width",
        str(width),
        "--height",
        str(height),
        "--prompt",
        arguments.prompt,
        "--seed",
        str(arguments.seed),
        "--steps",
        str(arguments.steps),
        "--reduction",
        arguments.reduction,
        "--output-dir",
        str(case),
    ]
    if not restart:
        command += ["--runs", str(arguments.runs), "--warmups", str(arguments.warmups)]
    if arguments.offline:
        command.append("--offline")
    if getattr(arguments, "save_images", False):
        command.append("--save-images")
    return command


def plan(arguments):
    options = SimpleNamespace(**vars(arguments))
    options.command = SUITE_COMMANDS[arguments.suite]
    schedule = jobs(options)
    return {
        "suite": arguments.suite,
        "steps": arguments.steps,
        "seed": arguments.seed,
        "prompt": arguments.prompt,
        "reduction": arguments.reduction,
        "warmups": arguments.warmups,
        "measured_calls_per_resolution": arguments.runs,
        "timeout_seconds_per_process": arguments.timeout,
        "minimum_sampled_headroom_mib": arguments.min_headroom_mib,
        "stop_on_limit": arguments.stop_on_limit,
        "workload": schedule,
        "workload_sha256": fingerprint(schedule),
        "resolutions": arguments.resolutions,
        "restart_calls_per_resolution": int(arguments.suite == "fixed-seed"),
        "measurement_context": "shared live system; other workloads left running",
    }


def run_sweep(arguments):
    summary = plan(arguments)
    if getattr(arguments, "plan", False):
        print(json.dumps(summary, indent=2))
        return
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    root = arguments.output_dir / "benchmarks" / f"{arguments.suite}-{run_id}"
    root.mkdir(parents=True, exist_ok=False)
    summary.update(
        {
            "schema_version": 2,
            "run_id": run_id,
            "status": "running",
            "results": [],
            "method": "Fresh process per resolution; cold load separate; no offload, VAE tiling or slicing",
        }
    )
    save(root, summary)
    for width, height in arguments.resolutions:
        case = root / f"{width}x{height}"
        print(f"{arguments.suite}: testing {width}x{height}", flush=True)
        report, status = child(
            command_for(arguments, width, height, case), case, arguments.timeout
        )
        row = summarize(
            width, height, report, status, arguments.min_headroom_mib, arguments.suite
        )
        if arguments.suite == "fixed-seed" and status == "passed":
            restarted, restart_status = child(
                command_for(arguments, width, height, case / "restart", restart=True),
                case / "restart",
                arguments.timeout,
            )
            restarted_hash = (restarted.get("runs") or [{}])[0].get("pixel_sha256")
            row["restart_pixel_sha256"] = restarted_hash
            row["fresh_process_bit_exact"] = (
                restart_status == "passed"
                and bool(row.get("reference_pixel_sha256"))
                and restarted_hash == row.get("reference_pixel_sha256")
            )
            if not row["fresh_process_bit_exact"]:
                row.update(
                    status="failed",
                    error="Fresh-process fixed-seed reproduction failed: "
                    + restarted.get("error", "pixel mismatch"),
                )
        for field in ("environment", "kernel", "source_sha256", "generation_profile"):
            if field not in summary and report.get(field):
                summary[field] = report[field]
        summary["results"].append(row)
        print(json.dumps(row), flush=True)
        save(root, summary)
        if row["status"] == "failed":
            summary.update(status="failed", stop_reason=f"Failure at {width}x{height}")
            save(root, summary)
            raise SystemExit(1)
        if arguments.stop_on_limit and row["status"] != "passed":
            summary["stop_reason"] = (
                f"Capacity boundary at {width}x{height}: {row['status']}"
            )
            break
    summary["status"] = "completed"
    save(root, summary)
    print(f"Benchmark summary: {root / 'summary.json'}", flush=True)
