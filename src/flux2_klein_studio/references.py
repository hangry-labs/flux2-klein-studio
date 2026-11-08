"""Bounded CPU image decoding and temporary reference storage; no GPU imports."""

import base64
import binascii
import hashlib
from io import BytesIO
import math
from pathlib import Path
import re
import threading
import time
import uuid
import warnings

from fastapi import HTTPException
from PIL import Image, ImageChops, ImageDraw, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_REQUEST_BYTES = 24 * 1024 * 1024
MAX_IMAGE_PIXELS = 32 * 1024 * 1024
REFERENCE_PIXELS = 1024 * 1024
PREPROCESSING = "exif-rgb-white-alpha-lanczos-1mp-align16-v1"
GREEN_PREPROCESSING = "exif-rgb-white-alpha-lanczos-nearest-green-1mp-align16-v1"
IDENTIFIER = re.compile(r"[a-f0-9]{32}\Z")


class ZoomRegion(BaseModel):
    """Rectangle as fractions of the oriented source image, not output pixels."""

    model_config = ConfigDict(extra="forbid", strict=True)
    x: float = Field(ge=0, lt=1)
    y: float = Field(ge=0, lt=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def within_image(self):
        if self.x + self.width > 1 + 1e-9 or self.y + self.height > 1 + 1e-9:
            raise ValueError("Zoom rectangle must stay inside the source image")
        return self


def decode_base64(value):
    if not isinstance(value, str) or len(value) > MAX_REQUEST_BYTES:
        raise HTTPException(413, "Reference image exceeds the upload limit")
    if value.startswith(("http://", "https://")):
        raise HTTPException(
            400,
            "Upload image bytes or a base64 data URL; remote image URLs are unsupported",
        )
    if value.startswith("data:"):
        header, separator, value = value.partition(",")
        if not separator or not header.endswith(";base64"):
            raise HTTPException(400, "Use a base64-encoded image data URL")
    try:
        data = base64.b64decode("".join(value.split()), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(400, "Invalid base64 image") from exc
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "Reference images must be at most 16 MiB")
    return data


def normalize_reference(data, *, preserve_green=False, zoom_region=None):
    if preserve_green and zoom_region is not None:
        raise HTTPException(400, "Choose green painting or a red zoom rectangle")
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(
            413 if data else 400, "Upload a nonempty image of at most 16 MiB"
        )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as source:
                width, height = source.size
                if (
                    width * height > MAX_IMAGE_PIXELS
                    or min(width, height) < 32
                    or max(width, height) > 16384
                ):
                    raise HTTPException(
                        400,
                        "Reference images need edges of 32–16384 pixels and at most 32 megapixels",
                    )
                if getattr(source, "n_frames", 1) != 1:
                    raise HTTPException(
                        400,
                        "Choose one still image; animated or multi-page inputs are unsupported",
                    )
                source.load()
                oriented = ImageOps.exif_transpose(source)
                original_size = oriented.size
                if "A" in oriented.getbands() or "transparency" in oriented.info:
                    rgba = oriented.convert("RGBA")
                    image = Image.new("RGB", rgba.size, "white")
                    image.paste(rgba, mask=rgba.getchannel("A"))
                else:
                    image = oriented.convert("RGB")
        scale = min(1, math.sqrt(REFERENCE_PIXELS / (image.width * image.height)))
        size = tuple(max(16, int(edge * scale) // 16 * 16) for edge in image.size)
        green = None
        if preserve_green:
            differences = ImageChops.difference(
                image, Image.new("RGB", image.size, (0, 255, 0))
            ).split()
            green = ImageChops.lighter(
                ImageChops.lighter(differences[0], differences[1]), differences[2]
            ).point(lambda value: 255 if value == 0 else 0)
        if image.size != size:
            image = image.resize(size, Image.Resampling.LANCZOS)
            if green is not None:
                green = green.resize(size, Image.Resampling.NEAREST)
        if green is not None:
            image.paste((0, 255, 0), mask=green)
        zoom_metadata = {}
        if zoom_region is not None:
            unmarked_hash = hashlib.sha256(image.tobytes()).hexdigest()
            x, y = (
                round(zoom_region.x * image.width),
                round(zoom_region.y * image.height),
            )
            x, y = min(x, image.width - 1), min(y, image.height - 1)
            right = max(
                x,
                min(
                    image.width - 1,
                    round((zoom_region.x + zoom_region.width) * image.width) - 1,
                ),
            )
            bottom = max(
                y,
                min(
                    image.height - 1,
                    round((zoom_region.y + zoom_region.height) * image.height) - 1,
                ),
            )
            thickness = min(
                max(3, round(min(image.size) * 0.008)),
                max(1, min(right - x + 1, bottom - y + 1) // 2),
            )
            ImageDraw.Draw(image).rectangle(
                (x, y, right, bottom), outline=(255, 0, 0), width=thickness
            )
            zoom_metadata = {
                "zoom_region": zoom_region.model_dump(),
                "unmarked_pixel_sha256": unmarked_hash,
            }
        image.info.clear()
        output = BytesIO()
        image.save(output, format="PNG")
        png = output.getvalue()
        return png, {
            "sha256": hashlib.sha256(png).hexdigest(),
            "pixel_sha256": hashlib.sha256(image.tobytes()).hexdigest(),
            "original_sha256": hashlib.sha256(data).hexdigest(),
            "original_width": original_size[0],
            "original_height": original_size[1],
            "width": image.width,
            "height": image.height,
            "preprocessing": GREEN_PREPROCESSING
            if preserve_green
            else PREPROCESSING
            + ("-red-rectangle-v1" if zoom_region is not None else ""),
            **zoom_metadata,
            **(
                {"green_mask": {"color": "#00FF00", "pixels": green.histogram()[255]}}
                if green is not None
                else {}
            ),
        }
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise HTTPException(
            400,
            "Could not decode this image; use a valid still image such as PNG, JPEG or WebP",
        ) from exc


class ReferenceStore:
    """Uploads are temporary. Admitted jobs receive independent PNG copies."""

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.records = {}

    def _discard(self, identifier):
        self.records.pop(identifier, None)
        (self.root / (identifier + ".png")).unlink(missing_ok=True)

    def create(
        self, data, source_job_id=None, *, preserve_green=False, zoom_region=None
    ):
        png, metadata = normalize_reference(
            data, preserve_green=preserve_green, zoom_region=zoom_region
        )
        if source_job_id:
            metadata["source_job_id"] = source_job_id
        with self.lock:
            now = time.monotonic()
            for identifier in list(self.records):
                if now - self.records[identifier]["touched"] > 3600:
                    self._discard(identifier)
            while len(self.records) >= 64:
                self._discard(
                    min(self.records, key=lambda key: self.records[key]["touched"])
                )
            identifier = uuid.uuid4().hex
            (self.root / (identifier + ".png")).write_bytes(png)
            self.records[identifier] = {"metadata": metadata, "touched": now}
            return {
                "id": identifier,
                "image": f"/api/references/{identifier}/image",
                **metadata,
            }

    def path(self, identifier):
        with self.lock:
            if not IDENTIFIER.fullmatch(identifier) or identifier not in self.records:
                raise HTTPException(
                    404, "Reference expired or was removed; choose the image again"
                )
            path = self.root / (identifier + ".png")
            if (
                path.is_symlink()
                or path.resolve().parent != self.root.resolve()
                or not path.is_file()
            ):
                raise HTTPException(404, "Reference image is unavailable")
            if time.monotonic() - self.records[identifier]["touched"] > 3600:
                self._discard(identifier)
                raise HTTPException(404, "Reference expired; choose the image again")
            self.records[identifier]["touched"] = time.monotonic()
            return path

    def copy_to(self, identifier, destination):
        with self.lock:
            data = self.path(identifier).read_bytes()
            if (
                hashlib.sha256(data).hexdigest()
                != self.records[identifier]["metadata"]["sha256"]
            ):
                raise HTTPException(409, "Reference changed; choose the image again")
            destination.write_bytes(data)
            return dict(self.records[identifier]["metadata"])

    def read(self, identifier):
        """Read an immutable upload snapshot together with its provenance."""
        with self.lock:
            path = self.path(identifier)
            data = path.read_bytes()
            metadata = dict(self.records[identifier]["metadata"])
            if hashlib.sha256(data).hexdigest() != metadata["sha256"]:
                raise HTTPException(409, "Reference changed; choose the image again")
            return data, metadata

    def delete(self, identifier):
        with self.lock:
            if not IDENTIFIER.fullmatch(identifier):
                raise HTTPException(404, "Reference image not found")
            self._discard(identifier)


def source_bytes(jobs, identifier):
    with jobs.lock:
        _, path = jobs.image_path(identifier)
        return path.read_bytes()
