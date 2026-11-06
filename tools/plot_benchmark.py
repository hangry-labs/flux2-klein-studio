"""Render a resolution-sweep summary using the optional reporting container."""

import argparse
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
matplotlib.rcParams["svg.hashsalt"] = "flux2-klein-studio"
import matplotlib.pyplot as plt  # noqa: E402


def save_figure(fig, output):
    fig.savefig(output, metadata={"Date": None})
    if output.suffix.lower() == ".svg":
        # Matplotlib emits trailing spaces inside path attributes; normalize only
        # whitespace so regenerated figures pass repository diff checks.
        output.write_text(
            "\n".join(
                line.rstrip()
                for line in output.read_text(encoding="utf-8").splitlines()
            )
            + "\n",
            encoding="utf-8",
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    render(report, args.output)


def render(report, output):
    if report.get("schema_version") == 2:
        render_suite(report, output)
        return
    rows = report["results"]
    labels = [f"{r['width']}×{r['height']}" for r in rows]
    positions = list(range(len(rows)))
    plt.rcParams.update({"svg.fonttype": "none", "font.size": 10})
    fig, (speed, memory) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
    for index, row in enumerate(rows):
        median = row.get("median_seconds")
        if median is None:
            speed.text(index, 0, row["status"], rotation=90, va="bottom", ha="center")
            continue
        color = "#c67922" if row["status"] != "passed" else "#28789b"
        speed.bar(index, median, color=color)
        speed.errorbar(
            index,
            median,
            yerr=[[median - row["min_seconds"]], [row["max_seconds"] - median]],
            fmt="none",
            capsize=4,
            color="#243746",
        )
        speed.annotate(
            f"{median:.2f}",
            (index, row["max_seconds"]),
            xytext=(0, 5),
            textcoords="offset points",
            ha="center",
        )
    speed.set_ylabel("Seconds per image")
    speed.set_title("Median generation time; whiskers show measured min–max")
    speed.margins(y=0.2)
    for field, label, color in (
        ("allocated_peak_bytes", "PyTorch allocated peak", "#28789b"),
        ("reserved_peak_bytes", "PyTorch reserved peak", "#c67922"),
    ):
        values = [
            r.get(field, 0) / 2**30 if r.get(field) is not None else float("nan")
            for r in rows
        ]
        memory.plot(positions, values, "o-", label=label, color=color)
    total = report["environment"]["total_vram_bytes"] / 2**30
    memory.axhline(
        total,
        color="#9c2736",
        linestyle="--",
        label="GPU capacity (other processes also need memory)",
    )
    memory.set_ylabel("GiB")
    memory.set_title(
        "Allocator peaks across warmups, measured calls and prompt control"
    )
    memory.legend(loc="upper left", fontsize=9)
    memory.set_xticks(positions, labels, rotation=35, ha="right")
    for axes in (speed, memory):
        axes.grid(axis="y", alpha=0.2)
        axes.set_axisbelow(True)
    env = report["environment"]
    fig.suptitle(
        f"{env['gpu']} · {report['steps']} steps · {report['reduction']} reduction\n"
        f"{report['warmups']} warmups + {report['measured_calls_per_resolution']} measured calls · live desktop system",
        fontsize=13,
    )
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    save_figure(fig, output)
    plt.close(fig)
    print(output)


def render_suite(report, output):
    suite = report["suite"]
    rows = report["results"]
    positions = list(range(len(rows)))
    labels = [f"{r['width']}×{r['height']}" for r in rows]
    plt.rcParams.update({"svg.fonttype": "none", "font.size": 10})
    fig, axes = plt.subplots(figsize=(12, 6))
    if suite == "speed":
        for i, row in enumerate(rows):
            median = row.get("median_seconds")
            if median is None:
                axes.text(i, 0, row["status"], rotation=90, ha="center", va="bottom")
                continue
            axes.bar(i, median, color="#28789b")
            axes.errorbar(
                i,
                median,
                yerr=[[median - row["min_seconds"]], [row["max_seconds"] - median]],
                fmt="none",
                capsize=4,
                color="#243746",
            )
            axes.annotate(
                f"{median:.2f}",
                (i, row["max_seconds"]),
                xytext=(0, 6),
                textcoords="offset points",
                ha="center",
            )
        axes.set_ylabel("Seconds per image")
        profile = (
            "normal CLI guards"
            if report.get("generation_profile", {}).get("per_step_finite_check")
            else "initial unchecked profile"
        )
        axes.set_title(f"Warmed generation · {profile} · median with measured min–max")
        axes.margins(y=0.2)
    elif suite == "memory":
        for field, label, color, style in (
            ("allocated_peak_bytes", "Generation allocated peak", "#28789b", "o-"),
            ("reserved_peak_bytes", "Generation reserved peak", "#c67922", "o-"),
            ("loading_reserved_peak_bytes", "Loading reserved peak", "#71816b", "s--"),
        ):
            values = [
                r[field] / 2**30 if r.get(field) is not None else float("nan")
                for r in rows
            ]
            axes.plot(positions, values, style, label=label, color=color)
        values = [
            r["generation_device_peak_mib"] / 1024
            if r.get("generation_device_peak_mib") is not None
            else float("nan")
            for r in rows
        ]
        axes.plot(
            positions,
            values,
            "d:",
            label="Whole-device sampled generation peak",
            color="#79548b",
        )
        if report["environment"].get("total_vram_bytes") is not None:
            axes.axhline(
                report["environment"]["total_vram_bytes"] / 2**30,
                color="#9c2736",
                linestyle="--",
                label="GPU capacity",
            )
        for i, row in enumerate(rows):
            if row["status"] != "passed":
                if math.isfinite(values[i]):
                    axes.annotate(
                        row["status"],
                        (i, values[i]),
                        xytext=(0, 9),
                        textcoords="offset points",
                        ha="center",
                        fontsize=8,
                    )
                else:
                    axes.text(
                        i,
                        0.02,
                        row["status"],
                        transform=axes.get_xaxis_transform(),
                        rotation=90,
                        ha="center",
                        va="bottom",
                        fontsize=8,
                    )
        axes.set_ylabel("GiB")
        axes.set_title(
            "VRAM per resolution · loading separate from measured generation"
        )
        axes.legend(fontsize=9, loc="upper left")
    else:
        keys = (
            "same_seed_bit_exact",
            "changed_seed_bit_exact",
            "changed_seed_changes_pixels",
            "changed_prompt_changes_pixels",
            "fresh_process_bit_exact",
        )
        names = (
            "Same seed exact",
            "Second seed exact",
            "Changed seed differs",
            "Changed prompt differs",
            "Fresh process exact",
        )
        matrix = [[int(row.get(key) is True) for row in rows] for key in keys]
        axes.imshow(matrix, vmin=0, vmax=1, cmap="RdYlGn", aspect="auto")
        for y in range(len(keys)):
            for x in positions:
                axes.text(
                    x,
                    y,
                    "PASS" if matrix[y][x] else "FAIL / incomplete",
                    ha="center",
                    va="center",
                    color="white",
                )
        axes.set_yticks(range(len(keys)), names)
        axes.set_title(
            "Fixed-seed correctness · exact pixels and changed-input controls"
        )
    axes.set_xticks(positions, labels, rotation=35, ha="right")
    if suite != "fixed-seed":
        axes.grid(axis="y", alpha=0.2)
        axes.set_axisbelow(True)
    fig.suptitle(
        f"{report['environment'].get('gpu', 'GPU unavailable')} · {report['steps']} steps · {suite}\n"
        f"{report['warmups']} warmups + {report['measured_calls_per_resolution']} measured calls · shared live system",
        fontsize=12,
    )
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    save_figure(fig, output)
    plt.close(fig)
    print(output)


if __name__ == "__main__":
    main()
