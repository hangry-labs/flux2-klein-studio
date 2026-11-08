"""Resident inference worker. The HTTP process never imports CUDA libraries."""

import os
import time
from types import SimpleNamespace

from .build_info import offline_default

WORKSPACE_IDLE_SECONDS = 1.0


def trim_workspace(torch, report, reason):
    """Return unused CUDA blocks without releasing live model/adapter tensors."""
    start = time.perf_counter()
    torch.cuda.synchronize()
    before = {
        "allocated_bytes": torch.cuda.memory_allocated(),
        "reserved_bytes": torch.cuda.memory_reserved(),
    }
    torch.cuda.empty_cache()
    report["last_workspace_trim"] = {
        "reason": reason,
        "before": before,
        "after": {
            "allocated_bytes": torch.cuda.memory_allocated(),
            "reserved_bytes": torch.cuda.memory_reserved(),
        },
        "seconds": time.perf_counter() - start,
    }


def serve(connection):
    os.environ.setdefault(
        "PYTORCH_ALLOC_CONF",
        os.getenv("PYTORCH_CUDA_ALLOC_CONF") or "expandable_segments:True",
    )
    if offline_default():
        os.environ["HF_HUB_OFFLINE"] = "1"
    import torch
    from PIL import Image, ImageStat

    from .cli import MODEL_ID, MODEL_REVISION, doctor
    from .inference import (
        check_residency,
        check_quantization,
        check_step,
        load_pipeline,
    )
    from .adapters import ADAPTERS, compose_prompt

    pipe = None
    current_adapter = None
    report = {}
    cache_dirty = False
    previous_workload = 0
    while True:
        try:
            # Queued work reuses the allocator. Return spare blocks once the
            # worker is quiet, without restarting the model's idle-unload timer.
            if cache_dirty and not connection.poll(WORKSPACE_IDLE_SECONDS):
                trim_workspace(torch, report, "idle")
                cache_dirty = False
            settings = connection.recv()
        except EOFError:
            break
        try:
            if pipe is None:
                connection.send({"phase": "loading"})
                report["environment"] = doctor()
                pipe = load_pipeline(
                    SimpleNamespace(offline=offline_default(), reduction="ordered"),
                    report,
                )
            if settings.get("_operation") in ("load", "trim"):
                # Lifecycle commands do not denoise, decode, or create outputs.
                # Release unused allocator blocks while keeping model tensors.
                check_residency(pipe)
                trim_workspace(torch, report, settings["_operation"])
                cache_dirty = False
                connection.send({"phase": "completed", "report": report})
                continue
            reference = None
            selected_adapter = settings.get("adapter")
            effective_prompt = compose_prompt(settings["prompt"], selected_adapter)
            if selected_adapter and (
                settings.get("adapter_profile") != ADAPTERS[selected_adapter]
                or settings.get("effective_prompt") != effective_prompt
            ):
                raise RuntimeError(
                    "Adapter prompt/identity does not match its job snapshot"
                )
            if selected_adapter != current_adapter:
                from .lora import remove_lora, load_adapter

                connection.send({"phase": "loading"})
                remove_lora(pipe.transformer)
                report.pop("lora", None)
                trim_workspace(torch, report, "adapter-change")
                cache_dirty = False
                if selected_adapter:
                    report["lora"] = load_adapter(
                        pipe.transformer, selected_adapter, offline=offline_default()
                    )
                current_adapter = selected_adapter
                report["quantization"] = check_quantization(pipe)
                report["placement"] = check_residency(pipe)
            workload = settings["width"] * settings["height"]
            if settings.get("reference"):
                workload += sum(
                    ref["width"] * ref["height"]
                    for ref in settings.get("references", [settings["reference"]])
                )
            if cache_dirty and workload < previous_workload:
                trim_workspace(torch, report, "smaller-workload")
                cache_dirty = False
            if settings.get("_reference_path"):
                import hashlib

                prepared = []
                for path, metadata in zip(
                    settings.get("_reference_paths", [settings["_reference_path"]]),
                    settings.get("references", [settings["reference"]]),
                    strict=True,
                ):
                    with Image.open(path) as source:
                        image = source.convert("RGB")
                    if (
                        hashlib.sha256(image.tobytes()).hexdigest()
                        != metadata["pixel_sha256"]
                    ):
                        raise RuntimeError("Reference image does not match its recipe")
                    prepared.append(image)
                reference = prepared[0] if len(prepared) == 1 else prepared
            connection.send({"phase": "generating"})
            torch.cuda.synchronize()
            start = time.perf_counter()
            with torch.inference_mode():
                image = pipe(
                    prompt=effective_prompt,
                    width=settings["width"],
                    height=settings["height"],
                    num_inference_steps=settings["steps"],
                    guidance_scale=1.0,
                    generator=torch.Generator("cuda").manual_seed(settings["seed"]),
                    callback_on_step_end=check_step,
                    **({"image": reference} if reference is not None else {}),
                ).images[0]
            torch.cuda.synchronize()
            seconds = time.perf_counter() - start
            if (
                image.size != (settings["width"], settings["height"])
                or max(ImageStat.Stat(image).stddev) < 1
            ):
                raise RuntimeError("Generation produced an invalid image")
            check_residency(pipe)
            report["placement_after_generation"] = check_residency(pipe)
            report["allocator_after_generation"] = {
                "allocated_bytes": torch.cuda.memory_allocated(),
                "reserved_bytes": torch.cuda.memory_reserved(),
            }
            recipe = {
                **{
                    key: value
                    for key, value in settings.items()
                    if not key.startswith("_")
                },
                "model": MODEL_ID,
                "revision": MODEL_REVISION,
                "reduction": "ordered",
                "guidance_scale": 1.0,
                **({"lora": report["lora"]} if current_adapter else {}),
            }
            cache_dirty = True
            previous_workload = workload
            connection.send(
                {
                    "phase": "completed",
                    "pixels": image.tobytes(),
                    "size": image.size,
                    "recipe": recipe,
                    "seconds": seconds,
                    "report": report,
                }
            )
        except Exception:
            # A failed CUDA context is discarded, never reused for another request.
            import traceback

            traceback.print_exc()
            connection.send(
                {
                    "phase": "failed",
                    "error": "Generation failed. Check service logs; a smaller canvas may need less VRAM.",
                }
            )
            break
    connection.close()
