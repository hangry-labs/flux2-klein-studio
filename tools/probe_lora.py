"""Generate matched 1024px LoRA comparisons and numerical controls.

Memory figures are pilot PyTorch counters, not a memory/speed benchmark suite.
Pictures require visual review; numerical success does not establish image quality.
"""

import argparse
import gc
import hashlib
import html
import json
from pathlib import Path
import time

import torch
from PIL import ImageStat
from PIL.PngImagePlugin import PngInfo

from flux2_klein_studio.inference import (
    check_quantization,
    check_residency,
    check_step,
    load_pipeline,
    memory,
    write_json,
)
from flux2_klein_studio.lora import AdditiveLoRALinear, load_pixel_art, remove_lora
from flux2_klein_studio.cli import doctor

PROMPTS = [
    "pixel art sprite, a brave knight in shining armor holding a sword and shield, full body, game asset, plain white background, 16-bit pixel art",
    "pixel art sprite, a cute orange robot companion with glowing blue eyes, full body, game asset, plain white background, 16-bit pixel art",
    "pixel art sprite, a fire-breathing dragon with red scales and spread wings, game asset, plain white background, 16-bit pixel art",
]


def gallery(output, report):
    rows = []
    for index, prompt in enumerate(PROMPTS, 1):
        rows.append(
            f"<h2>Seed {12344 + index}</h2><p>{html.escape(prompt)}</p>"
            f'<div class="pair"><figure><figcaption>Base model</figcaption><a href="{index:02}-base.png"><img src="{index:02}-base.png" alt="Base result"></a></figure>'
            f'<figure><figcaption>Pixel-art LoRA, strength 1</figcaption><a href="{index:02}-pixel-art.png"><img src="{index:02}-pixel-art.png" alt="LoRA result"></a></figure></div>'
        )
    text = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Pixel-art LoRA comparisons</title><style>body{font:16px system-ui;background:#111214;color:#f3f3f4;max-width:1300px;margin:auto;padding:24px}p{line-height:1.6}.pair{display:grid;grid-template-columns:1fr 1fr;gap:20px}figure{margin:0}figcaption{padding:10px 0}img{width:100%;display:block}a{color:#ff984d}@media(max-width:600px){.pair{grid-template-columns:1fr}}</style><h1>Pixel-art LoRA comparisons</h1><p>1024×1024 · 4 steps · matched prompts and seeds · ordered NVFP4 + NF4 · no offloading</p><p>The prompt itself already asks for pixel art. Compare how the adapter changes the result; this is not a claim of universal quality improvement.</p>'
    text += f'<p>Adapter weight allocation: {report["adapter_allocation_delta_bytes"] / 1024**2:.2f} MiB. <a href="report.json">Full diagnostic report</a></p>'
    (output / "index.html").write_text(
        text + "".join(rows) + "</html>", encoding="utf-8"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument(
        "--output-dir", type=Path, default=Path("/data/outputs/lora-pixel-art")
    )
    args = parser.parse_args()
    output = args.output_dir / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "status": "starting",
        "visual_review": "required",
        "width": 1024,
        "height": 1024,
        "steps": 4,
        "reduction": "ordered",
        "runs": [],
        "scope": "Shared-system visual pilot and PyTorch memory counters; not a speed benchmark",
    }
    path = output / "report.json"
    write_json(path, report)
    hashes = {}
    try:
        report["environment"] = doctor()
        pipe = load_pipeline(
            argparse.Namespace(offline=args.offline, reduction="ordered"), report
        )
        report["base_loaded_memory"] = memory()
        for profile in (
            "base",
            "pixel-art",
            "pixel-art-repeat",
            "zero-strength",
            "removed",
        ):
            if profile == "pixel-art":
                torch.cuda.empty_cache()
                before = torch.cuda.memory_allocated()
                report["adapter"] = load_pixel_art(
                    pipe.transformer, offline=args.offline
                )
                torch.cuda.synchronize()
                report["adapter_allocation_delta_bytes"] = (
                    torch.cuda.memory_allocated() - before
                )
                report["adapter_loaded_memory"] = memory()
            elif profile == "zero-strength":
                for module in pipe.transformer.modules():
                    if isinstance(module, AdditiveLoRALinear):
                        module.strength = 0
            elif profile == "removed":
                remove_lora(pipe.transformer)
                gc.collect()
                torch.cuda.empty_cache()
            report.setdefault("profiles", {})[profile] = {
                "quantization": check_quantization(pipe),
                "placement": check_residency(pipe),
            }
            for index, prompt in enumerate(
                PROMPTS if profile in ("base", "pixel-art") else PROMPTS[:1], 1
            ):
                seed = 12344 + index
                torch.cuda.reset_peak_memory_stats()
                with torch.inference_mode():
                    image = pipe(
                        prompt=prompt,
                        width=1024,
                        height=1024,
                        num_inference_steps=4,
                        guidance_scale=1.0,
                        generator=torch.Generator("cuda").manual_seed(seed),
                        callback_on_step_end=check_step,
                    ).images[0]
                torch.cuda.synchronize()
                pixel_hash = hashlib.sha256(image.tobytes()).hexdigest()
                hashes[profile, index] = pixel_hash
                row = {
                    "profile": profile,
                    "prompt": prompt,
                    "seed": seed,
                    "pixel_sha256": pixel_hash,
                    "memory": memory(),
                    "image": f"{index:02}-{profile}.png",
                }
                info = PngInfo()
                info.add_text(
                    "generation",
                    json.dumps(
                        {
                            **row,
                            "adapter": report.get("adapter")
                            if profile.startswith("pixel-art")
                            else None,
                        }
                    ),
                )
                image.save(output / row["image"], pnginfo=info)
                report["runs"].append(row)
                write_json(path, report)
                if max(ImageStat.Stat(image).stddev) < 1:
                    raise RuntimeError("Blank image")
                if (
                    profile in ("zero-strength", "removed")
                    and pixel_hash != hashes["base", index]
                ):
                    raise RuntimeError(f"{profile} differs from base pixels")
                if profile == "pixel-art" and pixel_hash == hashes["base", index]:
                    raise RuntimeError("Adapter had no effect")
                if (
                    profile == "pixel-art-repeat"
                    and pixel_hash != hashes["pixel-art", index]
                ):
                    raise RuntimeError("Adapter repeat changed pixels")
                print(json.dumps(row), flush=True)
        report["status"] = "numerical_checks_passed"
        gallery(output, report)
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        write_json(path, report)


if __name__ == "__main__":
    main()
