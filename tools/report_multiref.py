"""Project multi-reference evidence into portable immutable reports and a root SVG."""

import argparse
import csv
import html
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", type=Path)
    parser.add_argument("--root", type=Path, default=Path("benchmarks"))
    args = parser.parse_args()
    raw = json.loads(args.summary.read_text())
    suite = raw["suite"]
    run_id = args.summary.parent.name
    cases = []
    for source in raw["cases"]:
        row = {key: source[key] for key in ("count", "edge", "status")}
        row["references"] = source.get("references", [])
        if suite == "memory" and source["status"] == "passed":
            row.update(
                allocated_peak_bytes=max(
                    r["allocator"]["allocated_peak_bytes"] for r in source["runs"]
                ),
                reserved_peak_bytes=max(
                    r["allocator"]["reserved_peak_bytes"] for r in source["runs"]
                ),
                device_peak_mib=max(r["device"]["peak_mib"] for r in source["runs"]),
                minimum_free_mib=min(
                    r["device"]["minimum_free_mib"] for r in source["runs"]
                ),
                loading_device=source["loading_device"],
                loaded_allocator=source["loaded_allocator"],
                warmup=source["warmup"],
                samples=source["runs"],
            )
        elif suite == "quality":
            row.update(
                pixel_sha256=source.get("pixel_sha256"),
                repeat_equal=source.get("repeat_equal"),
            )
        cases.append(row)
    first = raw["cases"][0]
    environment = {
        key: first["environment"][key]
        for key in (
            "gpu",
            "driver_version",
            "torch",
            "cuda_runtime",
            "model",
            "revision",
            "allocator_config",
        )
        if key in first.get("environment", {})
    }
    report = {
        "run_id": run_id,
        "suite": suite,
        "output_size": "1024x1024",
        "seed": 12345,
        "steps": 4,
        "prompt": first.get("prompt"),
        "environment": environment,
        "method": "Fresh worker per profile; populated offline cache; ordered NVFP4 r32/NF4, all model components on GPU; no offload/tiling changes; shared live system. Memory: one warmup, two measured calls, whole-device sampling target100ms plus allocator counters; not a speed benchmark. Inputs: generated fictional-person views, hashes retained.",
        "harness_sha256": first.get("harness_sha256"),
        "versions": first.get("environment", {}).get("versions", {}),
        "cases": cases,
    }
    folder = args.root / "multiref" / ("memory/gpu" if suite == "memory" else "quality")
    archive = folder / "runs"
    archive.mkdir(parents=True, exist_ok=True)
    target = archive / (run_id + ".json")
    if target.exists() and first.get("harness_sha256") is None:
        previous = json.loads(target.read_text())
        if (
            previous["cases"] != cases
            or previous["prompt"] != report["prompt"]
            or previous["environment"] != environment
        ):
            raise ValueError("Refusing to replace different legacy evidence")
        report = previous
    text = json.dumps(report, indent=2) + "\n"
    if target.exists() and target.read_text() != text:
        raise ValueError("Refusing to replace different evidence")
    target.write_text(text, encoding="utf-8")
    fields = ["count", "edge", "status"] + (
        [
            "allocated_peak_bytes",
            "reserved_peak_bytes",
            "device_peak_mib",
            "minimum_free_mib",
        ]
        if suite == "memory"
        else ["pixel_sha256", "repeat_equal"]
    )
    with target.with_suffix(".csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(cases)
    history = [
        "# Multi-reference " + suite + " history",
        "",
        "| Run | Evidence |",
        "| --- | --- |",
    ]
    for path in sorted(archive.glob("*.json")):
        history.append(
            f"| {path.stem} | [JSON](runs/{path.name}) · [CSV](runs/{path.stem}.csv) |"
        )
    (folder / "BENCHMARKS.md").write_text("\n".join(history) + "\n", encoding="utf-8")
    if target != sorted(archive.glob("*.json"))[-1]:
        return
    details = [
        "# Multi-reference " + suite,
        "",
        report["method"],
        "",
        "Run: `"
        + run_id
        + "`. All outputs1024×1024,4steps,seed12345. Reference edge is per input, not output resolution.",
        "",
    ]
    if suite == "memory":
        details += [
            "| References | Reference edge | Status | PyTorch allocated GiB | Reserved GiB | Device peak GiB | Min free GiB |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for r in cases:
            values = [
                f"{r[k] / scale:.2f}" if k in r else "—"
                for k, scale in [
                    ("allocated_peak_bytes", 1024**3),
                    ("reserved_peak_bytes", 1024**3),
                    ("device_peak_mib", 1024),
                    ("minimum_free_mib", 1024),
                ]
            ]
            details.append(
                "| "
                + " | ".join([str(r["count"]), str(r["edge"]), r["status"], *values])
                + " |"
            )
        details += [
            "",
            "Device measurements include the desktop and other processes; sampling can miss peaks. Allocated counters describe PyTorch tensors/workspace. Spare VRAM is workload-specific, not a universal capacity guarantee. Loading/warmup/per-call records are retained in the JSON.",
        ]
        svg = [
            '<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="440" viewBox="0 0 1000 440"><rect width="1000" height="440" fill="#15171b"/><g font-family="sans-serif" fill="#eee"><text x="25" y="32" font-size="21">Multi-reference VRAM · 1024×1024 output · RTX 5070 Ti</text><text x="25" y="57" font-size="13">Whole-device peak (GiB), shared live system · one warmup + two calls · four steps</text>'
        ]
        for i, r in enumerate(cases):
            y = 90 + i * 47
            value = r.get("device_peak_mib", 0) / 1024
            svg.append(
                f'<text x="25" y="{y + 19}" font-size="14">{r["count"]} refs × {r["edge"]}</text><rect x="170" y="{y}" width="{value * 53:.1f}" height="28" rx="4" fill="{"#e98a45" if r["edge"] == 1024 else "#66b9bd"}"/><text x="{180 + value * 53:.1f}" y="{y + 19}" font-size="14">{value:.2f} GiB · {html.escape(r["status"])}</text>'
            )
        svg.append(
            '<text x="25" y="411" font-size="12">Reference edge describes input size. Base model only; NVFP4 r32 + NF4; no model offload. See benchmark details for allocator peaks.</text></g></svg>'
        )
        (args.root / "multiref-memory.svg").write_text("".join(svg), encoding="utf-8")
    else:
        details += [
            "| References | Edge | Status | Repeat checked |",
            "| --- | --- | --- | --- |",
        ]
        details += [
            f"| {r['count']} | {r['edge']} | {r['status']} | {r.get('repeat_equal') if r.get('repeat_equal') is not None else 'Not run'} |"
            for r in cases
        ]
        details += [
            "",
            "Exact hashes establish reproducibility, not likeness. Synthetic views may already contain identity drift. Visual review and additional real-photo evaluation remain separate.",
        ]
    (folder / "DETAILS.md").write_text("\n".join(details) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
