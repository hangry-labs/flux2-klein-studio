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
from PIL import Image, ImageDraw, PngImagePlugin

from flux2_klein_studio.references import ZoomRegion, normalize_reference
from flux2_klein_studio.schemas import Generation
from flux2_klein_studio.server import Jobs, create_app
from flux2_klein_studio.adapters import OUTPAINT_PROMPT, ZOOM_PROMPT


def fixture(size=(64, 64), color=(27, 91, 143)):
    image = Image.new("RGB", size, color)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class RefineTests(unittest.TestCase):
    def rpc(self, name, arguments):
        response = self.client.post(
            "/mcp/",
            headers={"Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["result"]

    def test_zoom_marker_is_hollow_and_preserves_original_pixels(self):
        data = fixture((128, 64))
        region = ZoomRegion(x=0.25, y=0.25, width=0.5, height=0.5)
        marked, metadata = normalize_reference(data, zoom_region=region)
        _, original = normalize_reference(data)
        self.assertEqual(metadata["unmarked_pixel_sha256"], original["pixel_sha256"])
        self.assertEqual(metadata["zoom_region"], region.model_dump())
        with Image.open(BytesIO(marked)) as image:
            self.assertEqual(image.getpixel((32, 16)), (255, 0, 0))
            self.assertEqual(image.getpixel((64, 32)), (27, 91, 143))
            self.assertEqual(image.getpixel((0, 0)), (27, 91, 143))
        with self.assertRaises(HTTPException):
            normalize_reference(data, zoom_region=region, preserve_green=True)

    @patch("flux2_klein_studio.job_queue.offline_default", new=lambda: False)
    def test_zoom_http_mcp_and_browser_match_and_iterate_from_result(self):
        original = self.upload()
        region = dict(x=0.25, y=0.25, width=0.5, height=0.5)
        marked = self.client.post(
            "/api/references",
            json={"reference_id": original["id"], "zoom_region": region},
        )
        self.assertEqual(marked.status_code, 201, marked.text)
        browser = self.client.post(
            "/api/jobs",
            json={
                "reference_id": marked.json()["id"],
                "adapter": "zoom",
                "width": 64,
                "height": 64,
            },
        )
        self.assertEqual(browser.status_code, 202, browser.text)
        http = self.client.post(
            "/v1/images/edits",
            json={
                "reference_id": original["id"],
                "adapter": "zoom",
                "zoom_region": region,
                "size": "square_small",
                "seed": 12345,
            },
        )
        self.assertEqual(http.status_code, 200, http.text)
        mcp = self.rpc(
            "refine_image",
            {
                "reference_id": original["id"],
                "adapter": "zoom",
                "zoom_region": region,
                "resolution": "square_small",
                "seed": 12345,
            },
        )
        self.assertFalse(mcp.get("isError", False), mcp)
        settings = [
            browser.json()["settings"],
            http.json()["studio"],
            mcp["structuredContent"],
        ]
        for recipe in settings:
            self.assertEqual(recipe["prompt"], "")
            self.assertEqual(recipe["effective_prompt"], ZOOM_PROMPT)
            self.assertEqual(recipe["adapter_profile"]["strength"], 1.1)
            self.assertEqual(recipe["reference"]["zoom_region"], region)
            self.assertEqual(
                recipe["reference"]["unmarked_pixel_sha256"], original["pixel_sha256"]
            )
        self.assertEqual(
            len({recipe["reference"]["pixel_sha256"] for recipe in settings}), 1
        )
        self.assertEqual(
            set(self.jobs.references.records), {original["id"], marked.json()["id"]}
        )
        unchanged = self.jobs.references.records[original["id"]]["metadata"]
        self.assertEqual(unchanged["pixel_sha256"], original["pixel_sha256"])
        first = http.json()["studio"]["job_id"]
        next_result = self.rpc(
            "refine_image",
            {
                "source_job_id": first,
                "adapter": "zoom",
                "zoom_region": dict(x=0.1, y=0.2, width=0.6, height=0.7),
                "prompt": "focus on the face",
                "resolution": "square_small",
            },
        )
        self.assertFalse(next_result.get("isError", False), next_result)
        recipe = next_result["structuredContent"]
        self.assertEqual(recipe["reference"]["source_job_id"], first)
        self.assertEqual(
            recipe["effective_prompt"], ZOOM_PROMPT + "\nfocus on the face"
        )
        self.assertEqual(
            recipe["reference"]["unmarked_pixel_sha256"],
            http.json()["studio"]["pixel_sha256"],
        )
        # The browser's result -> upload -> marked copy path keeps source provenance.
        unmarked = self.client.post(
            "/api/references", json={"source_job_id": first}
        ).json()
        copied = self.client.post(
            "/api/references",
            json={"reference_id": unmarked["id"], "zoom_region": region},
        )
        self.assertEqual(copied.status_code, 201, copied.text)
        self.assertEqual(copied.json()["source_job_id"], first)
        self.assertEqual(
            copied.json()["unmarked_pixel_sha256"],
            http.json()["studio"]["pixel_sha256"],
        )

    def test_zoom_invalid_regions_and_wrong_modes_reject_before_worker(self):
        original = self.upload()
        for region in (
            dict(x=-0.1, y=0, width=0.5, height=0.5),
            dict(x=0.8, y=0, width=0.5, height=0.5),
            dict(x=0, y=0, width=0, height=0.5),
            dict(x=0, y=0, width=0.5, height=float("inf")),
            dict(x="0", y=0, width=0.5, height=0.5),
        ):
            # Infinity cannot be encoded as strict JSON; send its textual form directly.
            body = json.dumps(
                {
                    "reference_id": original["id"],
                    "adapter": "zoom",
                    "zoom_region": region,
                }
            )
            response = self.client.post(
                "/v1/images/edits",
                content=body,
                headers={"Content-Type": "application/json"},
            )
            self.assertEqual(response.status_code, 400, response.text)
        region = dict(x=0, y=0, width=1, height=1)
        for adapter in (None, "outpaint"):
            response = self.client.post(
                "/v1/images/edits",
                json={
                    "reference_id": original["id"],
                    "adapter": adapter,
                    "prompt": "edit",
                    "zoom_region": region,
                },
            )
            self.assertEqual(response.status_code, 400, response.text)
        self.assertTrue(
            self.rpc(
                "refine_image",
                {
                    "reference_id": original["id"],
                    "prompt": "edit",
                    "zoom_region": region,
                },
            ).get("isError")
        )
        self.assertEqual(self.calls, [])
        self.assertEqual(set(self.jobs.references.records), {original["id"]})

    @patch("flux2_klein_studio.job_queue.offline_default", new=lambda: False)
    def test_zoom_multipart_and_already_marked_replay_do_not_duplicate_instruction(
        self,
    ):
        region = dict(x=0.2, y=0.2, width=0.6, height=0.6)
        response = self.client.post(
            "/v1/images/edits",
            files={"image": ("source.png", fixture(), "image/png")},
            data={
                "adapter": "zoom",
                "zoom_region": json.dumps(region),
                "size": "square_small",
                "prompt": ZOOM_PROMPT + "\nfocus on the eyes",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        recipe = response.json()["studio"]
        self.assertEqual(recipe["prompt"], "focus on the eyes")
        self.assertEqual(
            recipe["effective_prompt"], ZOOM_PROMPT + "\nfocus on the eyes"
        )
        self.assertEqual(recipe["reference"]["zoom_region"], region)
        self.assertEqual(self.jobs.references.records, {})
        self.assertEqual(
            self.client.post(
                "/v1/images/generations", json={"prompt": "test", "adapter": "zoom"}
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post("/api/jobs", json={"adapter": "zoom"}).status_code, 422
        )
        schema = self.client.get("/openapi.json").json()
        self.assertIn("ZoomRegion", schema["components"]["schemas"])
        status = self.client.get("/api/status").json()
        self.assertIn("zoom", [p["id"] for p in status["edit_adapters"]])
        self.assertNotIn("zoom", [p["id"] for p in status["adapters"]])

    def test_green_fill_is_edit_only_and_discovered_separately(self):
        status = self.client.get("/api/status").json()
        self.assertNotIn("outpaint", [p["id"] for p in status["adapters"]])
        self.assertEqual(status["edit_adapters"][0]["id"], "outpaint")
        self.assertEqual(status["edit_adapters"][0]["default_prompt"], OUTPAINT_PROMPT)
        for path, status_code in (("/api/jobs", 422), ("/v1/images/generations", 400)):
            self.assertEqual(
                self.client.post(
                    path, json={"prompt": OUTPAINT_PROMPT, "adapter": "outpaint"}
                ).status_code,
                status_code,
            )
        self.assertEqual(self.calls, [])

    @patch("flux2_klein_studio.job_queue.offline_default", new=lambda: False)
    def test_green_fill_browser_http_and_mcp_preserve_custom_instructions(self):
        data = fixture(color=(0, 255, 0))
        upload = self.client.post(
            "/api/references",
            files={"image": ("marked.png", data, "image/png")},
            data={"preserve_green": "true"},
        )
        self.assertEqual(upload.status_code, 201, upload.text)
        reference = upload.json()
        prompt = "a brick wall"
        response = self.client.post(
            "/api/jobs",
            json={
                "prompt": prompt,
                "adapter": "outpaint",
                "reference_id": reference["id"],
            },
        )
        self.assertEqual(response.status_code, 202, response.text)
        browser = response.json()["settings"]
        response = self.client.post(
            "/v1/images/edits",
            json={
                "prompt": prompt,
                "adapter": "outpaint",
                "size": "square_small",
                "image": base64.b64encode(data).decode(),
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        http = response.json()["studio"]
        response = self.client.post(
            "/mcp/",
            headers={"Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "refine_image",
                    "arguments": {
                        "prompt": prompt,
                        "adapter": "outpaint",
                        "image_base64": base64.b64encode(data).decode(),
                        "resolution": "square_small",
                    },
                },
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()["result"]
        self.assertFalse(result.get("isError", False), result)
        for settings in (browser, http, result["structuredContent"]):
            self.assertEqual(settings["prompt"], prompt)
            self.assertEqual(
                settings["effective_prompt"], OUTPAINT_PROMPT + "\n" + prompt
            )
            self.assertEqual(settings["adapter_profile"]["strength"], 1.1)
            self.assertEqual(settings["reference"]["green_mask"]["pixels"], 64 * 64)
            self.assertEqual(settings["reference"]["green_mask"]["color"], "#00FF00")
        self.assertEqual(len(self.calls), 3)

    @patch("flux2_klein_studio.job_queue.offline_default", new=lambda: False)
    def test_green_fill_optional_text_and_legacy_prompt_replay(self):
        reference = self.upload(fixture(color=(0, 255, 0)))
        for extra, details in (
            ({}, ""),
            ({"prompt": ""}, ""),
            ({"prompt": "   "}, ""),
            ({"prompt": OUTPAINT_PROMPT}, ""),
            ({"prompt": OUTPAINT_PROMPT + "\na brick wall"}, "a brick wall"),
        ):
            with self.subTest(extra=extra):
                response = self.client.post(
                    "/v1/images/edits",
                    json={
                        "adapter": "outpaint",
                        "reference_id": reference["id"],
                        "size": "square_small",
                        **extra,
                    },
                )
                self.assertEqual(response.status_code, 200, response.text)
                settings = response.json()["studio"]
                self.assertEqual(settings["prompt"], details)
                self.assertEqual(
                    settings["effective_prompt"],
                    OUTPAINT_PROMPT + ("\n" + details if details else ""),
                )
        response = self.client.post(
            "/api/jobs",
            json={
                "adapter": "outpaint",
                "reference_id": reference["id"],
                "width": 64,
                "height": 64,
            },
        )
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(
            response.json()["settings"]["effective_prompt"], OUTPAINT_PROMPT
        )
        response = self.client.post(
            "/mcp/",
            headers={"Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "refine_image",
                    "arguments": {
                        "adapter": "outpaint",
                        "reference_id": reference["id"],
                        "resolution": "square_small",
                    },
                },
            },
        )
        result = response.json()["result"]
        self.assertFalse(result.get("isError", False), result)
        self.assertEqual(result["structuredContent"]["prompt"], "")
        self.assertEqual(
            result["structuredContent"]["effective_prompt"], OUTPAINT_PROMPT
        )
        calls = len(self.calls)
        for extra in ({}, {"prompt": ""}, {"prompt": "   "}):
            self.assertEqual(
                self.client.post(
                    "/v1/images/edits", json={"reference_id": reference["id"], **extra}
                ).status_code,
                400,
            )
        self.assertEqual(len(self.calls), calls)

    def test_green_fill_offline_missing_weights_rejects_before_worker(self):
        with (
            patch("flux2_klein_studio.job_queue.offline_default", return_value=True),
            patch("huggingface_hub.try_to_load_from_cache", return_value=None),
        ):
            response = self.client.post(
                "/v1/images/edits",
                json={
                    "prompt": OUTPAINT_PROMPT,
                    "adapter": "outpaint",
                    "image": base64.b64encode(fixture()).decode(),
                },
            )
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn("STUDIO_OFFLINE=0", response.json()["error"]["message"])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.jobs.references.records, {})

    def test_green_markers_survive_reference_resizing_exactly(self):
        image = Image.new("RGB", (1600, 800), (30, 42, 90))
        ImageDraw.Draw(image).rectangle((401, 101, 1198, 698), fill=(0, 255, 0))
        data = BytesIO()
        image.save(data, format="PNG")
        normalized, meta = normalize_reference(data.getvalue(), preserve_green=True)
        with Image.open(BytesIO(normalized)) as result:
            self.assertLessEqual(result.width * result.height, 1024**2)
            self.assertEqual(
                result.getpixel((result.width // 2, result.height // 2)), (0, 255, 0)
            )
            self.assertEqual(result.getpixel((0, 0)), (30, 42, 90))
            count = sum(pixel == (0, 255, 0) for pixel in result.getdata())
            self.assertGreaterEqual(count, meta["green_mask"]["pixels"])
            self.assertGreater(meta["green_mask"]["pixels"], 0)
        self.assertIn("nearest-green", meta["preprocessing"])

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
