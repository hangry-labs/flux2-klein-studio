"""Project an archived memory run into the studio's packaged reference table."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from flux2_klein_studio.cli import MODEL_ID, MODEL_REVISION  # noqa: E402
from flux2_klein_studio.presets import SIZES  # noqa: E402


def project(data):
    if (
        data.get("schema_version") != 2
        or data.get("suite") != "memory"
        or data.get("status") == "running"
    ):
        raise ValueError("Select a completed, archived memory-suite run")
    env = data["environment"]
    if (
        env.get("model") != MODEL_ID
        or env.get("revision") != MODEL_REVISION
        or data.get("reduction") != "ordered"
        or data.get("steps") != 4
        or env.get("allocator_config") != "expandable_segments:True"
    ):
        raise ValueError(
            "Memory reference must match the studio's pinned model, four-step ordered profile and allocator"
        )
    rows = {(row["width"], row["height"]): row for row in data["results"]}
    if any(size not in rows for size in SIZES.values()):
        raise ValueError(
            "This run does not cover every named resolution; run task benchmark:presets"
        )
    return {
        "schema_version": 1,
        "run_id": data["run_id"],
        "archive": f"benchmarks/memory/gpu/runs/{data['run_id']}.json",
        "archive_content_sha256": hashlib.sha256(
            json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "environment": env,
        "steps": data["steps"],
        "reduction": data["reduction"],
        "warmups": data["warmups"],
        "measured_calls_per_resolution": data["measured_calls_per_resolution"],
        "workload_sha256": data["workload_sha256"],
        "method": data["method"],
        "measurement_context": data["measurement_context"],
        "results": {name.value: rows[size] for name, size in SIZES.items()},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("src/flux2_klein_studio/data/preset-memory.json"),
    )
    args = parser.parse_args()
    source = args.input
    if source is None:
        paths = sorted(Path("benchmarks/memory/gpu/runs").glob("*.json"))
        if not paths:
            parser.error("No archived memory run found")
        source = paths[-1]
    data = project(json.loads(source.read_text(encoding="utf-8")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"{args.output}: {data['run_id']}, {len(data['results'])} named resolutions")


if __name__ == "__main__":
    main()
