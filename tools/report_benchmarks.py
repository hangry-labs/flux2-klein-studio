"""Archive portable benchmark runs and rebuild separate reports without inference."""

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path

DIRECTORIES = {"speed": "speed", "memory": "memory/gpu", "fixed-seed": "fixed-seed"}
COMMON = ("width", "height", "megapixels", "status", "completed_calls")
COLUMNS = {
    "speed": (
        "median_seconds",
        "mean_seconds",
        "min_seconds",
        "max_seconds",
        "stdev_seconds",
        "p95_seconds",
        "images_per_second",
        "first_call_seconds",
        "loading_seconds",
    ),
    "memory": (
        "loading_allocated_peak_bytes",
        "loading_reserved_peak_bytes",
        "allocated_peak_bytes",
        "reserved_peak_bytes",
        "baseline_device_mib",
        "loading_device_peak_mib",
        "generation_device_peak_mib",
        "loading_device_delta_mib",
        "generation_device_delta_mib",
        "sampled_device_headroom_mib",
        "vae_tiling",
        "vae_slicing",
        "failure_allocated_peak_bytes",
    ),
    "fixed-seed": (
        "same_seed_bit_exact",
        "changed_seed_bit_exact",
        "changed_seed_changes_pixels",
        "changed_prompt_changes_pixels",
        "reference_pixel_sha256",
        "fresh_process_bit_exact",
        "restart_pixel_sha256",
        "changed_reference_bit_exact",
        "changed_reference_changes_pixels",
    ),
}


def portable(data):
    suite = data["suite"]
    if data.get("schema_version") != 2 or suite not in DIRECTORIES:
        raise ValueError("Expected a version-2 separated-suite summary")
    if data["status"] == "running":
        raise ValueError("The suite is still running; wait for it to finish")
    if not re.fullmatch(r"[0-9TZ]+", data["run_id"]):
        raise ValueError("Invalid run identifier")
    fields = (
        "schema_version",
        "run_id",
        "suite",
        "status",
        "steps",
        "seed",
        "prompt",
        "reduction",
        "warmups",
        "measured_calls_per_resolution",
        "workload",
        "workload_sha256",
        "resolutions",
        "restart_calls_per_resolution",
        "measurement_context",
        "method",
        "source_sha256",
        "generation_profile",
    )
    result = {key: data[key] for key in fields if key in data}
    result["raw_summary_sha256"] = hashlib.sha256(
        json.dumps(data, sort_keys=True).encode()
    ).hexdigest()
    allowed_env = (
        "versions",
        "cuda_runtime",
        "driver_version",
        "gpu",
        "capability",
        "total_vram_bytes",
        "allocator_config",
        "model",
        "revision",
    )
    result["environment"] = {
        key: data["environment"][key]
        for key in allowed_env
        if key in data.get("environment", {})
    }
    result["kernel"] = {
        key: data["kernel"][key]
        for key in ("repository", "revision", "file_sha256")
        if key in data.get("kernel", {})
    }
    result["results"] = []
    for row in data["results"]:
        projected = {key: row[key] for key in COMMON + COLUMNS[suite] if key in row}
        if row.get("error"):
            # Raw exceptions may contain cache paths, local configuration or prompt data.
            projected["error"] = (
                "Case did not pass; details retained in ignored local logs"
            )
        result["results"].append(projected)
    return result


def text(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value.rstrip() + "\n", encoding="utf-8")


def number(value, divisor=1):
    if value is None:
        return "n/a"
    return f"{value / divisor:.3f}"


def cells(suite, row, run, include_reference=False):
    identity = [f"{row['width']}×{row['height']}", row["status"]]
    if suite == "speed":
        checks = run.get("generation_profile", {}).get("per_step_finite_check")
        return (
            identity
            + ["on" if checks else "initial unchecked profile"]
            + [
                number(row.get(key))
                for key in (
                    "median_seconds",
                    "p95_seconds",
                    "min_seconds",
                    "max_seconds",
                    "images_per_second",
                )
            ]
        )
    if suite == "memory":
        return (
            identity
            + [
                number(row.get(key), 2**30)
                for key in (
                    "loading_allocated_peak_bytes",
                    "loading_reserved_peak_bytes",
                    "allocated_peak_bytes",
                    "reserved_peak_bytes",
                )
            ]
            + [
                str(row.get(key, "n/a"))
                for key in (
                    "baseline_device_mib",
                    "generation_device_delta_mib",
                    "sampled_device_headroom_mib",
                )
            ]
        )
    return identity + [
        str(row.get(key, "n/a"))
        for key in (
            "same_seed_bit_exact",
            "changed_seed_bit_exact",
            "changed_seed_changes_pixels",
            "changed_prompt_changes_pixels",
            "fresh_process_bit_exact",
        )
        + (
            ("changed_reference_bit_exact", "changed_reference_changes_pixels")
            if include_reference
            else ()
        )
    ]


def render(root, suite):
    folder = root / DIRECTORIES[suite]
    runs = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((folder / "runs").glob("*.json"))
    ]
    headers = {
        "speed": ["Step checks", "Median s", "p95 s", "Min s", "Max s", "Images/s"],
        "memory": [
            "Load allocated GiB",
            "Load reserved GiB",
            "Generation allocated GiB",
            "Generation reserved GiB",
            "Device baseline MiB",
            "Generation device delta MiB",
            "Minimum free MiB",
        ],
        "fixed-seed": [
            "Same seed exact",
            "Second seed exact",
            "Changed seed differs",
            "Changed prompt differs",
            "Restart exact",
        ],
    }[suite]
    include_reference = suite == "fixed-seed" and any(
        "changed_reference_bit_exact" in row for run in runs for row in run["results"]
    )
    if include_reference:
        headers += ["Changed reference exact", "Changed reference differs"]
    lines = [
        f"# {suite} benchmark history",
        "",
        "Each run and resolution remain separate; measurements are never pooled.",
        "",
        "| Run | Resolution | Status | " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * (3 + len(headers))) + " |",
    ]
    for run in runs:
        for row in run["results"]:
            lines.append(
                "| "
                + " | ".join(
                    [f"[{run['run_id']}](runs/{run['run_id']}.json)"]
                    + cells(suite, row, run, include_reference)
                )
                + " |"
            )
    text(
        folder / "BENCHMARKS.md",
        "\n".join(
            lines
            + ["", "See [DETAILS.md](DETAILS.md) for the latest run and methodology."]
        ),
    )
    latest = runs[-1]
    env = latest["environment"]
    details = [
        f"# {suite} details",
        "",
        f"Latest run: `{latest['run_id']}`.",
        "",
        f"GPU: {env.get('gpu', 'unknown')}; driver: {env.get('driver_version', 'unknown')}; allocator: `{env.get('allocator_config', 'unknown')}`.",
        "",
        f"{latest['steps']} steps, {latest['warmups']} discarded warmups and {latest['measured_calls_per_resolution']} measured calls per resolution.",
        "",
        f"Workload SHA-256: `{latest['workload_sha256']}`.",
        "",
        f"Context: {latest['measurement_context']}.",
        "",
        "Full settings, dependency/source fingerprints and individual rows are in the immutable run JSON and CSV.",
        "",
    ]
    if suite == "speed":
        details += [
            "Pipeline wall time includes tokenization, encoding, denoising, decoding and CUDA synchronization.",
            "It excludes model loading, output checks, pixel hashing, PNG writes and container startup.",
            "No periodic GPU polling, peak-memory resets or equality assertions run in this suite.",
            "The current timed profile retains normal CLI per-step finite-value checks. The initial separated speed run omitted them; compare those profiles with care.",
            "The default text workload rotates three prompts; an explicit prompt uses one instruction with deterministic per-call seeds. Warmups use the primary prompt.",
            "First-call time can include compilation; persistent caches may already contain some shapes.",
            "p95 uses linear interpolation and is descriptive with only five samples.",
        ]
    elif suite == "memory":
        details += [
            "Loading and generation use separate peak counters and sampled telemetry.",
            "Generation allocated/reserved peaks cover measured calls; warmups are excluded from those aggregates.",
            "Minimum sampled free memory includes loading, warmups and measured calls for capacity checking.",
            "Device delta is whole-device peak minus the pre-load baseline; it includes context/allocator/driver effects and other applications.",
            "It is an estimate on this shared GPU, not exact per-process attribution. Values may be negative if other workloads release memory.",
            "Sampling targets 100 ms plus query overhead and can miss brief peaks; Torch counters capture allocator peaks.",
            "The 512 MiB headroom flag is advisory. CUDA placement does not rule out Windows driver paging.",
            "Cold-load baseline already includes the CUDA context initialized by doctor; it is taken before model loading.",
            "No application offload, VAE tiling or slicing. No speed claim is derived from this instrumented suite.",
        ]
    else:
        details += [
            "The primary prompt/seed must repeat exactly, including warmups and every measured image.",
            "A second seed must repeat exactly and produce different pixels. A changed prompt must also produce different pixels.",
            "A separate fresh process generates the primary seed once and must match its exact pixel hash.",
            "The checks detect run-to-run regressions on the same GPU/stack; they do not assert GPU-to-GPU equality or aesthetic quality.",
            "The seed suite never reports speed or VRAM as benchmark results.",
        ]
    reference = latest.get("generation_profile", {}).get("reference")
    if reference:
        details += [
            "",
            "Single-reference editing profile.",
            f"Prepared reference: {reference['width']}×{reference['height']}; SHA-256 `{reference['sha256']}`.",
            f"Preprocessing: `{reference['preprocessing']}`. Reference encoding and conditioning are included in pipeline work.",
            "Reference upload, file decoding and normalization precede measurement; the pipeline includes VAE reference encoding and conditioning.",
            "Mirrored-reference controls in the fixed-seed suite must repeat exactly and differ from the primary reference.",
        ]
    text(folder / "DETAILS.md", "\n".join(details))


def archive(data, root, chart_prefix="", chart_base=""):
    data = portable(data)
    folder = root / DIRECTORIES[data["suite"]] / "runs"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (data["run_id"] + ".json")
    encoded = json.dumps(data, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") != encoded:
        raise ValueError(
            "Refusing to replace an existing run with changed measurements"
        )
    text(path, encoded)
    fields = list(dict.fromkeys(key for row in data["results"] for key in row))
    with path.with_suffix(".csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(data["results"])
    render(root, data["suite"])
    index = [
        "# Benchmark summaries",
        "",
        "Per-size results remain separate by suite and run.",
        "",
        "| Suite | Latest run | Report | Summary chart |",
        "| --- | --- | --- | --- |",
    ]
    for suite, directory in DIRECTORIES.items():
        matches = sorted((root / directory / "runs").glob("*.json"))
        if matches:
            latest = matches[-1].stem
            index.append(
                f"| {suite} | [{latest}]({directory}/runs/{latest}.json) | [History]({directory}/BENCHMARKS.md) | [{chart_prefix}{suite}.svg]({chart_base}{chart_prefix}{suite}.svg) |"
            )
    extra_profiles = [
        path
        for path in sorted(root.glob("*/BENCHMARKS.md"))
        if path.parent.name not in {"memory", "speed", "fixed-seed", "history"}
    ]
    if extra_profiles:
        index += ["", "Additional workload profiles:", ""] + [
            f"- [{path.parent.name}]({path.parent.name}/BENCHMARKS.md)"
            for path in extra_profiles
        ]
    text(
        root / "BENCHMARKS.md",
        "\n".join(
            index
            + [
                "",
                "Methodology and commands: [README.md](README.md).",
                *(
                    [
                        "Historical results: [allocator comparison](history/allocator-comparison/README.md)."
                    ]
                    if (root / "history/allocator-comparison/README.md").is_file()
                    else []
                ),
                "",
            ]
        ),
    )
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=tuple(DIRECTORIES), required=True)
    parser.add_argument("--input", default="latest")
    parser.add_argument("--artifacts", type=Path, default=Path("outputs/benchmarks"))
    parser.add_argument("--root", type=Path, default=Path("benchmarks"))
    parser.add_argument("--plot", action="store_true")
    parser.add_argument(
        "--profile",
        choices=("refine",),
        help="Keep single-reference results separate from text-only reports",
    )
    args = parser.parse_args()
    report_root = args.root / args.profile if args.profile else args.root
    if args.input == "latest":
        matches = sorted(args.artifacts.glob(f"{args.suite}-*/summary.json"))
        if not matches:
            # Published snapshots are enough to regenerate reports and charts.
            matches = sorted(
                (report_root / DIRECTORIES[args.suite] / "runs").glob("*.json")
            )
        if not matches:
            parser.error(f"No local or archived {args.suite} summary found")
        source = matches[-1]
    else:
        source = Path(args.input)
    data = json.loads(source.read_text(encoding="utf-8"))
    if data["suite"] != args.suite:
        parser.error("The selected input belongs to another suite")
    if (
        args.profile
        and data.get("generation_profile", {}).get("mode") != "single-reference"
    ):
        parser.error("Refine reports require a measured single-reference profile")
    archived = archive(
        data,
        report_root,
        (args.profile + "-") if args.profile else "",
        "../" if args.profile else "",
    )
    if args.plot:
        from plot_benchmark import render as plot

        latest = sorted(archived.parent.glob("*.json"))[-1]
        chart_name = (args.profile + "-" if args.profile else "") + args.suite + ".svg"
        plot(
            json.loads(latest.read_text(encoding="utf-8")),
            args.root / chart_name,
        )
    print(archived)


if __name__ == "__main__":
    main()
