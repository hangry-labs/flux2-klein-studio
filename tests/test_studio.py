import json
import tempfile
import threading
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin

from flux2_klein_studio.server import Generation, Jobs, create_app
from flux2_klein_studio.residency import ModelWorker


class StudioTests(unittest.TestCase):
    def completed(self, saved=True):
        jobs = self.app.state.jobs
        with patch("threading.Thread.start"):
            item = jobs.submit(
                Generation(prompt="deletion fixture", save_gallery=saved)
            )
            jobs.worker = None
        jobs.active = None
        jobs.items[item["id"]].update(
            status="completed", seconds=1, image=f"/api/jobs/{item['id']}/image"
        )
        folder = jobs.folder(item)
        (folder / "generate.png").write_bytes(b"fixture")
        (folder / "report.json").write_text("{}")
        (folder / "nested").mkdir()
        (folder / "nested/worker.log").write_text("fixture log")
        jobs.save(jobs.items[item["id"]])
        return item, folder

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = create_app(Path(self.directory.name))
        self.client = TestClient(self.app)

    def tearDown(self):
        self.app.state.jobs.close()
        self.client.close()
        self.directory.cleanup()

    def test_shell_and_capabilities_do_not_load_models(self):
        with patch("subprocess.Popen", side_effect=AssertionError("Unexpected worker")):
            self.assertEqual(self.client.get("/").status_code, 200)
            response = self.client.get("/api/status")
            self.assertFalse(response.json()["offloading"])
            self.assertEqual(response.headers["cache-control"], "no-store")
            for file in (
                "app.js",
                "styles.css",
                "resolutions.js",
            ):
                self.assertEqual(self.client.get("/static/" + file).status_code, 200)
            for name in ("logo", "logo_horizontal", "mascot", "badge", "favicon"):
                response = self.client.get(f"/assets/flux2_klein_{name}.webp")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["content-type"], "image/webp")
            self.assertEqual(
                self.client.get("/assets/hangrylabs_logo.webp").status_code, 200
            )

    def test_explicit_preload_trim_and_persistent_keep_loaded(self):
        model = self.app.state.jobs.model
        with (
            patch.object(model, "preload", return_value={"state": "ready"}) as load,
            patch.object(model, "trim", return_value={"state": "ready"}) as trim,
        ):
            self.assertEqual(self.client.post("/api/model/load").status_code, 200)
            self.assertEqual(self.client.post("/api/model/trim").status_code, 200)
            self.app.state.jobs.active = "active-fixture"
            self.assertEqual(self.client.post("/api/model/load").status_code, 409)
            self.assertEqual(self.client.post("/api/model/trim").status_code, 409)
            self.app.state.jobs.active = None
            load.assert_called_once()
            trim.assert_called_once()
        self.assertEqual(
            self.client.put(
                "/api/settings", json={"idle_seconds": 42, "keep_loaded": True}
            ).status_code,
            200,
        )
        recovered = Jobs(Path(self.directory.name))
        try:
            self.assertTrue(recovered.model.keep_loaded)
            self.assertEqual(recovered.model.idle_seconds, 42)
        finally:
            recovered.close()

    def test_validation_rejects_invalid_sizes_and_blank_prompts(self):
        for payload in (
            {"prompt": " "},
            {"prompt": "test", "width": 1080},
            {"prompt": "test", "seed": -1},
            {"prompt": "test", "steps": 0},
        ):
            self.assertEqual(
                self.client.post("/api/jobs", json=payload).status_code, 422
            )

    def test_foreign_origins_hosts_and_unknown_images_are_rejected(self):
        self.assertEqual(
            self.client.post(
                "/api/jobs",
                json={"prompt": "test"},
                headers={"origin": "https://elsewhere.example"},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                "/api/status", headers={"host": "elsewhere.example"}
            ).status_code,
            400,
        )
        self.assertEqual(self.client.get("/api/jobs/unknown/image").status_code, 404)

    def test_single_worker_cancel_and_restart_recovery(self):
        jobs = self.app.state.jobs
        with patch("threading.Thread.start"):
            first = jobs.submit(Generation(prompt="a landscape", save_gallery=True))
            jobs.cancel(first["id"])
            self.assertEqual(jobs.get(first["id"])["status"], "cancelling")
            jobs.worker = None
        queued = self.client.post("/api/jobs", json={"prompt": "another"})
        self.assertEqual(queued.status_code, 202)
        self.assertEqual(queued.json()["status"], "queued")
        jobs.cancel(queued.json()["id"])
        recovered = Jobs(Path(self.directory.name))
        self.assertEqual(recovered.get(first["id"])["status"], "failed")
        recovered.close()
        jobs.active = None

    def test_failed_worker_is_recorded_and_releases_slot(self):
        jobs = self.app.state.jobs
        with patch("threading.Thread.start"):
            item = jobs.submit(Generation(prompt="test", save_gallery=True))
            jobs.worker = None
        with patch.object(
            jobs.model, "generate", side_effect=OSError("worker unavailable")
        ):
            jobs.execute(item["id"])
        self.assertIsNone(jobs.active)
        self.assertEqual(jobs.get(item["id"])["status"], "failed")
        report = json.loads(
            (Path(self.directory.name) / item["id"] / "job.json").read_text()
        )
        self.assertEqual(report["status"], "failed")

    def test_delete_removes_all_files_and_does_not_restore_after_restart(self):
        item, folder = self.completed()
        neighbour, other_folder = self.completed()
        response = self.client.delete(f"/api/jobs/{item['id']}")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(folder.exists())
        self.assertTrue((other_folder / "generate.png").exists())
        self.assertEqual(
            self.client.get(f"/api/jobs/{item['id']}/image").status_code, 404
        )
        recovered = Jobs(Path(self.directory.name))
        try:
            self.assertNotIn(item["id"], recovered.items)
            self.assertIn(neighbour["id"], recovered.items)
        finally:
            recovered.close()

    def test_saved_gallery_remains_accessible_past_100_jobs_and_restart(self):
        oldest, folder = self.completed()
        for _ in range(100):
            self.completed()
        jobs = self.app.state.jobs
        jobs._prune()
        self.assertEqual(len(self.client.get("/api/jobs").json()), 101)
        self.assertEqual(
            self.client.get(f"/api/jobs/{oldest['id']}/image").status_code, 200
        )
        recovered = Jobs(Path(self.directory.name))
        try:
            self.assertEqual(len(recovered.items), 101)
            recovered.delete(oldest["id"])
            self.assertFalse(folder.exists())
        finally:
            recovered.close()

    def test_restart_skips_malformed_records_and_keeps_valid_gallery(self):
        valid, _ = self.completed()
        root = Path(self.directory.name)
        for index, bad in enumerate(
            (
                None,
                [],
                {"id": None},
                {"settings": None},
                {"created": "yesterday"},
                {"created": float("nan")},
                {"status": "unknown"},
                {"seconds": None},
            )
        ):
            folder = root / f"{index:032x}"
            folder.mkdir()
            item = (
                {**self.app.state.jobs.get(valid["id"]), "id": folder.name, **bad}
                if isinstance(bad, dict)
                else bad
            )
            (folder / "job.json").write_text(json.dumps(item), encoding="utf-8")
        recovered = Jobs(root)
        try:
            self.assertEqual(set(recovered.items), {valid["id"]})
            self.assertEqual(recovered.get(valid["id"])["status"], "completed")
        finally:
            recovered.close()

    def test_restart_keeps_saved_records_without_explicit_gallery_flag(self):
        item, _ = self.completed()
        jobs = self.app.state.jobs
        del jobs.items[item["id"]]["settings"]["save_gallery"]
        jobs.save(jobs.items[item["id"]])
        recovered = Jobs(jobs.root)
        try:
            self.assertEqual(recovered.get(item["id"]), jobs.get(item["id"]))
        finally:
            recovered.close()

    def test_delete_rejects_active_foreign_and_failed_filesystem_operations(self):
        item, folder = self.completed()
        jobs = self.app.state.jobs
        jobs.active = item["id"]
        self.assertEqual(self.client.delete(f"/api/jobs/{item['id']}").status_code, 409)
        jobs.active = None
        self.assertEqual(
            self.client.delete(
                f"/api/jobs/{item['id']}",
                headers={"origin": "https://elsewhere.example"},
            ).status_code,
            403,
        )
        with patch("shutil.rmtree", side_effect=PermissionError("locked")):
            self.assertEqual(
                self.client.delete(f"/api/jobs/{item['id']}").status_code, 409
            )
        self.assertIn(item["id"], jobs.items)
        self.assertTrue(folder.exists())

    def test_unsaved_results_are_temporary_and_absent_from_gallery(self):
        item, folder = self.completed(saved=False)
        self.assertFalse((Path(self.directory.name) / item["id"]).exists())
        self.assertEqual(self.client.get("/api/jobs").json(), [])
        self.assertEqual(
            self.client.get(f"/api/jobs/{item['id']}/image").status_code, 200
        )
        self.app.state.jobs.close()
        self.assertFalse(folder.exists())

    def test_idle_setting_is_validated_and_persists(self):
        self.assertEqual(
            self.client.put("/api/settings", json={"idle_seconds": -1}).status_code, 422
        )
        self.assertEqual(
            self.client.put("/api/settings", json={"idle_seconds": 42}).status_code, 200
        )
        recovered = Jobs(Path(self.directory.name))
        try:
            self.assertEqual(recovered.model.idle_seconds, 42)
        finally:
            recovered.close()

    def test_settings_write_failure_preserves_saved_and_live_policy(self):
        jobs = self.app.state.jobs
        self.client.put("/api/settings", json={"idle_seconds": 42})
        path = jobs.root / "settings.json"
        before = path.read_bytes()
        with patch("pathlib.Path.replace", side_effect=PermissionError("locked")):
            response = self.client.put(
                "/api/settings", json={"idle_seconds": 99, "keep_loaded": True}
            )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(jobs.model.idle_seconds, 42)
        self.assertFalse(jobs.model.keep_loaded)

    def test_download_formats_preserve_recipe_without_creating_files(self):
        for saved in (True, False):
            with self.subTest(saved=saved):
                item, folder = self.completed(saved=saved)
                # Varied pixels catch accidental lossy WebP encoding.
                original = Image.new("RGB", (32, 32))
                original.putdata(
                    [(x * 7 % 256, x * 13 % 256, x * 29 % 256) for x in range(1024)]
                )
                recipe = json.dumps({**item["settings"], "prompt": "orchids \u2728"})
                metadata = PngImagePlugin.PngInfo()
                metadata.add_text("generation", recipe)
                path = folder / "generate.png"
                original.save(path, pnginfo=metadata)
                canonical = path.read_bytes()
                before = set(folder.rglob("*"))
                for format, extension in (
                    ("png", "png"),
                    ("webp", "webp"),
                    ("jpeg", "jpg"),
                ):
                    response = self.client.get(
                        f"/api/jobs/{item['id']}/download?format={format}"
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(
                        response.headers["content-type"], f"image/{format}"
                    )
                    self.assertIn(
                        "attachment;", response.headers["content-disposition"]
                    )
                    self.assertIn(
                        f".{extension}", response.headers["content-disposition"]
                    )
                    with Image.open(BytesIO(response.content)) as decoded:
                        self.assertEqual(decoded.size, original.size)
                        if format == "png":
                            self.assertEqual(response.content, canonical)
                            self.assertEqual(decoded.info["generation"], recipe)
                        else:
                            self.assertEqual(decoded.getexif()[270], recipe)
                        if format != "jpeg":
                            self.assertEqual(
                                decoded.convert("RGB").tobytes(), original.tobytes()
                            )
                self.assertEqual(set(folder.rglob("*")), before)
                self.assertEqual(path.read_bytes(), canonical)
                self.assertEqual(
                    self.client.get(
                        f"/api/jobs/{item['id']}/download?format=gif"
                    ).status_code,
                    422,
                )
                self.client.delete(f"/api/jobs/{item['id']}")
                self.assertEqual(
                    self.client.get(
                        f"/api/jobs/{item['id']}/download?format=webp"
                    ).status_code,
                    404,
                )

    def test_legacy_png_download_includes_recipe_and_missing_image_is_404(self):
        item, folder = self.completed()
        Image.new("RGB", (16, 16)).save(folder / "generate.png")
        with Image.open(
            BytesIO(
                self.client.get(f"/api/jobs/{item['id']}/download?format=webp").content
            )
        ) as decoded:
            recipe = json.loads(decoded.getexif()[270])
            self.assertEqual(recipe["seed"], item["settings"]["seed"])
            self.assertEqual(recipe["reduction"], "ordered")
        (folder / "generate.png").unlink()
        self.assertEqual(
            self.client.get(f"/api/jobs/{item['id']}/download").status_code, 404
        )

    def test_delete_refuses_linked_directory(self):
        identifier = "a" * 32
        external = Path(self.directory.name) / "outside"
        external.mkdir()
        (external / "keep.txt").write_text("must survive")
        folder = Path(self.directory.name) / identifier
        try:
            folder.symlink_to(external, target_is_directory=True)
        except OSError:
            self.skipTest("Directory symlinks unavailable on this host")
        self.app.state.jobs.items[identifier] = {
            "id": identifier,
            "status": "failed",
            "settings": {"save_gallery": True},
        }
        self.assertEqual(self.client.delete(f"/api/jobs/{identifier}").status_code, 409)
        self.assertTrue((external / "keep.txt").exists())


class ResidencyTests(unittest.TestCase):
    def test_preload_and_trim_reuse_process_without_image_settings(self):
        worker = ModelWorker()
        worker.stop.set()
        worker.monitor.join(timeout=2)
        process = Mock(pid=1)
        process.is_alive.return_value = False
        connection = Mock()
        connection.poll.return_value = True
        connection.recv.return_value = {"phase": "completed"}
        worker.process, worker.connection = process, connection
        try:
            self.assertEqual(worker.preload()["state"], "ready")
            self.assertEqual(worker.trim()["state"], "ready")
            self.assertIs(worker.process, process)
            self.assertEqual(
                [call.args[0] for call in connection.send.call_args_list],
                [{"_operation": "load"}, {"_operation": "trim"}],
            )
            worker.keep_loaded = True
            self.assertIsNone(worker.snapshot()["unload_in_seconds"])
            with (
                patch.object(worker.stop, "wait", side_effect=[False, True]),
                patch.object(worker, "unload") as unload,
            ):
                worker.expire()
                unload.assert_not_called()
        finally:
            worker.close()

    def test_warm_requests_reuse_worker_and_restart_idle_deadline(self):
        worker = ModelWorker()
        worker.stop.set()
        worker.monitor.join(timeout=2)
        process = Mock(pid=1)
        process.is_alive.return_value = False
        connection = Mock()
        connection.poll.return_value = True
        connection.recv.return_value = {"phase": "completed", "png": b"image"}
        worker.process, worker.connection = process, connection
        try:
            for now in (100, 250):
                with patch(
                    "flux2_klein_studio.residency.time.monotonic", return_value=now
                ):
                    worker.generate({}, lambda value: None, threading.Event())
                    self.assertEqual(worker.snapshot()["unload_in_seconds"], 300)
            self.assertIs(worker.process, process)
            self.assertEqual(connection.send.call_count, 2)
        finally:
            worker.close()
