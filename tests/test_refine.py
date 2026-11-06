"""Reference ingestion, queue ownership and edit transports using CPU fixtures."""

import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
from io import BytesIO
import json
from pathlib import Path
import random
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin

from flux2_klein_studio.references import normalize_reference
from flux2_klein_studio.schemas import Generation
from flux2_klein_studio.server import Jobs, create_app


def fixture(size=(64, 64), color=(27, 91, 143)):
    image = Image.new("RGB", size, color)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class RefineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = create_app(Path(self.directory.name))
        self.jobs = self.app.state.jobs
        self.client = TestClient(self.app).__enter__()
        self.calls = []
        self.worker = patch.object(self.jobs.model, "generate", side_effect=self.render)
        self.worker.start()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.worker.stop()
        self.directory.cleanup()

    def render(self, settings, phase, cancelled):
        self.calls.append(settings)
        if settings.get("_reference_path"):
            with Image.open(settings["_reference_path"]) as source:
                self.assertEqual(
                    hashlib.sha256(source.convert("RGB").tobytes()).hexdigest(),
                    settings["reference"]["pixel_sha256"],
                )
                image = source.resize((settings["width"], settings["height"])).convert(
                    "RGB"
                )
        else:
            image = Image.new(
                "RGB", (settings["width"], settings["height"]), (44, 91, 143)
            )
        return dict(
            pixels=image.tobytes(),
            size=image.size,
            seconds=0.01,
            recipe={
                key: value for key, value in settings.items() if not key.startswith("_")
            },
        )

    def upload(self, data=None):
        response = self.client.post(
            "/api/references",
            files={
                "image": (
                    "private-name.png",
                    fixture() if data is None else data,
                    "image/png",
                )
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_upload_is_temporary_and_does_not_load_model(self):
        reference = self.upload()
        self.assertEqual(self.calls, [])
        self.assertEqual(list(Path(self.directory.name).glob("*.png")), [])
        self.assertEqual(self.client.get(reference["image"]).status_code, 200)
        self.assertNotIn("private-name", json.dumps(reference))
        self.client.delete("/api/references/" + reference["id"])
        self.assertEqual(self.client.get(reference["image"]).status_code, 404)

    def test_multipart_json_and_reference_id_edits_share_the_queue(self):
        reference = self.upload()
        cases = [
            dict(
                files={"image": ("photo.png", fixture(), "image/png")},
                data={
                    "prompt": "sunset",
                    "seed": "12345",
                    "n": "2",
                    "size": "square_small",
                },
            ),
            dict(
                json={
                    "prompt": "sunset",
                    "seed": 12345,
                    "n": 2,
                    "size": "square_small",
                    "images": [
                        {
                            "image_url": "data:image/png;base64,"
                            + base64.b64encode(fixture()).decode()
                        }
                    ],
                }
            ),
            dict(
                json={
                    "prompt": "sunset",
                    "seed": 12345,
                    "n": 2,
                    "size": "square_small",
                    "reference_id": reference["id"],
                }
            ),
        ]
        for case in cases:
            response = self.client.post("/v1/images/edits", **case)
            self.assertEqual(response.status_code, 200, response.text)
            data = response.json()
            self.assertEqual(len(data["data"]), 2)
            self.assertEqual(
                [item["seed"] for item in data["studio"]["images"]], [12345, 12346]
            )
            self.assertNotIn("_reference_path", response.text)
            for entry, recipe in zip(
                data["data"], data["studio"]["images"], strict=True
            ):
                self.assertEqual(recipe["reference"]["sha256"], reference["sha256"])
                with Image.open(BytesIO(base64.b64decode(entry["b64_json"]))) as image:
                    self.assertNotIn("_reference_path", image.info["generation"])
        self.assertEqual(len(self.jobs.references.records), 1)

    def test_source_job_copy_survives_original_deletion_while_queued(self):
        source = self.client.post(
            "/v1/images/generations", json={"prompt": "source", "size": "square_small"}
        ).json()["studio"]
        entered, release = threading.Event(), threading.Event()

        def blocked(settings, phase, cancelled):
            if settings["prompt"] == "block":
                entered.set()
                release.wait(4)
            return self.render(settings, phase, cancelled)

        with (
            patch.object(self.jobs.model, "generate", side_effect=blocked),
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            self.jobs.submit(Generation(prompt="block", width=512, height=512))
            self.assertTrue(entered.wait(2))
            future = pool.submit(
                self.client.post,
                "/v1/images/edits",
                json={
                    "prompt": "change lighting",
                    "source_job_id": source["job_id"],
                    "size": "square_small",
                },
            )
            try:
                deadline = time.monotonic() + 3
                while not self.jobs.pending and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(len(self.jobs.pending), 1)
                self.assertEqual(
                    self.client.delete("/api/jobs/" + source["job_id"]).status_code, 200
                )
            finally:
                release.set()
            response = future.result(timeout=3)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json()["studio"]["reference"]["source_job_id"], source["job_id"]
        )

    def test_saved_refinement_retains_reference_after_restart_and_deletes_everything(
        self,
    ):
        reference = self.upload()
        response = self.client.post(
            "/v1/images/edits",
            json={
                "prompt": "edit",
                "size": "square_small",
                "reference_id": reference["id"],
                "save_gallery": True,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        identifier = response.json()["studio"]["job_id"]
        self.client.delete("/api/references/" + reference["id"])
        self.assertEqual(
            self.client.get(f"/api/jobs/{identifier}/reference").status_code, 200
        )
        recovered = Jobs(Path(self.directory.name))
        try:
            self.assertEqual(
                recovered.get(identifier)["settings"]["reference"]["sha256"],
                reference["sha256"],
            )
            self.assertTrue(
                (
                    recovered.folder(recovered.get(identifier)) / "reference.png"
                ).is_file()
            )
        finally:
            recovered.close()
        folder = self.jobs.folder(self.jobs.get(identifier))
        self.assertTrue((folder / "reference.png").is_file())
        self.assertEqual(self.client.delete("/api/jobs/" + identifier).status_code, 200)
        self.assertFalse(folder.exists())

    def test_invalid_sources_masks_and_remote_urls_are_rejected_before_gpu(self):
        for extra in (
            {},
            {"image": "not an image"},
            {"image": "https://example.com/picture.png"},
            {"images": []},
            {"images": [{"image_url": "x"}, {"image_url": "y"}]},
            {"reference_id": "../secret"},
            {"image": "x", "source_job_id": "a" * 32},
            {"image": "x", "mask": "anything"},
        ):
            response = self.client.post(
                "/v1/images/edits", json={"prompt": "edit", **extra}
            )
            self.assertIn(response.status_code, (400, 404), response.text)
            self.assertIn("error", response.json())
        for files in (
            {"image": ("bad.png", b"not an image")},
            [("image[]", ("one.png", fixture())), ("image[]", ("two.png", fixture()))],
        ):
            response = self.client.post(
                "/v1/images/edits", data={"prompt": "edit"}, files=files
            )
            self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.calls, [])

    def test_mcp_refinement_accepts_payloads_larger_than_old_transport_limit(self):
        image = Image.frombytes(
            "RGB", (512, 512), random.Random(12345).randbytes(512 * 512 * 3)
        )
        output = BytesIO()
        image.save(output, format="PNG")
        encoded = base64.b64encode(output.getvalue()).decode()
        self.assertGreater(len(encoded), 64 * 1024)
        response = self.client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "refine_image",
                    "arguments": {
                        "prompt": "warm light",
                        "resolution": "square_small",
                        "image_base64": encoded,
                    },
                },
            },
            headers={"Accept": "application/json, text/event-stream"},
        )
        self.assertEqual(response.status_code, 200, response.text[:200])
        result = response.json()["result"]
        self.assertFalse(result.get("isError"), result)
        self.assertIn("reference", result["structuredContent"])
        self.assertEqual(len(self.jobs.references.records), 0)

    def test_edit_openapi_and_disabled_size_policy(self):
        schema = self.client.get("/openapi.json").json()
        self.assertIn("/v1/images/edits", schema["paths"])
        self.assertIn("EditRequest", schema["components"]["schemas"])
        self.client.put("/api/resolutions", json={"enabled_resolutions": []})
        response = self.client.post(
            "/v1/images/edits",
            files={"image": ("one.png", fixture())},
            data={"prompt": "edit"},
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.calls, [])


class NormalizationTests(unittest.TestCase):
    def test_large_reference_is_bounded_aligned_and_deterministic(self):
        data = fixture((1600, 1200))
        first, metadata = normalize_reference(data)
        second, repeated = normalize_reference(data)
        self.assertEqual(first, second)
        self.assertEqual(metadata, repeated)
        self.assertLessEqual(metadata["width"] * metadata["height"], 1024 * 1024)
        self.assertEqual(metadata["width"] % 16, 0)
        self.assertEqual(metadata["height"] % 16, 0)

    def test_transparency_exif_orientation_and_private_metadata(self):
        image = Image.new("RGBA", (64, 32), (200, 10, 20, 0))
        exif = Image.Exif()
        exif[274], exif[270] = 6, "private description"
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("private", "secret text")
        output = BytesIO()
        image.save(output, format="PNG", exif=exif, pnginfo=metadata)
        png, record = normalize_reference(output.getvalue())
        with Image.open(BytesIO(png)) as prepared:
            self.assertEqual(prepared.size, (32, 64))
            self.assertEqual(prepared.getpixel((0, 0)), (255, 255, 255))
            self.assertEqual(dict(prepared.getexif()), {})
            self.assertNotIn("private", prepared.info)
        self.assertEqual(
            (record["original_width"], record["original_height"]), (32, 64)
        )

    def test_invalid_empty_small_and_animated_images(self):
        for data in (b"", b"invalid", fixture((16, 16))):
            with self.assertRaises(HTTPException):
                normalize_reference(data)
        first, second = (
            Image.new("RGB", (64, 64), "red"),
            Image.new("RGB", (64, 64), "blue"),
        )
        output = BytesIO()
        first.save(
            output, format="GIF", save_all=True, append_images=[second], duration=100
        )
        with self.assertRaises(HTTPException):
            normalize_reference(output.getvalue())


if __name__ == "__main__":
    unittest.main()
