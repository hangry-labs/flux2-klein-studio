"""Measured, GPU-resident text-to-image inference."""

import hashlib
import json
import os
import subprocess
import threading
import time
from datetime import datetime, timezone
from contextlib import nullcontext
from pathlib import Path

from .cli import MODEL_ID, MODEL_REVISION, doctor
from .workloads import fingerprint, jobs, seed_checks, timing_summary

from .model_catalog import KERNEL_REPOSITORY, KERNEL_REVISION


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

    def __init__(self, interval=0.1):
        self.peak_mib = None
        self.error = None
        self.start_mib = None
        self.total_mib = None
        self.minimum_free_mib = None
        self.interval = interval
        self.samples = []
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self):
        while not self.stop_event.is_set():
            try:
                result = subprocess.run(
                    [
                        "nvidia-smi",
                        "--id=0",
                        "--query-gpu=memory.used,memory.total,memory.free",
                        "--format=csv,noheader,nounits",
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=5,
                )
                value, self.total_mib, free = (
                    int(part.strip()) for part in result.stdout.strip().split(",")
                )
                if self.start_mib is None:
                    self.start_mib = value
                self.peak_mib = max(self.peak_mib or 0, value)
                self.minimum_free_mib = (
                    free
                    if self.minimum_free_mib is None
                    else min(self.minimum_free_mib, free)
                )
                self.samples.append(
                    {
                        "monotonic_seconds": time.perf_counter(),
                        "used_mib": value,
                        "free_mib": free,
                    }
                )
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                self.error = str(exc)
                return
            self.stop_event.wait(self.interval)

    def __enter__(self):
        self.thread.start()
        # Establish the pre-stage baseline before model loading or generation.
        deadline = time.perf_counter() + 6
        while (
            self.start_mib is None
            and self.error is None
            and time.perf_counter() < deadline
        ):
            self.stop_event.wait(0.01)
        if self.start_mib is None:
            self.stop_event.set()
            self.thread.join(timeout=6)
            raise RuntimeError(
                f"Cannot sample GPU memory: {self.error or 'no baseline sample'}"
            )
        return self

    def __exit__(self, *_):
        self.stop_event.set()
        self.thread.join(timeout=6)

    def summary(self):
        if self.error:
            raise RuntimeError(f"GPU memory sampling failed: {self.error}")
        return {
            "baseline_mib": self.start_mib,
            "peak_mib": self.peak_mib,
            "minimum_free_mib": self.minimum_free_mib,
            "total_mib": self.total_mib,
            "sample_count": len(self.samples),
            "target_interval_seconds": self.interval,
        }


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


def load_pipeline(arguments, report):
    import sys
    import torch
    from diffusers import Flux2KleinPipeline
    from huggingface_hub import HfApi

    if not arguments.offline:
        actual = (
            HfApi().repo_info(KERNEL_REPOSITORY, repo_type="kernel", revision="v2").sha
        )
        if actual != KERNEL_REVISION:
            raise RuntimeError(f"Kernel v2 moved to {actual}; review before updating")
    pipe = Flux2KleinPipeline.from_pretrained(
        MODEL_ID,
        revision=MODEL_REVISION,
        dtype=torch.bfloat16,
        local_files_only=arguments.offline,
    ).to("cuda")
    if arguments.reduction == "ordered":
        from .ordered_reduction import install_ordered_reduction

        replaced = install_ordered_reduction(pipe.transformer)
        if replaced != 100:
            raise RuntimeError(f"Expected 100 ordered SVDQ modules, got {replaced}")
        report["ordered_modules"] = replaced
    report["vae_tiling"] = getattr(pipe.vae, "use_tiling", None)
    report["vae_slicing"] = getattr(pipe.vae, "use_slicing", None)
    if report["vae_tiling"] or report["vae_slicing"]:
        raise RuntimeError(
            "The selected profile requires VAE tiling and slicing disabled"
        )
    report["placement"] = check_residency(pipe)
    report["quantization"] = check_quantization(pipe)
    paths = sorted(
        {
            str(getattr(module, "__file__", ""))
            for name, module in sys.modules.items()
            if "nunchaku" in name
            and "snapshots" in str(getattr(module, "__file__", ""))
        }
    )
    if not paths or any(KERNEL_REVISION not in path for path in paths):
        raise RuntimeError(f"Cannot verify the loaded kernel snapshot: {paths}")
    report["kernel"] = {
        "repository": KERNEL_REPOSITORY,
        "revision": KERNEL_REVISION,
        "file_sha256": {Path(path).name: file_sha256(path) for path in paths},
    }
    return pipe


def run(arguments):
    # Set offline mode before importing Hub and Diffusers, which cache environment settings.
    if arguments.offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
    import torch
    from PIL import ImageStat, ImageOps
    from PIL.PngImagePlugin import PngInfo

    measure_memory = arguments.command == "memory"
    test_seed = arguments.command == "seed-test"
    measure_speed = arguments.command == "benchmark"
    reference = None
    reference_metadata = None
    if getattr(arguments, "reference_image", None):
        from io import BytesIO
        from PIL import Image
        from .references import normalize_reference

        data, reference_metadata = normalize_reference(
            arguments.reference_image.read_bytes()
        )
        reference = Image.open(BytesIO(data)).convert("RGB")
    selected_jobs = jobs(arguments)
    output = arguments.output_dir / datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )
    output.mkdir(parents=True, exist_ok=False)
    report_path = output / "report.json"
    report = {
        "status": "starting",
        "runs": [],
        "width": arguments.width,
        "height": arguments.height,
        "steps": arguments.steps,
        "guidance_scale": 1.0,
        "seed": arguments.seed,
        "prompt": arguments.prompt,
        "offloading": False,
        "mode": arguments.command,
        "reduction": arguments.reduction,
        "warmups": getattr(arguments, "warmups", 0),
        "measured_calls_requested": getattr(arguments, "runs", 0),
        "workload_sha256": fingerprint(selected_jobs),
        "source_sha256": {
            name: file_sha256(Path(__file__).with_name(name))
            for name in ("inference.py", "ordered_reduction.py", "workloads.py")
            + (("references.py",) if reference_metadata else ())
        },
        "generation_profile": {"per_step_finite_check": True},
    }
    if reference_metadata:
        report["reference"] = reference_metadata
        report["generation_profile"]["mode"] = "single-reference"
        report["generation_profile"]["reference"] = reference_metadata
        report["workload_sha256"] = fingerprint(
            {"jobs": selected_jobs, "reference": reference_metadata}
        )
    write_json(report_path, report)
    sampler = None
    try:
        report["environment"] = doctor()
        with DeviceMemorySampler() if measure_memory else nullcontext() as sampler:
            if measure_memory:
                torch.cuda.reset_peak_memory_stats(0)
            torch.cuda.synchronize()
            start = time.perf_counter()
            pipe = load_pipeline(arguments, report)
            torch.cuda.synchronize()
            report["loading_seconds"] = time.perf_counter() - start
            if measure_memory:
                report["loading_memory"] = memory()
        if measure_memory:
            report["loading_device_memory"] = sampler.summary()
            report["loading_device_samples"] = sampler.samples
        report["status"] = "loaded"
        write_json(report_path, report)
        for job in selected_jobs:
            report["active_call"] = job["label"]
            write_json(report_path, report)
            with DeviceMemorySampler() if measure_memory else nullcontext() as sampler:
                if measure_memory:
                    torch.cuda.reset_peak_memory_stats(0)
                torch.cuda.synchronize()
                start = time.perf_counter()
                with torch.inference_mode():
                    image = pipe(
                        prompt=job["prompt"],
                        width=arguments.width,
                        height=arguments.height,
                        num_inference_steps=arguments.steps,
                        guidance_scale=1.0,
                        generator=torch.Generator("cuda").manual_seed(job["seed"]),
                        callback_on_step_end=check_step,
                        **(
                            {
                                "image": ImageOps.mirror(reference)
                                if job.get("reference_variant") == "mirror"
                                else reference
                            }
                            if reference is not None
                            else {}
                        ),
                    ).images[0]
                torch.cuda.synchronize()
                seconds = time.perf_counter() - start
                record = {
                    **job,
                    "pixel_sha256": hashlib.sha256(image.tobytes()).hexdigest(),
                }
                if measure_speed or arguments.command == "generate":
                    record["seconds"] = seconds
                if measure_memory:
                    record["memory"] = memory()
            if measure_memory:
                record["device_memory"] = sampler.summary()
                record["device_samples"] = sampler.samples
            if (
                image.size != (arguments.width, arguments.height)
                or max(ImageStat.Stat(image).stddev) < 1
            ):
                raise RuntimeError("Output has an unexpected size or appears blank")
            if arguments.command == "generate" or test_seed or arguments.save_images:
                metadata = PngInfo()
                metadata.add_text(
                    "generation",
                    json.dumps(
                        {
                            **job,
                            "model": MODEL_ID,
                            "revision": MODEL_REVISION,
                            "width": arguments.width,
                            "height": arguments.height,
                            "steps": arguments.steps,
                            "guidance_scale": 1.0,
                            "reduction": arguments.reduction,
                            **(
                                {"reference": reference_metadata}
                                if reference_metadata
                                else {}
                            ),
                        }
                    ),
                )
                image.save(output / f"{job['label']}.png", pnginfo=metadata)
            report["runs"].append(record)
            report["placement_after_generation"] = check_residency(pipe)
            write_json(report_path, report)
            print(
                json.dumps({k: v for k, v in record.items() if k != "device_samples"}),
                flush=True,
            )
        if measure_speed:
            report["timing"] = timing_summary(
                [
                    r["seconds"]
                    for r in report["runs"]
                    if r["label"].startswith("measured-")
                ]
            )
        if test_seed:
            report["seed_checks"] = seed_checks(report["runs"])
            if not all(
                value
                for key, value in report["seed_checks"].items()
                if key != "reference_pixel_sha256"
            ):
                raise RuntimeError(
                    "Fixed-seed repeatability or changed-input control failed"
                )
        report["status"] = "passed"
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = f"{type(exc).__name__}: {exc}"
        if measure_memory and torch.cuda.is_initialized():
            try:
                report["failure_memory"] = memory()
            except RuntimeError as memory_exc:
                report["failure_memory_error"] = str(memory_exc)
        if measure_memory and sampler is not None:
            report["failure_device_samples"] = sampler.samples
        raise
    finally:
        write_json(report_path, report)
        print(f"Report: {report_path}", flush=True)
