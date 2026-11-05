"""Docker inference entry point. No UI or HTTP service is started yet."""

import argparse
import importlib.metadata
import json
from pathlib import Path

MODEL_ID = "lite-infer/flux.2-klein-4b-nunchaku-lite-nvfp4-r32"
MODEL_REVISION = "79dbbc2fdcfaf228863583d0cfabf56ff3e3c216"
DEFAULT_PROMPT = "A glass robot tending orchids in a sunlit greenhouse, cinematic lighting, highly detailed"


def versions():
    return {
        name: importlib.metadata.version(name)
        for name in (
            "torch",
            "diffusers",
            "transformers",
            "bitsandbytes",
            "kernels",
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
    return {
        "versions": versions(),
        "cuda_runtime": torch.version.cuda,
        "gpu": torch.cuda.get_device_name(0),
        "capability": capability,
        "total_vram_bytes": torch.cuda.get_device_properties(0).total_memory,
        "model": MODEL_ID,
        "revision": MODEL_REVISION,
    }


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "doctor", help="Check CUDA and imports without downloading model weights"
    )
    commands.add_parser(
        "download", help="Download the pinned model into the persistent cache"
    )
    for name in ("generate", "benchmark"):
        command = commands.add_parser(name)
        command.add_argument("--prompt", default=DEFAULT_PROMPT)
        command.add_argument("--seed", type=int, default=12345)
        command.add_argument("--steps", type=int, default=4)
        command.add_argument("--output-dir", type=Path, default=Path("/data/outputs"))
        command.add_argument("--offline", action="store_true")
        if name == "benchmark":
            command.add_argument("--runs", type=int, default=3)
    return result


def main():
    arguments = parser().parse_args()
    if getattr(arguments, "steps", 1) < 1 or getattr(arguments, "runs", 1) < 1:
        raise SystemExit("Steps and runs must be positive.")
    if not getattr(arguments, "prompt", "valid").strip():
        raise SystemExit("The prompt must not be empty.")
    if arguments.command == "doctor":
        print(json.dumps(doctor(), indent=2))
    elif arguments.command == "download":
        from huggingface_hub import snapshot_download

        print(snapshot_download(MODEL_ID, revision=MODEL_REVISION))
    else:
        from .inference import run

        run(arguments)
