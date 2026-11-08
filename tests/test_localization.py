"""Catalog and packaged-route guards; no CUDA or external translation service."""

from html.parser import HTMLParser
import json
from pathlib import Path
import re
import tempfile
import unittest

from fastapi.testclient import TestClient

from flux2_klein_studio.server import create_app, STATIC

LOCALES = ("en", "nb", "pl", "ja", "zh", "es")
ROOT = Path(__file__).resolve().parents[1]


class LocalizationTests(unittest.TestCase):
    def test_translated_guides_keep_commands_notices_and_honest_english_links(self):
        def image_references(text):
            return re.findall(
                r"^docker run [^\n]* (hangrylabs/flux2-klein-studio:[^\s]+)$",
                text,
                re.M,
            )

        # Release quick starts pin stable indexes; development guides may use
        # rolling tags. Translations must retain the English deployment choices.
        expected = image_references((ROOT / "README.md").read_text(encoding="utf-8"))[
            :2
        ]
        self.assertEqual(len(expected), 2)
        for suffix, reference in zip(("", "_tiny"), expected):
            self.assertRegex(
                reference,
                r"^hangrylabs/flux2-klein-studio:(?:latest"
                + suffix
                + r"|v\d+\.\d+(?:\.\d+)?"
                + suffix
                + r"@sha256:[a-f0-9]{64})$",
            )
        self.assertEqual(
            image_references((ROOT / "docs/dockerhub.md").read_text(encoding="utf-8")),
            expected,
        )
        for locale in LOCALES[1:]:
            paths = (
                ROOT / f"README.{locale}.md",
                ROOT / f"docs/dockerhub.{locale}.md",
                ROOT / f"docs/{locale}/README.md",
            )
            for path in paths:
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("\ufffd", text)
                self.assertIn("flux2_klein_studio_data", text)
                self.assertIn("/app/persistent", text)
                for notice in ("LICENSE", "NOTICE", "THIRD_PARTY.md"):
                    self.assertIn(notice, text)
            readme = paths[0].read_text(encoding="utf-8")
            self.assertIn("--gpus all --init -p 127.0.0.1:7860:7860", readme)
            self.assertEqual(image_references(readme), expected, locale)
            self.assertIn("README.md#version-history", readme)
            docker = paths[1].read_text(encoding="utf-8")
            self.assertEqual(image_references(docker), expected, locale)
            self.assertLess(len(docker.encode("utf-8")), 25000)
            self.assertIn(
                "https://raw.githubusercontent.com/hangry-labs/flux2-klein-studio/main/assets/",
                docker,
            )
            self.assertIn("../agent-api.md", paths[2].read_text(encoding="utf-8"))

    def test_catalog_key_parity_placeholders_and_utf8(self):
        english = json.loads((STATIC / "locales/en.json").read_text(encoding="utf-8"))
        for locale in LOCALES:
            catalog = json.loads(
                (STATIC / f"locales/{locale}.json").read_text(encoding="utf-8")
            )
            self.assertEqual(set(catalog), set(english), locale)
            for key, value in catalog.items():
                self.assertIsInstance(value, str)
                self.assertTrue(value.strip(), (locale, key))
                self.assertNotIn("\ufffd", value, (locale, key))
                self.assertEqual(
                    sorted(re.findall(r"\{\d+\}", value)),
                    sorted(re.findall(r"\{\d+\}", english[key])),
                    (locale, key),
                )

    def test_static_markers_exist_and_do_not_contain_controls(self):
        catalog = json.loads((STATIC / "locales/en.json").read_text(encoding="utf-8"))
        case = self

        class Markers(HTMLParser):
            def __init__(self):
                super().__init__()
                self.marked = False
                self.ids = []

            def handle_starttag(self, tag, attributes):
                attrs = dict(attributes)
                if tag in {"input", "button", "select", "textarea"}:
                    case.assertFalse(self.marked, (tag, attrs))
                if "id" in attrs:
                    self.ids.append(attrs["id"])
                for name, value in attrs.items():
                    if name.startswith("data-i18n"):
                        case.assertIn(value, catalog)
                if tag == "span" and "data-i18n" in attrs:
                    self.marked = True

            def handle_endtag(self, tag):
                if tag == "span":
                    self.marked = False

        parser = Markers()
        parser.feed((STATIC / "index.html").read_text(encoding="utf-8"))
        self.assertEqual(len(parser.ids), len(set(parser.ids)))

    def test_direct_and_trailing_slash_locales_and_api_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(Path(directory))
            with TestClient(app) as client:
                for locale in LOCALES:
                    for suffix in ("", "/"):
                        response = client.get(f"/{locale}{suffix}")
                        self.assertEqual(response.status_code, 200)
                        self.assertIn(f'<html lang="{locale}">', response.text)
                    self.assertEqual(
                        client.get(f"/static/locales/{locale}.json").status_code, 200
                    )
                self.assertEqual(client.get("/pt").status_code, 404)
                self.assertEqual(client.get("/v1/models").status_code, 200)
                self.assertEqual(client.get("/health/ready").status_code, 200)
                self.assertEqual(client.get("/openapi.json").status_code, 200)
                response = client.post(
                    "/mcp/",
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                    headers={"Accept": "application/json, text/event-stream"},
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn("tools", response.json()["result"])
