"""Publication safeguards run on CPU, without model downloads or registry writes."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools import ci_images, collect_runtime_sources as sources, release
from flux2_klein_studio.build_info import offline_default
from flux2_klein_studio.container_entrypoint import seed_cache

ROOT = Path(__file__).resolve().parents[1]


class PublicationTests(unittest.TestCase):
    def test_main_only_publishes_moving_tags(self):
        plan = ci_images.plan_images("push", "refs/heads/main")
        self.assertEqual(plan["release_tag"], "")
        self.assertEqual(len(plan["baked_tags"]), 2)
        self.assertTrue(all(tag.endswith(":latest") for tag in plan["baked_tags"]))
        self.assertTrue(all(tag.endswith(":latest_tiny") for tag in plan["tiny_tags"]))

    def test_stable_tags_do_not_move_latest(self):
        for tag in ("v0.1", "v0.1.0"):
            with self.subTest(tag=tag):
                plan = ci_images.plan_images("push", "refs/tags/" + tag)
                self.assertTrue(
                    all(ref.endswith(":" + tag) for ref in plan["baked_tags"])
                )
                self.assertTrue(
                    all(ref.endswith(":" + tag + "_tiny") for ref in plan["tiny_tags"])
                )

    def test_rejects_untrusted_or_ambiguous_publication_events(self):
        for event, ref, tag, latest in (
            ("pull_request", "refs/pull/1/merge", "", False),
            ("push", "refs/heads/feature", "", False),
            ("push", "refs/tags/v0.1-snapshot", "", False),
            ("workflow_dispatch", "refs/heads/main", "", False),
            ("workflow_dispatch", "refs/heads/main", "v0.1\nother", False),
        ):
            with (
                self.subTest(event=event, ref=ref, tag=tag),
                self.assertRaises(ValueError),
            ):
                ci_images.plan_images(event, ref, tag, latest)

    def test_dispatch_can_explicitly_publish_stable_and_moving(self):
        plan = ci_images.plan_images(
            "workflow_dispatch", "refs/heads/main", "v0.1", True
        )
        self.assertEqual(len(plan["baked_tags"]), 4)

    def test_publication_catalog_contains_only_standard_and_tiny(self):
        plan = ci_images.plan_images(
            "workflow_dispatch", "refs/heads/main", "v0.1", True
        )
        self.assertEqual(
            set(plan), {"baked_tags", "tiny_tags", "release_tag", "image_version"}
        )
        self.assertEqual(len(plan["baked_tags"]) + len(plan["tiny_tags"]), 8)

    def test_existing_stable_tag_and_registry_auth_errors_fail_closed(self):
        plan = ci_images.plan_images("push", "refs/tags/v0.1")
        for code, message in ((0, "existing"), (1, "unauthorized")):
            with (
                patch.object(
                    ci_images,
                    "run",
                    return_value=subprocess.CompletedProcess([], code, message, ""),
                ),
                self.assertRaises((ValueError, RuntimeError)),
            ):
                ci_images.check_unpublished(plan)
        with patch.object(
            ci_images,
            "run",
            return_value=subprocess.CompletedProcess([], 1, "", "manifest unknown"),
        ):
            ci_images.check_unpublished(plan)

    def test_partial_tiny_publication_still_prevents_overwrite(self):
        plan = ci_images.plan_images("push", "refs/tags/v0.1")

        def inspect(*command):
            return subprocess.CompletedProcess(
                [], 0 if command[-1].endswith("_tiny") else 1, "", "manifest unknown"
            )

        with (
            patch.object(ci_images, "run", side_effect=inspect),
            self.assertRaisesRegex(ValueError, "already exists"),
        ):
            ci_images.check_unpublished(plan)

    def test_digest_parser_uses_index_not_child_manifest(self):
        index, child = "sha256:" + "a" * 64, "sha256:" + "b" * 64
        result = subprocess.CompletedProcess(
            [], 0, f"Name: image\nDigest: {index}\nManifests:\n  Digest: {child}\n", ""
        )
        with patch.object(ci_images, "run", return_value=result):
            self.assertEqual(ci_images.inspect_digest("image"), index)

    def test_both_mirrors_must_match_build_digest(self):
        plan = ci_images.plan_images("push", "refs/heads/main")
        digest = "sha256:" + "a" * 64
        with patch.object(ci_images, "inspect_digest", return_value=digest):
            self.assertEqual(len(ci_images.verify_publication(plan, digest, digest)), 4)
        with (
            patch.object(
                ci_images, "inspect_digest", return_value="sha256:" + "b" * 64
            ),
            patch.object(ci_images.time, "sleep"),
            self.assertRaises(RuntimeError),
        ):
            ci_images.verify_publication(plan, digest, digest)

    def test_offline_defaults_and_explicit_override(self):
        for environment, expected in (
            ({}, False),
            ({"STUDIO_DEFAULT_OFFLINE": "1"}, True),
            ({"STUDIO_DEFAULT_OFFLINE": "1", "STUDIO_OFFLINE": "0"}, False),
            ({"HF_HUB_OFFLINE": "1"}, True),
            ({"STUDIO_OFFLINE": "1"}, True),
        ):
            with patch.dict(os.environ, environment, clear=True):
                self.assertEqual(offline_default(), expected)

    def test_cache_seeding_is_repeatable_and_preserves_other_snapshots(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, destination = root / "baked", root / "cache"
            (source / "repo/blobs").mkdir(parents=True)
            (source / "repo/blobs/abc").write_bytes(b"pinned")
            destination.mkdir()
            (destination / "other-model").write_text("keep", encoding="utf-8")
            self.assertEqual(seed_cache(source, destination), 1)
            self.assertEqual(seed_cache(source, destination), 0)
            self.assertEqual(
                (destination / "other-model").read_text(encoding="utf-8"), "keep"
            )

    def test_cache_rejects_escaping_destination_link(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, destination, outside = (
                root / "baked",
                root / "cache",
                root / "outside",
            )
            for path in (source, destination, outside):
                path.mkdir()
            (source / "repo").mkdir()
            (source / "repo/file").write_text("data", encoding="utf-8")
            try:
                (destination / "repo").symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("Symlink creation requires privileges on this host")
            with self.assertRaises(ValueError):
                seed_cache(source, destination)
            self.assertEqual(list(outside.iterdir()), [])

    def test_release_and_next_snapshot_transformations(self):
        original = self.snapshot_files()
        plan, stable, following = release.prepare(original)
        version = original["VERSION"].strip().removesuffix("-snapshot")
        self.assertEqual(plan["tag"], "v" + version)
        self.assertEqual(stable["VERSION"], version + "\n")
        self.assertIn(f"### v{version}\n", stable["README.md"])
        self.assertIn("verified", stable["docs/dockerhub.md"].lower())
        self.assertIn("-snapshot", following["VERSION"])
        self.assertIn(f"### v{version}\n", following["README.md"])
        with self.assertRaises(ValueError):
            release.prepare(original, version + "-snapshot")

    @staticmethod
    def snapshot_files():
        return {
            "VERSION": "1.2-snapshot\n",
            "src/flux2_klein_studio/__init__.py": '__version__ = "1.2-snapshot"\n',
            "README.md": "Development version **v1.2-snapshot**.\n\n<details open>\n<summary>Development</summary>\n\n### v1.2-snapshot\n\n- A tested change.\n\n</details>\n",
            "docs/dockerhub.md": "**Publication status:** pending.\n\nContainer instructions.\n",
        }

    def test_default_next_snapshot_preserves_version_shape(self):
        for snapshot, expected in (
            ("1.2-snapshot", "1.3-snapshot"),
            ("1.0.0-snapshot", "1.1.0-snapshot"),
            ("1.2.3-snapshot", "1.3.0-snapshot"),
        ):
            with self.subTest(snapshot=snapshot):
                original = {
                    name: text.replace("1.2-snapshot", snapshot)
                    for name, text in self.snapshot_files().items()
                }
                plan, stable, following = release.prepare(original)
                self.assertEqual(plan["next_snapshot"], expected)
                self.assertEqual(following["VERSION"], expected + "\n")
                self.assertEqual(
                    stable["VERSION"], snapshot.removesuffix("-snapshot") + "\n"
                )
                self.assertEqual(plan["tag"], "v" + snapshot.removesuffix("-snapshot"))

    def test_release_dry_run_does_not_mutate_files_or_refs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for file, content in self.snapshot_files().items():
                (root / file).parent.mkdir(parents=True, exist_ok=True)
                (root / file).write_text(content, encoding="utf-8")
            before = {file: (root / file).read_bytes() for file in release.FILES}
            with (
                patch.object(release, "git", side_effect=["main", "", "dirty"]),
                patch.object(release.subprocess, "run") as command,
            ):
                release.run(root, dry_run=True)
                command.assert_not_called()
            self.assertEqual(
                before, {file: (root / file).read_bytes() for file in release.FILES}
            )

    def test_source_manifest_detects_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "input").write_bytes(b"source")
            entry = dict(
                file="input", size=6, sha256=hashlib.sha256(b"source").hexdigest()
            )
            (root / "manifest.json").write_text(
                json.dumps(dict(source_collection_complete=True, files=[entry])),
                encoding="utf-8",
            )
            sources.verify(root)
            (root / "input").write_bytes(b"broken")
            with self.assertRaises(ValueError):
                sources.verify(root)

    def test_source_paths_cannot_escape(self):
        for path in ("../file", "/file", "a/../file", "C:/file", "a\\file", "a//file"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                sources.safe_path(path)


if __name__ == "__main__":
    unittest.main()
