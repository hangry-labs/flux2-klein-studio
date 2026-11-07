"""Curated adapter identities and server-owned prompt templates (no CUDA imports)."""

PIXEL_ART = {
    "repository": "Limbicnation/pixel-art-lora",
    "revision": "0ac8e5c3400af68228811edc324721e25fc26777",
    "filename": "pytorch_lora_weights.comfyui.safetensors",
    "sha256": "24e938f510f5dd0c890ac8b1078f4abb87a50c9c053b2e85c44821c0f30011ad",
    "format": "comfyui-unit-scale",
}

ADAPTERS = {
    "pixel-art": {
        "id": "pixel-art",
        "label": "Pixel Art · character sprites",
        "template_version": 2,
        "prefix": "pixel art sprite, ",
        "suffix": ", game asset, solid green screen background, flat uniform green background, isolated character",
        "help": "Adds the fixed sprite prompt and requests a green screen background. Output remains RGB; background removal is a separate step.",
        "weights": PIXEL_ART,
    }
}


def cached(adapter):
    from huggingface_hub import try_to_load_from_cache

    weights = ADAPTERS[adapter]["weights"]
    return all(
        isinstance(
            try_to_load_from_cache(
                weights["repository"], filename, revision=weights["revision"]
            ),
            str,
        )
        for filename in (weights["filename"], "LICENSE")
    )


def ensure_available(adapter, offline):
    if adapter and offline and not cached(adapter):
        from fastapi import HTTPException

        raise HTTPException(
            503,
            "Pixel Art is not cached. Enable network loading once with STUDIO_OFFLINE=0 and generate with this adapter before using it offline.",
        )


def catalog(offline=False):
    profiles = []
    for profile in ADAPTERS.values():
        available = not offline or cached(profile["id"])
        profiles.append(
            {
                **profile,
                "weights": dict(profile["weights"]),
                "available": available,
                "unavailable_reason": ""
                if available
                else "Not cached: enable network loading with STUDIO_OFFLINE=0 for its first use.",
            }
        )
    return profiles


def compose_prompt(description, adapter=None):
    if adapter is None:
        return description
    profile = ADAPTERS[adapter]
    return profile["prefix"] + description + profile["suffix"]


def snapshot(options):
    settings = options.model_dump(exclude_none=True)
    if options.adapter:
        profile = ADAPTERS[options.adapter]
        settings["adapter_profile"] = {**profile, "weights": dict(profile["weights"])}
        settings["effective_prompt"] = compose_prompt(options.prompt, options.adapter)
    return settings
