"""Measured, GPU-resident inference for the initial 1024 square profile."""

import hashlib
import json
import os
import statistics
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .cli import MODEL_ID, MODEL_REVISION, doctor

KERNEL_REPOSITORY = "rootonchair/nunchaku-lite-kernels"
KERNEL_REVISION = "d04512b422d7c9d029ae760449687fb09f33bf58"


def check_residency(pipe):
    """Reject host weights, meta tensors, spill device maps and offload hooks."""
    import torch

    placement = {}
    for name, component in pipe.components.items():
        if not isinstance(component, torch.nn.Module):
            continue
        devices = {
            str(t.device) for t in (*component.parameters(), *component.buffers())
        }
        if any(torch.device(device).type != "cuda" for device in devices):
            raise RuntimeError(f"{name} has non-CUDA tensors: {sorted(devices)}")
        device_map = getattr(component, "hf_device_map", None) or {}
        if any(
            str(value) not in ("0", "cuda", "cuda:0") for value in device_map.values()
        ):
            raise RuntimeError(
                f"{name} has a CPU/disk or unexpected device map: {device_map}"
            )
        for module in component.modules():
            hooks = [getattr(module, "_hf_hook", None)]
            while hooks:
                hook = hooks.pop()
                if (
                    getattr(hook, "offload", False)
                    or "offload" in type(hook).__name__.lower()
                ):
                    raise RuntimeError(f"{name} has an active model offload hook")
                hooks.extend(getattr(hook, "hooks", ()))
        placement[name] = {
            "class": type(component).__name__,
            "devices": sorted(devices),
            "device_map": device_map,
        }
    return placement


def check_quantization(pipe):
    config = pipe.transformer.config.quantization_config
    if hasattr(config, "to_dict"):
        config = config.to_dict()
    svdq = config.get("svdq_w4a4", {})
    if (config.get("quant_method"), svdq.get("precision"), svdq.get("rank")) != (
        "nunchaku_lite",
        "nvfp4",
        32,
    ):
        raise RuntimeError(
            "Transformer does not match the required Nunchaku NVFP4 rank-32 profile"
        )
    encoder = pipe.text_encoder.config.quantization_config
    if hasattr(encoder, "to_dict"):
        encoder = encoder.to_dict()
    if not encoder.get("load_in_4bit") or encoder.get("bnb_4bit_quant_type") != "nf4":
        raise RuntimeError(
            "The text encoder is not the required bitsandbytes NF4 profile"
        )
    quantized = [
        module
        for module in pipe.transformer.modules()
        if type(module).__name__ == "SVDQW4A4Linear"
    ]
    nf4 = [
        module
        for module in pipe.text_encoder.modules()
        if type(module).__name__ == "Linear4bit"
    ]
    if len(quantized) != len(svdq.get("targets", [])) or not nf4:
        raise RuntimeError("Expected quantized projection modules are missing")
    if any(module.rank != 32 or module.precision != "nvfp4" for module in quantized):
        raise RuntimeError(
            "Loaded transformer projection precision or rank does not match"
        )
    return {
        "transformer": config,
        "encoder": encoder,
        "svdq_modules": len(quantized),
        "nf4_modules": len(nf4),
    }


class DeviceMemorySampler:
    """Sample whole-device usage; this includes the desktop and other processes."""

    def __init__(self):
        self.peak_mib = None
        self.error = None
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self):
        while not self.stop_event.is_set():
            try:
                result = subprocess.run(
                    [
                        "nvidia-smi",
                        "--id=0",
                        "--query-gpu=memory.used",
                        "--format=csv,noheader,nounits",
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=5,
                )
                value = int(result.stdout.strip())
                self.peak_mib = max(self.peak_mib or 0, value)
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                self.error = str(exc)
                return
            self.stop_event.wait(0.5)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop_event.set()
        self.thread.join(timeout=6)


def memory():
    import torch

    return {
        "allocated_peak_bytes": torch.cuda.max_memory_allocated(0),
        "reserved_peak_bytes": torch.cuda.max_memory_reserved(0),
        "allocated_bytes": torch.cuda.memory_allocated(0),
        "reserved_bytes": torch.cuda.memory_reserved(0),
    }


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def file_sha256(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def check_step(_pipe, _index, _timestep, tensors):
    import torch

    if not torch.isfinite(tensors["latents"]).all().item():
        raise RuntimeError("Denoising produced non-finite image latents")
    return tensors


def run(arguments):
    # Set offline mode before importing Hub and Diffusers, which cache environment settings.
    if arguments.offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
    import torch
    from PIL import ImageChops, ImageStat
    from PIL.PngImagePlugin import PngInfo

    output = arguments.output_dir / datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )
    output.mkdir(parents=True, exist_ok=False)
    report_path = output / "report.json"
    report = {
        "status": "starting",
        "runs": [],
        "width": 1024,
        "height": 1024,
        "steps": arguments.steps,
        "guidance_scale": 1.0,
        "seed": arguments.seed,
        "prompt": arguments.prompt,
        "offloading": False,
        "mode": arguments.command,
        "reduction": arguments.reduction,
    }
    write_json(report_path, report)
    try:
        report["environment"] = doctor()
        from huggingface_hub import HfApi

        if not arguments.offline:
            actual = (
                HfApi()
                .repo_info(KERNEL_REPOSITORY, repo_type="kernel", revision="v2")
                .sha
            )
            if actual != KERNEL_REVISION:
                raise RuntimeError(
                    f"Kernel v2 moved to {actual}; review it before updating the pinned revision"
                )
        from diffusers import Flux2KleinPipeline

        with DeviceMemorySampler() as sampler:
            start = time.perf_counter()
            torch.cuda.reset_peak_memory_stats(0)
            pipe = Flux2KleinPipeline.from_pretrained(
                MODEL_ID,
                revision=MODEL_REVISION,
                dtype=torch.bfloat16,
                local_files_only=arguments.offline,
            ).to("cuda")
            torch.cuda.synchronize()
            report["loading_seconds"] = time.perf_counter() - start
            if arguments.reduction == "ordered":
                from .ordered_reduction import install_ordered_reduction

                replaced = install_ordered_reduction(pipe.transformer)
                if replaced != 100:
                    raise RuntimeError(
                        f"Expected 100 ordered SVDQ modules, got {replaced}"
                    )
                report["ordered_modules"] = replaced
            report["loading_memory"] = memory()
            report["placement"] = check_residency(pipe)
            report["quantization"] = check_quantization(pipe)
            # The loaded extension must come from the exact reviewed Hub snapshot.
            import sys

            kernel_paths = sorted(
                {
                    str(getattr(module, "__file__", ""))
                    for name, module in sys.modules.items()
                    if "nunchaku" in name
                    and "snapshots" in str(getattr(module, "__file__", ""))
                }
            )
            if not kernel_paths or any(
                KERNEL_REVISION not in path for path in kernel_paths
            ):
                raise RuntimeError(
                    f"Cannot verify the loaded kernel snapshot: {kernel_paths}"
                )
            report["kernel"] = {
                "repository": KERNEL_REPOSITORY,
                "revision": KERNEL_REVISION,
                "paths": kernel_paths,
                "file_sha256": {path: file_sha256(path) for path in kernel_paths},
            }
            report["status"] = "loaded"
            write_json(report_path, report)
            previous = None
            jobs = [("generate", arguments.prompt)]
            if arguments.command == "benchmark":
                jobs = [("warmup", arguments.prompt)]
                jobs += [
                    (f"measured-{i + 1}", arguments.prompt)
                    for i in range(arguments.runs)
                ]
                jobs += [
                    (
                        "changed-prompt",
                        "A small orange sailboat on a calm mountain lake at sunrise, realistic photograph",
                    )
                ]
            for label, prompt in jobs:
                torch.cuda.reset_peak_memory_stats(0)
                torch.cuda.synchronize()
                start = time.perf_counter()
                with torch.inference_mode():
                    image = pipe(
                        prompt=prompt,
                        width=1024,
                        height=1024,
                        num_inference_steps=arguments.steps,
                        guidance_scale=1.0,
                        generator=torch.Generator("cuda").manual_seed(arguments.seed),
                        callback_on_step_end=check_step,
                    ).images[0]
                torch.cuda.synchronize()
                seconds = time.perf_counter() - start
                if image.size != (1024, 1024) or max(ImageStat.Stat(image).stddev) < 1:
                    raise RuntimeError("Output has an unexpected size or appears blank")
                record = {
                    "label": label,
                    "prompt": prompt,
                    "seed": arguments.seed,
                    "seconds": seconds,
                    "memory": memory(),
                    "pixel_sha256": hashlib.sha256(image.tobytes()).hexdigest(),
                }
                if previous is not None and label.startswith("measured-"):
                    record["same_seed_pixel_mae"] = statistics.mean(
                        ImageStat.Stat(ImageChops.difference(previous, image)).mean
                    )
                if label == "warmup":
                    previous = image.copy()
                if label == "changed-prompt" and previous is not None:
                    record["changed_prompt_pixel_mae"] = statistics.mean(
                        ImageStat.Stat(ImageChops.difference(previous, image)).mean
                    )
                    if record["changed_prompt_pixel_mae"] < 1:
                        raise RuntimeError(
                            "Changing the prompt did not materially change the image"
                        )
                metadata = PngInfo()
                metadata.add_text(
                    "generation",
                    json.dumps(
                        {
                            **record,
                            "model": MODEL_ID,
                            "revision": MODEL_REVISION,
                            "width": 1024,
                            "height": 1024,
                            "steps": arguments.steps,
                            "guidance_scale": 1.0,
                            "reduction": arguments.reduction,
                        }
                    ),
                )
                image.save(output / f"{label}.png", pnginfo=metadata)
                report["runs"].append(record)
                report["placement_after_generation"] = check_residency(pipe)
                write_json(report_path, report)
                print(json.dumps(record), flush=True)
            report["sampled_device_peak_mib"] = sampler.peak_mib
            report["device_sampling_error"] = sampler.error
            measured = [
                item["seconds"]
                for item in report["runs"]
                if item["label"].startswith("measured-")
            ]
            if measured:
                report["measured_mean_seconds"] = statistics.mean(measured)
                report["measured_stdev_seconds"] = statistics.pstdev(measured)
                reference_hash = report["runs"][0]["pixel_sha256"]
                report["same_seed_bit_exact"] = all(
                    item["pixel_sha256"] == reference_hash
                    for item in report["runs"]
                    if item["label"].startswith("measured-")
                )
                if not report["same_seed_bit_exact"]:
                    if arguments.reduction == "ordered":
                        raise RuntimeError(
                            "Ordered accumulation failed the same-seed pixel equality check"
                        )
                    report["repeatability_note"] = (
                        "Repeated same-seed calls differ. The current SVDQ CUDA projection "
                        "was observed to vary for identical input. Exact image reproduction "
                        "is not guaranteed by this runtime profile."
                    )
                    print(report["repeatability_note"], flush=True)
        report["status"] = "passed"
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        write_json(report_path, report)
        print(f"Report: {report_path}", flush=True)
