"""Small, deterministic benchmark workloads; importing this never imports torch."""

import hashlib
import json
import statistics

from .cli import DEFAULT_PROMPT

CONTROL_PROMPT = (
    "A small orange sailboat on a calm mountain lake at sunrise, realistic photograph"
)
PORTRAIT_PROMPT = "A studio portrait of an elderly woman wearing a blue scarf, soft light, realistic photograph"
BENCHMARK_PROMPTS = (DEFAULT_PROMPT, CONTROL_PROMPT, PORTRAIT_PROMPT)
SUITE_COMMANDS = {"speed": "benchmark", "memory": "memory", "fixed-seed": "seed-test"}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def jobs(arguments):
    if arguments.command == "generate":
        return [
            {"label": "generate", "prompt": arguments.prompt, "seed": arguments.seed}
        ]
    prompts = (
        BENCHMARK_PROMPTS if arguments.prompt == DEFAULT_PROMPT else (arguments.prompt,)
    )
    fixed = arguments.command == "seed-test"
    result = [
        {"label": f"warmup-{i + 1}", "prompt": arguments.prompt, "seed": arguments.seed}
        for i in range(arguments.warmups)
    ]
    result += [
        {
            "label": f"measured-{i + 1}",
            "prompt": arguments.prompt if fixed else prompts[i % len(prompts)],
            "seed": arguments.seed if fixed else arguments.seed + i,
        }
        for i in range(arguments.runs)
    ]
    if fixed:
        result += [
            {
                "label": f"changed-seed-{i + 1}",
                "prompt": arguments.prompt,
                "seed": arguments.seed + 1,
            }
            for i in range(2)
        ]
        result.append(
            {
                "label": "changed-prompt",
                "prompt": CONTROL_PROMPT
                if arguments.prompt != CONTROL_PROMPT
                else PORTRAIT_PROMPT,
                "seed": arguments.seed,
            }
        )
    return result


def timing_summary(values):
    values = sorted(values)
    index = (len(values) - 1) * 0.95
    lower = int(index)
    p95 = values[lower] + (values[min(lower + 1, len(values) - 1)] - values[lower]) * (
        index - lower
    )
    return {
        "median_seconds": statistics.median(values),
        "mean_seconds": statistics.mean(values),
        "min_seconds": values[0],
        "max_seconds": values[-1],
        "stdev_seconds": statistics.pstdev(values),
        "p95_seconds": p95,
        "images_per_second": len(values) / sum(values),
    }


def seed_checks(records):
    fixed = [
        r["pixel_sha256"]
        for r in records
        if r["label"].startswith(("warmup-", "measured-"))
    ]
    alternate = [
        r["pixel_sha256"] for r in records if r["label"].startswith("changed-seed-")
    ]
    control = next(
        (r["pixel_sha256"] for r in records if r["label"] == "changed-prompt"), None
    )
    return {
        "same_seed_bit_exact": len(set(fixed)) == 1 and len(fixed) >= 3,
        "changed_seed_bit_exact": len(alternate) == 2 and len(set(alternate)) == 1,
        "changed_seed_changes_pixels": bool(
            fixed and alternate and alternate[0] != fixed[0]
        ),
        "changed_prompt_changes_pixels": bool(
            fixed and control and control != fixed[0]
        ),
        "reference_pixel_sha256": fixed[0] if fixed else None,
    }
