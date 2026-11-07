"""Resident inference worker. The HTTP process never imports CUDA libraries."""

import os
import time
from types import SimpleNamespace

from .build_info import offline_default


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
    while True:
        try:
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
                torch.cuda.synchronize()
                torch.cuda.empty_cache()
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
                torch.cuda.empty_cache()
                if selected_adapter:
                    report["lora"] = load_adapter(
                        pipe.transformer, selected_adapter, offline=offline_default()
                    )
                current_adapter = selected_adapter
                report["quantization"] = check_quantization(pipe)
                report["placement"] = check_residency(pipe)
            if settings.get("_reference_path"):
                with Image.open(settings["_reference_path"]) as source:
                    reference = source.convert("RGB")
                import hashlib

                if (
                    hashlib.sha256(reference.tobytes()).hexdigest()
                    != settings["reference"]["pixel_sha256"]
                ):
                    raise RuntimeError("Reference image does not match its recipe")
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
