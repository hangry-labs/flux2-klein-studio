"""Stable, discoverable image sizes shared by agent APIs."""

from enum import StrEnum


class Resolution(StrEnum):
    square_small = "square_small"
    square_medium = "square_medium"
    square_big = "square_big"
    portrait_small = "portrait_small"
    portrait_medium = "portrait_medium"
    portrait_big = "portrait_big"
    landscape_small = "landscape_small"
    landscape_medium = "landscape_medium"
    landscape_big = "landscape_big"
    widescreen_small = "widescreen_small"
    widescreen_medium = "widescreen_medium"
    widescreen_big = "widescreen_big"
    panorama_small = "panorama_small"
    panorama_medium = "panorama_medium"
    panorama_big = "panorama_big"
    photo_portrait = "photo_portrait"
    photo_landscape = "photo_landscape"


SIZES = dict(
    zip(
        Resolution,
        (
            (512, 512),
            (768, 768),
            (1024, 1024),
            (576, 768),
            (768, 1024),
            (960, 1280),
            (768, 576),
            (1024, 768),
            (1280, 960),
            (1024, 576),
            (1280, 720),
            (1536, 864),
            (1024, 512),
            (1280, 640),
            (1536, 768),
            (1024, 1536),
            (1536, 1024),
        ),
        strict=True,
    )
)


def catalog():
    from math import gcd

    return [
        {
            "id": name.value,
            "width": w,
            "height": h,
            "size": f"{w}x{h}",
            "aspect_ratio": f"{w // gcd(w, h)}:{h // gcd(w, h)}",
        }
        for name, (w, h) in SIZES.items()
    ]


def resolve_size(value):
    if value == "auto":
        return Resolution.square_big
    for name, (w, h) in SIZES.items():
        if value in (name.value, f"{w}x{h}"):
            return name
    raise ValueError(
        "Choose a named resolution or its exact size from /v1/images/resolutions"
    )
