"""Resident inference worker. The HTTP process never imports CUDA libraries."""

import hashlib
import io
import os
import time
from types import SimpleNamespace


def serve(connection):
    os.environ.setdefault(
        "PYTORCH_ALLOC_CONF",
        os.getenv("PYTORCH_CUDA_ALLOC_CONF") or "expandable_segments:True",
    )
    if os.getenv("STUDIO_OFFLINE") == "1":
        os.environ["HF_HUB_OFFLINE"] = "1"
    import torch
    from PIL import ImageStat
    from PIL.PngImagePlugin import PngInfo

    from .cli import MODEL_ID, MODEL_REVISION, doctor
    from .inference import check_residency, check_step, load_pipeline
    import json

    pipe = None
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
                    SimpleNamespace(
                        offline=os.getenv("STUDIO_OFFLINE") == "1", reduction="ordered"
                    ),
                    report,
                )
            connection.send({"phase": "generating"})
            torch.cuda.synchronize()
            start = time.perf_counter()
            with torch.inference_mode():
                image = pipe(
                    prompt=settings["prompt"],
                    width=settings["width"],
                    height=settings["height"],
                    num_inference_steps=settings["steps"],
                    guidance_scale=1.0,
                    generator=torch.Generator("cuda").manual_seed(settings["seed"]),
                    callback_on_step_end=check_step,
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
            metadata = PngInfo()
            metadata.add_text(
                "generation",
                json.dumps(
                    {
                        **settings,
                        "model": MODEL_ID,
                        "revision": MODEL_REVISION,
                        "reduction": "ordered",
                        "guidance_scale": 1.0,
                    }
                ),
            )
            output = io.BytesIO()
            image.save(output, format="PNG", pnginfo=metadata)
            connection.send(
                {
                    "phase": "completed",
                    "png": output.getvalue(),
                    "seconds": seconds,
                    "pixel_sha256": hashlib.sha256(image.tobytes()).hexdigest(),
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
