"""On-demand export, preserving the original image and its generation recipe."""

import json
from io import BytesIO

from PIL import Image

from .cli import MODEL_ID, MODEL_REVISION


def encode_image(path, format, settings):
    if format == "png":
        return path.read_bytes()
    with Image.open(path) as source:
        recipe = source.info.get("generation") or json.dumps(
            {
                "model": MODEL_ID,
                "revision": MODEL_REVISION,
                **settings,
                "reduction": "ordered",
            }
        )
        exif = Image.Exif()
        exif[270] = recipe
        output = BytesIO()
        options = (
            {"lossless": True, "method": 4}
            if format == "webp"
            else {"quality": 95, "subsampling": 0}
        )
        source.convert("RGB").save(
            output, format=format.upper(), exif=exif.tobytes(), **options
        )
    return output.getvalue()
