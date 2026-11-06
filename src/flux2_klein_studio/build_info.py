"""Source and container identity without importing GPU libraries."""

import os

from . import __version__


def build_identity():
    return {
        "version": __version__,
        "revision": os.getenv("STUDIO_BUILD_ID", "unknown"),
        "build_date": os.getenv("STUDIO_BUILD_DATE", "unknown"),
        "image_version": os.getenv("STUDIO_IMAGE_VERSION", "dev"),
        "image_flavor": os.getenv("STUDIO_IMAGE_FLAVOR", "tiny"),
    }


def offline_default():
    value = os.getenv("STUDIO_OFFLINE", "").strip()
    if value in {"0", "1"}:
        return value == "1"
    return (
        os.getenv("HF_HUB_OFFLINE") == "1" or os.getenv("STUDIO_DEFAULT_OFFLINE") == "1"
    )
