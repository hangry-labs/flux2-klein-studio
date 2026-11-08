"""Fixed-1024 multi-reference memory and visual qualification, in fresh workers."""

import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import time

import torch
from PIL import Image

from flux2_klein_studio.cli import doctor
from flux2_klein_studio.inference import (
    DeviceMemorySampler,
    check_quantization,
    check_residency,
    check_step,
    load_pipeline,
    memory,
    write_json,
)
from flux2_klein_studio.references import normalize_reference

PROMPT = "All reference images show the same adult man. Preserve his face, red hair, freckles, round glasses and blue ear stud. Show this man seated outdoors at a cafe in a dark green jacket, waist-up portrait, natural daylight."


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite", choices=["fixtures", "memory", "quality"], required=True
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("/app/persistent/outputs/multiref")
    )
    parser.add_argument("--reference", type=Path, action="append")
    parser.add_argument("--count", type=int, choices=[1, 2, 4])
    parser.add_argument("--edge", type=int, choices=[512, 1024], default=512)
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args()
    if args.plan:
        print(
            json.dumps(
                {
                    "suite": args.suite,
                    "output": "1024x1024",
                    "seed": 12345,
                    "steps": 4,
                    "reference_counts": [1, 2, 4],
                    "reference_edges": [512, 1024]
                    if args.suite == "memory"
                    else [args.edge],
                    "memory_warmups": 1,
                    "memory_measured_calls": 2,
                    "workers": "fresh per memory profile",
                    "quality": "synthetic identity, visual review required",
                    "cache": "offline populated",
                    "isolation": "shared live system; unrelated services untouched",
                }
            )
        )
        return
    args.output_dir.mkdir(parents=True, exist_ok=True)
    fixtures = args.output_dir / "fixtures"
    if args.suite != "fixtures" and args.count is None:
        run = args.output_dir / (
            args.suite + "-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        )
        run.mkdir(exist_ok=False)
        rows = []
        for edge in [512, 1024] if args.suite == "memory" else [args.edge]:
            for count in [1, 2, 4]:
                target = run / f"{count}refs-{edge}"
                target.mkdir()
                command = [
                    sys.executable,
                    __file__,
                    "--suite",
                    args.suite,
                    "--count",
                    str(count),
                    "--edge",
                    str(edge),
                    "--output-dir",
                    str(target),
                ]
                for path in args.reference or [
                    fixtures / f"view-{i}.png" for i in range(1, 5)
                ]:
                    command += ["--reference", str(path)]
                with (target / "worker.log").open("w") as log:
                    try:
                        process = subprocess.run(
                            command, stdout=log, stderr=subprocess.STDOUT, timeout=360
                        )
                        row = (
                            json.loads((target / "report.json").read_text())
                            if (target / "report.json").exists()
                            else {
                                "status": "worker_failed",
                                "returncode": process.returncode,
                            }
                        )
                    except subprocess.TimeoutExpired:
                        row = {"status": "timeout"}
                rows.append({"count": count, "edge": edge, **row})
                write_json(run / "summary.json", {"suite": args.suite, "cases": rows})
                print(
                    json.dumps({"count": count, "edge": edge, "status": row["status"]}),
                    flush=True,
                )
        print("OUTPUT_DIR=" + str(run), flush=True)
        return
    report = {
        "status": "starting",
        "suite": args.suite,
        "width": 1024,
        "height": 1024,
        "seed": 12345,
        "steps": 4,
        "prompt": PROMPT,
        "runs": [],
        "harness_sha256": hashlib.sha256(
            Path(__file__).read_text().replace("\r\n", "\n").encode()
        ).hexdigest(),
    }

    def save():
        write_json(args.output_dir / "report.json", report)

    save()
    try:
        report["environment"] = doctor()
        if args.suite == "memory":
            with DeviceMemorySampler() as sampler:
                pipe = load_pipeline(
                    argparse.Namespace(offline=True, reduction="ordered"), report
                )
            report["loading_device"] = sampler.summary()
            write_json(args.output_dir / "loading-samples.json", sampler.samples)
            report["loaded_allocator"] = memory()
        else:
            pipe = load_pipeline(
                argparse.Namespace(offline=True, reduction="ordered"), report
            )
        report["quantization"] = check_quantization(pipe)
        report["placement"] = check_residency(pipe)
        if args.suite == "fixtures":
            fixtures.mkdir(exist_ok=True)
            front = None
            descriptions = [
                "front view, looking at the viewer",
                "left profile view, looking to the left",
                "right three-quarter view",
                "full-body standing view",
            ]
            for i, description in enumerate(descriptions, 1):
                text = (
                    (
                        "Photographic portrait of a fictional adult man aged 35, short curly red hair, freckles, round black glasses, a small blue ear stud, plain white shirt, neutral gray studio background, "
                        + description
                    )
                    if front is None
                    else "Show the exact same man from the reference, preserving his face, red hair, freckles, glasses and blue ear stud, plain white shirt, neutral gray studio background, "
                    + description
                )
                with torch.inference_mode():
                    image = pipe(
                        prompt=text,
                        width=1024,
                        height=1024,
                        num_inference_steps=4,
                        guidance_scale=1.0,
                        generator=torch.Generator("cuda").manual_seed(12344 + i),
                        callback_on_step_end=check_step,
                        **({"image": front} if front else {}),
                    ).images[0]
                image.save(fixtures / f"view-{i}.png")
                if front is None:
                    front = image
            report["status"] = "synthetic_fixtures_created"
            return
        references = []
        for path in (args.reference or [])[: args.count]:
            png, metadata = normalize_reference(path.read_bytes())
            with Image.open(BytesIO(png)) as source:
                image = source.convert("RGB").resize(
                    (args.edge, args.edge), Image.Resampling.LANCZOS
                )
            references.append(image)
            report.setdefault("references", []).append(
                {
                    "pixel_sha256": hashlib.sha256(image.tobytes()).hexdigest(),
                    "width": image.width,
                    "height": image.height,
                }
            )
        if len(references) != args.count:
            raise ValueError("Supply four input references")

        def render():
            with torch.inference_mode():
                image = pipe(
                    prompt=PROMPT,
                    image=references[0] if len(references) == 1 else references,
                    width=1024,
                    height=1024,
                    num_inference_steps=4,
                    guidance_scale=1.0,
                    generator=torch.Generator("cuda").manual_seed(12345),
                    callback_on_step_end=check_step,
                ).images[0]
            torch.cuda.synchronize()
            check_residency(pipe)
            return image

        if args.suite == "memory":
            with DeviceMemorySampler() as sampler:
                torch.cuda.reset_peak_memory_stats()
                render()
            report["warmup"] = {"allocator": memory(), "device": sampler.summary()}
            write_json(args.output_dir / "warmup-samples.json", sampler.samples)
            save()
            for i in range(2):
                with DeviceMemorySampler() as sampler:
                    torch.cuda.reset_peak_memory_stats()
                    image = render()
                report["runs"].append(
                    {"allocator": memory(), "device": sampler.summary()}
                )
                write_json(
                    args.output_dir / f"run-{i + 1}-samples.json", sampler.samples
                )
                save()
                if i == 0:
                    image.save(args.output_dir / "result.png")
        else:
            image = render()
            image.save(args.output_dir / "result.png")
            digest = hashlib.sha256(image.tobytes()).hexdigest()
            report["pixel_sha256"] = digest
            if args.count == 4:
                repeated = render()
                report["repeat_equal"] = (
                    hashlib.sha256(repeated.tobytes()).hexdigest() == digest
                )
                if not report["repeat_equal"]:
                    raise RuntimeError("Same-seed repeat differs")
        report["placement_after"] = check_residency(pipe)
        report["status"] = "passed"
    except torch.OutOfMemoryError:
        report["status"] = "out_of_memory"
        report["partial_allocator"] = memory()
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        save()


if __name__ == "__main__":
    main()
