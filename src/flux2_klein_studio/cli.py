"""Local image generation, benchmarks and browser studio entry point."""

import argparse
import importlib.metadata
import json
import os
import subprocess
from pathlib import Path

from .model_catalog import MODEL_ID, MODEL_REVISION
from .build_info import offline_default

DEFAULT_PROMPT = "A glass robot tending orchids in a sunlit greenhouse, cinematic lighting, highly detailed"
DEFAULT_RESOLUTIONS = "512x512,768x768,1024x1024,1280x1280,1536x1024,1024x1536,1920x1088,1088x1920,1536x1536"


def dimension(value):
    number = int(value)
    if number < 64 or number % 16:
        raise argparse.ArgumentTypeError(
            "Image dimensions must be at least 64 and divisible by 16"
        )
    return number


def resolutions(value):
    try:
        pairs = [
            tuple(dimension(n) for n in item.lower().split("x"))
            for item in value.split(",")
        ]
        if (
            not pairs
            or any(len(pair) != 2 for pair in pairs)
            or len(set(pairs)) != len(pairs)
        ):
            raise ValueError("Expected unique WIDTHxHEIGHT pairs")
        return pairs
    except (ValueError, argparse.ArgumentTypeError) as exc:
        raise argparse.ArgumentTypeError(
            "Use unique, 16-aligned dimensions such as 1024x1024,1536x1024"
        ) from exc


def versions():
    return {
        name: importlib.metadata.version(name)
        for name in (
            "torch",
            "diffusers",
            "transformers",
            "bitsandbytes",
            "kernels",
            "triton",
            "accelerate",
            "huggingface-hub",
        )
    }


def doctor():
    import torch
    from diffusers import Flux2KleinPipeline  # noqa: F401
    from diffusers.quantizers.auto import AUTO_QUANTIZER_MAPPING

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable. Run the container with --gpus all.")
    capability = torch.cuda.get_device_capability(0)
    if capability < (10, 0):
        raise RuntimeError("The selected NVFP4 checkpoint requires a Blackwell GPU.")
    if "nunchaku_lite" not in AUTO_QUANTIZER_MAPPING:
        raise RuntimeError("This Diffusers build lacks the Nunchaku Lite quantizer.")
    try:
        driver = subprocess.check_output(
            [
                "nvidia-smi",
                "--id=0",
                "--query-gpu=driver_version",
                "--format=csv,noheader",
            ],
            text=True,
            timeout=5,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        driver = None
    return {
        "versions": versions(),
        "cuda_runtime": torch.version.cuda,
        "driver_version": driver,
        "gpu": torch.cuda.get_device_name(0),
        "capability": capability,
        "total_vram_bytes": torch.cuda.get_device_properties(0).total_memory,
        "allocator_config": os.getenv("PYTORCH_ALLOC_CONF")
        or os.getenv("PYTORCH_CUDA_ALLOC_CONF")
        or "default",
        "model": MODEL_ID,
        "revision": MODEL_REVISION,
    }


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Start the local browser studio")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=7860)
    serve.add_argument("--output-dir", type=Path, default=Path("/data/outputs/studio"))
    commands.add_parser(
        "doctor", help="Check CUDA and imports without downloading model weights"
    )
    commands.add_parser(
        "download", help="Download the pinned model into the persistent cache"
    )
    for name in ("generate", "benchmark", "memory", "seed-test", "resolution-sweep"):
        command = commands.add_parser(name)
        command.add_argument("--prompt", default=DEFAULT_PROMPT)
        command.add_argument(
            "--reference-image",
            type=Path,
            help="One reference image for instruction-based editing",
        )
        command.add_argument("--seed", type=int, default=12345)
        command.add_argument("--steps", type=int, default=4)
        if name != "resolution-sweep":
            command.add_argument("--width", type=dimension, default=1024)
            command.add_argument("--height", type=dimension, default=1024)
        command.add_argument("--output-dir", type=Path, default=Path("/data/outputs"))
        command.add_argument(
            "--offline", action="store_true", default=offline_default()
        )
        command.add_argument(
            "--save-images",
            action="store_true",
            help="Save performance-suite PNGs; generation and seed tests always save them",
        )
        command.add_argument(
            "--reduction",
            choices=("ordered", "native"),
            default="ordered",
            help="Ordered low-rank accumulation (default), or the original native kernel",
        )
        if name != "generate":
            command.add_argument("--runs", type=int, default=5)
            command.add_argument("--warmups", type=int, default=2)
        if name == "resolution-sweep":
            command.add_argument(
                "--named-resolutions",
                action="store_true",
                help="Measure every named agent resolution from the preset catalog",
            )
            command.add_argument(
                "--suite", choices=("speed", "memory", "fixed-seed"), default="speed"
            )
            command.add_argument(
                "--plan",
                action="store_true",
                help="Print the workload without loading a model or writing files",
            )
            command.add_argument(
                "--resolutions", type=resolutions, default=DEFAULT_RESOLUTIONS
            )
            command.add_argument(
                "--timeout",
                type=int,
                default=180,
                help="Maximum seconds per isolated resolution process",
            )
            command.add_argument(
                "--stop-on-limit",
                action="store_true",
                help="Stop at OOM, timeout or low sampled headroom",
            )
            command.add_argument("--min-headroom-mib", type=int, default=512)
    return result


def main():
    # Configure CUDA allocation before importing anything that can initialize torch.
    # An explicit caller setting (including the legacy alias) takes precedence.
    os.environ.setdefault(
        "PYTORCH_ALLOC_CONF",
        os.getenv("PYTORCH_CUDA_ALLOC_CONF") or "expandable_segments:True",
    )
    arguments = parser().parse_args()
    if getattr(arguments, "named_resolutions", False):
        from .presets import SIZES

        arguments.resolutions = list(SIZES.values())
    if any(
        getattr(arguments, name, 1) < 1
        for name in ("steps", "runs", "warmups", "timeout")
    ):
        raise SystemExit("Steps, runs, warmups and timeout must be positive.")
    if getattr(arguments, "min_headroom_mib", 0) < 0:
        raise SystemExit("Minimum headroom must not be negative.")
    if not getattr(arguments, "prompt", "valid").strip():
        raise SystemExit("The prompt must not be empty.")
    if (
        getattr(arguments, "suite", None) == "fixed-seed"
        or arguments.command == "seed-test"
    ) and arguments.runs < 2:
        raise SystemExit("Fixed-seed tests require at least two measured repeats.")
    if getattr(arguments, "stop_on_limit", False) and arguments.suite != "memory":
        raise SystemExit("Capacity/headroom testing requires --suite memory.")
    if arguments.command == "serve":
        import uvicorn
        from .server import create_app

        uvicorn.run(
            create_app(arguments.output_dir), host=arguments.host, port=arguments.port
        )
    elif arguments.command == "doctor":
        print(json.dumps(doctor(), indent=2))
    elif arguments.command == "download":
        from huggingface_hub import snapshot_download

        print(snapshot_download(MODEL_ID, revision=MODEL_REVISION))
    elif arguments.command == "resolution-sweep":
        from .resolution_sweep import run_sweep

        run_sweep(arguments)
    else:
        from .inference import run

        run(arguments)
