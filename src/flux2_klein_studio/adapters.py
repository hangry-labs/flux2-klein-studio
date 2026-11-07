"""Curated adapter identities and server-owned prompt templates (no CUDA imports)."""

from typing import Literal

Adapter = Literal["pixel-art", "koni-anime"]

PIXEL_ART = {
    "repository": "Limbicnation/pixel-art-lora",
    "revision": "0ac8e5c3400af68228811edc324721e25fc26777",
    "filename": "pytorch_lora_weights.comfyui.safetensors",
    "sha256": "24e938f510f5dd0c890ac8b1078f4abb87a50c9c053b2e85c44821c0f30011ad",
    "format": "comfyui-unit-scale",
}

KONI_ANIME = {
    "repository": "Sawata97/flux2_4b_koni_animestyle",
    "revision": "b392813770c6155d3ae22d3fb8062f247f5108a2",
    "filename": "Flux_klein_4b_anime_Koni.safetensors",
    "sha256": "c5f8fef8610f8f7725ab4da0410ff6ffb09542e9a65676ebcf01527eb3af429f",
    "format": "ai-toolkit-flux2",
    "license_file": "README.md",
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
    },
    "koni-anime": {
        "id": "koni-anime",
        "label": "Koni · Anime illustration",
        "template_version": 1,
        "prefix": "anime_style, ",
        "suffix": "",
        "help": "Adds the anime_style trigger automatically. Use your description for the character, scene and background.",
        "weights": KONI_ANIME,
    },
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
        for filename in (weights["filename"], weights.get("license_file", "LICENSE"))
    )


def ensure_available(adapter, offline):
    if adapter and offline and not cached(adapter):
        from fastapi import HTTPException

        raise HTTPException(
            503,
            f"{ADAPTERS[adapter]['label']} is not cached. Enable network loading once with STUDIO_OFFLINE=0 and generate with this adapter before using it offline.",
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
