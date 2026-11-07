"""Local green-border/interior-paint pilot; visual review is required."""

import argparse
import hashlib
import html
from io import BytesIO
import json
from pathlib import Path
import time

import torch
from PIL import Image, ImageDraw, ImageOps
from PIL.PngImagePlugin import PngInfo

from flux2_klein_studio.adapters import OUTPAINT_PROMPT
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument(
        "--output-dir", type=Path, default=Path("/data/outputs/lora-outpaint")
    )
    args = parser.parse_args()
    output = args.output_dir / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    output.mkdir(parents=True, exist_ok=False)
    source_bytes, source_meta = normalize_reference(args.reference.read_bytes())
    with Image.open(BytesIO(source_bytes)) as image:
        source = ImageOps.fit(image.convert("RGB"), (1024, 1024))
    border = Image.new("RGB", (1024, 1024), (0, 255, 0))
    border.paste(source.resize((768, 768)), (128, 128))
    interior = source.copy()
    ImageDraw.Draw(interior).rectangle((320, 384, 704, 896), fill=(0, 255, 0))
    references = {"border": border, "interior": interior}
    for name, image in references.items():
        image.save(output / (name + "-input.png"))
    report = {
        "status": "running",
        "scope": "1024 visual editing pilot and allocator diagnostics, not a performance benchmark.",
        "source_reference": source_meta,
        "width": 1024,
        "height": 1024,
        "seed": 12345,
        "steps": 4,
        "runs": [],
    }

    def save():
        (output / "report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )

    save()
    hashes = {}
    try:
        report["environment"] = doctor()
        pipe = load_pipeline(
            argparse.Namespace(offline=args.offline, reduction="ordered"), report
        )
        for profile in ("base", "outpaint", "custom-instruction", "repeat", "removed"):
            if profile == "outpaint":
                torch.cuda.empty_cache()
                before = torch.cuda.memory_allocated()
                report["adapter"] = load_adapter(
                    pipe.transformer, "outpaint", offline=args.offline
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
            for name in (
                references
                if profile in ("base", "outpaint")
                else ["interior"]
                if profile == "custom-instruction"
                else ["border"]
            ):
                prompt = (
                    OUTPAINT_PROMPT
                    if profile != "custom-instruction"
                    else OUTPAINT_PROMPT
                    + ". Fill the marked area with a large purple orchid in a terracotta pot."
                )
                torch.cuda.reset_peak_memory_stats()
                with torch.inference_mode():
                    image = pipe(
                        prompt=prompt,
                        image=references[name],
                        width=1024,
                        height=1024,
                        num_inference_steps=4,
                        guidance_scale=1.0,
                        generator=torch.Generator("cuda").manual_seed(12345),
                        callback_on_step_end=check_step,
                    ).images[0]
                torch.cuda.synchronize()
                digest = hashlib.sha256(image.tobytes()).hexdigest()
                row = {
                    "profile": profile,
                    "reference": name,
                    "prompt": prompt,
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
                            "adapter": report.get("adapter")
                            if profile not in ("base", "removed")
                            else None,
                        }
                    ),
                )
                image.save(output / row["image"], pnginfo=info)
                report["runs"].append(row)
                hashes[profile, name] = digest
                save()
                if profile == "repeat":
                    assert digest == hashes["outpaint", name]
                if profile == "removed":
                    assert digest == hashes["base", name]
                print(json.dumps(row), flush=True)
        report["status"] = "numerical_checks_passed_visual_review_required"
        rows = []
        for name in references:
            figures = []
            for suffix in ("input", "base", "outpaint"):
                file = f"{name}-{suffix}.png"
                figures.append(
                    f'<figure><figcaption>{html.escape(suffix)}</figcaption><a href="{file}"><img src="{file}" alt="{name} {suffix}"></a></figure>'
                )
            rows.append(
                "<h2>" + name + '</h2><div class="grid">' + "".join(figures) + "</div>"
            )
        rows.append(
            '<h2>Experimental custom instruction</h2><img width="512" src="interior-custom-instruction.png" alt="Custom instruction result">'
        )
        page = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Green Fill outpaint pilot</title><style>body{font:16px system-ui;background:#18191c;color:#eee;margin:24px}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}figure{margin:0}img{width:100%;max-width:1024px}a{color:#ff984d}figcaption{padding:8px}@media(max-width:700px){.grid{grid-template-columns:1fr}}</style><h1>Green Fill outpaint pilot</h1><p>1024x1024, 4 steps, seed 12345. Recommended prompt, border and interior marks; custom instruction is exploratory. This is image conditioning, not a pixel-preserving compositing mask.</p><p><a href="report.json">Full report</a></p>'
        (output / "index.html").write_text(
            page + "".join(rows) + "</html>", encoding="utf-8"
        )
    finally:
        save()
    print("OUTPUT_DIR=" + str(output), flush=True)


if __name__ == "__main__":
    main()
