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
from flux2_klein_studio.lora import AdditiveLoRALinear, load_adapter, remove_lora
from flux2_klein_studio.adapters import ADAPTERS
from flux2_klein_studio.cli import doctor

PROMPTS = [
    "pixel art sprite, a brave knight in shining armor holding a sword and shield, full body, game asset, plain white background, 16-bit pixel art",
    "pixel art sprite, a cute orange robot companion with glowing blue eyes, full body, game asset, plain white background, 16-bit pixel art",
    "pixel art sprite, a fire-breathing dragon with red scales and spread wings, game asset, plain white background, 16-bit pixel art",
]

KONI_PROMPTS = [
    "anime_style, a young adult woman with long light blue wavy hair and emerald green eyes, wearing a white short-sleeved shirt and navy blue pleated skirt, sitting on grass beneath a leafy tree, relaxed hands resting on her knees, dappled sunlight, peaceful expression, lush park in the background",
    "anime_style, a tiny orange robot tending orchids inside a sunlit glass greenhouse, cinematic composition, lush leaves and detailed glass reflections",
    "anime_style, an adult traveler with a red backpack standing in a golden field, looking toward a colossal futuristic ring above a distant hill town, blue sky and soft clouds, wide environmental illustration",
]


NASA_PROMPTS = [
    "NASA_CONCEPT retro-futuristic space concept art of a rotating wheel space station above the Moon, large circular habitat with spokes and a central hub, docked spacecraft, painted technical illustration",
    "NASA_CONCEPT artist depiction of a space colony, mid-century modern buildings under a glass dome, lush plants and waterfalls, a monorail in the distance, retro-futuristic science fiction illustration",
    "NASA_CONCEPT detailed cross-section of a lunar exploration spacecraft, 1960s style technical drawing, cutaway view revealing crew cabins, life support systems and scientific instruments",
]


def gallery(output, report, prompts):
    rows = []
    adapter = report["adapter_id"]
    label = html.escape(ADAPTERS[adapter]["label"])
    for index, prompt in enumerate(prompts, 1):
        rows.append(
            f"<h2>Seed {12344 + index}</h2><p>{html.escape(prompt)}</p>"
            f'<div class="pair"><figure><figcaption>Base model</figcaption><a href="{index:02}-base.png"><img src="{index:02}-base.png" alt="Base result"></a></figure>'
            f'<figure><figcaption>{label}, strength {report["adapter"]["strength"]:g}</figcaption><a href="{index:02}-{adapter}.png"><img src="{index:02}-{adapter}.png" alt="LoRA result"></a></figure></div>'
        )
    text = (
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{label} comparisons</title>"
        "<style>body{font:16px system-ui;background:#111214;color:#f3f3f4;max-width:1300px;margin:auto;padding:24px}"
        "p{line-height:1.6}.pair{display:grid;grid-template-columns:1fr 1fr;gap:20px}figure{margin:0}"
        "figcaption{padding:10px 0}img{width:100%;display:block}a{color:#ff984d}"
        "@media(max-width:600px){.pair{grid-template-columns:1fr}}</style>"
        f"<h1>{label} comparisons</h1>"
        "<p>1024×1024 · 4 steps · matched prompts and seeds · ordered NVFP4 + NF4 · no offloading</p>"
        "<p>Both images use the same prompt, including style trigger words. Compare how the adapter changes the result; "
        "this is not a claim of universal quality improvement.</p>"
    )
    text += f'<p>Adapter weight allocation: {report["adapter_allocation_delta_bytes"] / 1024**2:.2f} MiB. <a href="report.json">Full diagnostic report</a></p>'
    (output / "index.html").write_text(
        text + "".join(rows) + "</html>", encoding="utf-8"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--adapter",
        choices=tuple(
            key for key, profile in ADAPTERS.items() if profile.get("mode") != "edit"
        ),
        default="pixel-art",
    )
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    prompts = {
        "pixel-art": PROMPTS,
        "koni-anime": KONI_PROMPTS,
        "nasa-concept": NASA_PROMPTS,
    }[args.adapter]
    output = (
        args.output_dir or Path(f"/app/persistent/outputs/lora-{args.adapter}")
    ) / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "status": "starting",
        "adapter_id": args.adapter,
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
            args.adapter,
            args.adapter + "-repeat",
            "zero-strength",
            "removed",
        ):
            if profile == args.adapter:
                torch.cuda.empty_cache()
                before = torch.cuda.memory_allocated()
                report["adapter"] = load_adapter(
                    pipe.transformer, args.adapter, offline=args.offline
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
                prompts if profile in ("base", args.adapter) else prompts[:1], 1
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
                            if profile.startswith(args.adapter)
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
                if profile == args.adapter and pixel_hash == hashes["base", index]:
                    raise RuntimeError("Adapter had no effect")
                if (
                    profile == args.adapter + "-repeat"
                    and pixel_hash != hashes[args.adapter, index]
                ):
                    raise RuntimeError("Adapter repeat changed pixels")
                print(json.dumps(row), flush=True)
        report["status"] = "numerical_checks_passed"
        gallery(output, report, prompts)
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        write_json(path, report)


if __name__ == "__main__":
    main()
