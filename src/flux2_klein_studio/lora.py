"""Experimental additive LoRA inference without changing packed base weights."""

import hashlib
import math
from pathlib import Path

import torch
from torch import nn
from torch.nn import functional as F

from .adapters import ADAPTERS
from .adapters import PIXEL_ART as PIXEL_ART


class AdditiveLoRALinear(nn.Module):
    """Retain the original projection; add B(A(x)) in adapter precision."""

    def __init__(self, base, down, up, scale, strength):
        super().__init__()
        self.base = base
        self.scale = scale
        self.strength = strength
        self.register_buffer("down", down, persistent=False)
        self.register_buffer("up", up, persistent=False)

    def forward(self, inputs, *args, **kwargs):
        result = self.base(inputs, *args, **kwargs)
        if not self.strength:
            return result
        correction = F.linear(F.linear(inputs.to(self.down.dtype), self.down), self.up)
        return (result + correction * (self.scale * self.strength)).to(result.dtype)


def install_lora(transformer, state, metadata, strength=1.0, require_cuda=True):
    """Validate an entire Diffusers A/B adapter before changing any modules.

    Ordinary LoRA and rsLoRA are supported. Reject alternative adapter algorithms
    and heterogeneous rank/alpha patterns instead of silently changing their math.
    """
    if not math.isfinite(strength) or not 0 <= strength <= 2:
        raise ValueError("LoRA strength must be finite and between 0 and 2")
    if any(isinstance(module, AdditiveLoRALinear) for module in transformer.modules()):
        raise ValueError("Remove the existing LoRA before installing another")
    config = {
        key.removeprefix("transformer."): value
        for key, value in (metadata or {}).items()
    }
    if (
        config.get("peft_type", "LORA") != "LORA"
        or config.get("bias", "none") != "none"
    ):
        raise ValueError(
            "Only ordinary LoRA or rsLoRA without adapter bias is supported"
        )
    unsupported = (
        "use_dora",
        "use_qalora",
        "fan_in_fan_out",
        "lora_bias",
        "rank_pattern",
        "alpha_pattern",
        "modules_to_save",
        "target_parameters",
        "layer_replication",
        "alora_invocation_tokens",
        "arrow_config",
        "corda_config",
        "eva_config",
        "trainable_token_indices",
    )
    if any(config.get(key) for key in unsupported):
        raise ValueError("This adapter requests an unsupported LoRA variant")
    pairs = {}
    for key, tensor in state.items():
        if key.endswith(".lora_A.weight"):
            path, side = key.removesuffix(".lora_A.weight"), "down"
        elif key.endswith(".lora_B.weight"):
            path, side = key.removesuffix(".lora_B.weight"), "up"
        else:
            raise ValueError(f"Unsupported adapter tensor: {key}")
        path = path.removeprefix("transformer.")
        pairs.setdefault(path, {})[side] = tensor
    if not pairs:
        raise ValueError("Adapter contains no LoRA projections")
    plans = []
    ranks = set()
    for path, pair in pairs.items():
        if set(pair) != {"down", "up"}:
            raise ValueError(f"Incomplete LoRA pair: {path}")
        base = transformer.get_submodule(path)
        leaf = base.base if type(base).__name__ == "OrderedSVDQLinear" else base
        if not isinstance(leaf, nn.Linear) and type(leaf).__name__ not in (
            "SVDQW4A4Linear",
            "AWQW4A16Linear",
        ):
            raise ValueError(f"Unsupported LoRA target: {path} ({type(base).__name__})")
        down, up = pair["down"], pair["up"]
        if down.ndim != 2 or up.ndim != 2:
            raise ValueError(f"Expected matrix LoRA tensors: {path}")
        rank = down.shape[0]
        if (
            rank <= 0
            or down.shape[1] != leaf.in_features
            or up.shape != (leaf.out_features, rank)
        ):
            raise ValueError(f"LoRA tensor dimensions do not match {path}")
        if rank != config.get("r", rank):
            raise ValueError(f"LoRA rank metadata does not match {path}")
        if down.dtype != up.dtype or down.dtype not in (
            torch.float32,
            torch.float16,
            torch.bfloat16,
        ):
            raise ValueError(f"Unsupported LoRA tensor precision: {path}")
        if not torch.isfinite(down).all() or not torch.isfinite(up).all():
            raise ValueError(f"Non-finite LoRA weights: {path}")
        alpha = config.get("lora_alpha", rank)
        if (
            not isinstance(alpha, (int, float))
            or not math.isfinite(alpha)
            or alpha <= 0
        ):
            raise ValueError("LoRA alpha must be finite and positive")
        scale = alpha / (math.sqrt(rank) if config.get("use_rslora") else rank)
        tensors = list(base.parameters()) + list(base.buffers())
        device = tensors[0].device
        if require_cuda and device.type != "cuda":
            raise ValueError(f"LoRA target is not GPU resident: {path}")
        parent_path, _, attribute = path.rpartition(".")
        parent = transformer.get_submodule(parent_path)
        plans.append((parent, attribute, base, down, up, scale, device))
        ranks.add(rank)
    wrappers = [
        AdditiveLoRALinear(
            base, down.to(device).detach(), up.to(device).detach(), scale, strength
        )
        for _, _, base, down, up, scale, device in plans
    ]
    for (parent, attribute, *_), wrapper in zip(plans, wrappers, strict=True):
        setattr(parent, attribute, wrapper)
    return {
        "modules": len(plans),
        "ranks": sorted(ranks),
        "alpha": config.get("lora_alpha"),
        "use_rslora": bool(config.get("use_rslora")),
        "strength": strength,
        "scales": sorted({plan[5] for plan in plans}),
        "weight_bytes": sum(
            w.down.numel() * w.down.element_size() + w.up.numel() * w.up.element_size()
            for w in wrappers
        ),
        "dtypes": sorted({str(w.down.dtype) for w in wrappers}),
    }


def remove_lora(transformer):
    for parent in list(transformer.modules()):
        for name, module in list(parent.named_children()):
            if isinstance(module, AdditiveLoRALinear):
                setattr(parent, name, module.base)


def load_adapter(transformer, adapter, *, offline=False, strength=1.0):
    """Load a curated, hash-verified adapter; never execute Hub code."""
    from diffusers import Flux2KleinPipeline
    from huggingface_hub import hf_hub_download

    weights = ADAPTERS[adapter]["weights"]
    hf_hub_download(
        weights["repository"],
        weights.get("license_file", "LICENSE"),
        revision=weights["revision"],
        local_files_only=offline,
    )
    path = Path(
        hf_hub_download(
            weights["repository"],
            weights["filename"],
            revision=weights["revision"],
            local_files_only=offline,
        )
    )
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != weights["sha256"]:
            raise ValueError(f"{adapter} adapter file does not match its pinned hash")
    state, metadata = Flux2KleinPipeline.lora_state_dict(
        str(path), use_safetensors=True, return_lora_metadata=True
    )
    details = install_lora(transformer, state, metadata, strength)
    return {**weights, **details}


def load_pixel_art(transformer, *, offline=False, strength=1.0):
    return load_adapter(transformer, "pixel-art", offline=offline, strength=strength)
