"""Matched red-rectangle zoom and iterative zoom pilot at 1024 pixels."""

import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path
import time

import torch
from PIL import Image, ImageDraw, ImageOps
from PIL.PngImagePlugin import PngInfo

from flux2_klein_studio.adapters import ZOOM_PROMPT
from flux2_klein_studio.cli import doctor
from flux2_klein_studio.inference import (
    load_pipeline,
    check_quantization,
    check_residency,
    check_step,
    memory,
)
from flux2_klein_studio.lora import load_adapter, remove_lora
from flux2_klein_studio.references import normalize_reference


def mark(image, region):
    image = image.copy()
    ImageDraw.Draw(image).rectangle(
        tuple(round(n * 1024) for n in region), outline=(255, 0, 0), width=8
    )
    return image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument(
        "--output-dir", type=Path, default=Path("/data/outputs/lora-zoom")
    )
    args = parser.parse_args()
    output = args.output_dir / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    output.mkdir(parents=True, exist_ok=False)
    data, metadata = normalize_reference(args.reference.read_bytes())
    with Image.open(BytesIO(data)) as image:
        source = ImageOps.fit(image.convert("RGB"), (1024, 1024))
    source.save(output / "source.png")
    references = {
        "region1": mark(source, (0.40, 0.06, 0.68, 0.45)),
        "region2": mark(source, (0.04, 0.32, 0.32, 0.74)),
    }
    for name, image in references.items():
        image.save(output / (name + "-input.png"))
    report = {
        "status": "running",
        "scope": "1024 visual diagnostic, not a benchmark or faithful-detail-recovery claim.",
        "source_reference": metadata,
        "source_preparation": "Reference normalization followed by a centered square fit to 1024x1024; see source.png.",
        "rectangles_xyxy": {
            "region1": [0.40, 0.06, 0.68, 0.45],
            "region2": [0.04, 0.32, 0.32, 0.74],
            "iteration": [0.25, 0.25, 0.75, 0.75],
        },
        "prompt": ZOOM_PROMPT,
        "seed": 12345,
        "steps": 4,
        "runs": [],
    }
    hashes = {}
    first = None

    def save():
        (output / "report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )

    save()
    try:
        report["environment"] = doctor()
        pipe = load_pipeline(
            argparse.Namespace(offline=args.offline, reduction="ordered"), report
        )
        for profile in ("base", "zoom", "repeat", "iteration", "removed"):
            if profile == "zoom":
                torch.cuda.empty_cache()
                before = torch.cuda.memory_allocated()
                report["adapter"] = load_adapter(
                    pipe.transformer, "zoom", offline=args.offline
                )
                report["adapter_allocation_delta_bytes"] = (
                    torch.cuda.memory_allocated() - before
                )
            elif profile == "removed":
                remove_lora(pipe.transformer)
                torch.cuda.empty_cache()
            report.setdefault("profiles", {})[profile] = {
                "quantization": check_quantization(pipe),
                "placement": check_residency(pipe),
            }
            selected = (
                references if profile == "zoom" else {"region1": references["region1"]}
            )
            if profile == "iteration":
                selected = {"region1": mark(first, (0.25, 0.25, 0.75, 0.75))}
                selected["region1"].save(output / "iteration-input.png")
            for name, reference in selected.items():
                torch.cuda.reset_peak_memory_stats()
                with torch.inference_mode():
                    image = pipe(
                        prompt=ZOOM_PROMPT,
                        image=reference,
                        width=1024,
                        height=1024,
                        num_inference_steps=4,
                        guidance_scale=1.0,
                        generator=torch.Generator("cuda").manual_seed(12345),
                        callback_on_step_end=check_step,
                    ).images[0]
                torch.cuda.synchronize()
                digest = hashlib.sha256(image.tobytes()).hexdigest()
                hashes[profile, name] = digest
                row = {
                    "profile": profile,
                    "reference": name,
                    "pixel_sha256": digest,
                    "image": f"{name}-{profile}.png",
                    "memory": memory(),
                }
                info = PngInfo()
                info.add_text(
                    "generation",
                    json.dumps(
                        {
                            **row,
                            "prompt": ZOOM_PROMPT,
                            "adapter": report.get("adapter")
                            if profile not in ("base", "removed")
                            else None,
                        }
                    ),
                )
                image.save(output / row["image"], pnginfo=info)
                report["runs"].append(row)
                save()
                if profile == "zoom" and name == "region1":
                    first = image.copy()
                if profile == "repeat":
                    assert digest == hashes["zoom", name]
                if profile == "removed":
                    assert digest == hashes["base", name]
                print(json.dumps(row), flush=True)
        report["status"] = "numerical_checks_passed_visual_review_required"
        files = [
            ("Original", "source.png"),
            ("First selection", "region1-input.png"),
            ("Zoom", "region1-zoom.png"),
            ("Next selection", "iteration-input.png"),
            ("Zoom again", "region1-iteration.png"),
            ("Second region", "region2-input.png"),
            ("Second zoom", "region2-zoom.png"),
            ("Base-model control", "region1-base.png"),
        ]
        page = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Red Zoom pilot</title><style>body{font:16px system-ui;background:#18191c;color:#eee;padding:24px}main{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:20px}figure{margin:0}img{width:100%}a{color:#ff984d}@media(max-width:700px){main{grid-template-columns:1fr}}</style><h1>Red Zoom pilot</h1><p>1024x1024, four steps, seed12345, strength1.1. This generates a close-up; it can invent details. Inspect the images as well as numerical controls.</p><a href="report.json">Full report</a><main>'
        for label, file in files:
            page += f'<figure><figcaption>{label}</figcaption><a href="{file}"><img src="{file}" alt="{label}"></a></figure>'
        (output / "index.html").write_text(page + "</main></html>", encoding="utf-8")
    except Exception:
        report["status"] = "failed"
        raise
    finally:
        save()
    print("OUTPUT_DIR=" + str(output), flush=True)


if __name__ == "__main__":
    main()
