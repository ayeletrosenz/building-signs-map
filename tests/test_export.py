"""Public export checks use temporary archives and never upload anything."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import export_site


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        for name in ("originals", "data", "vendor", ".editor"):
            (self.root / name).mkdir()
        exif = Image.Exif()
        exif[274] = 6
        exif[315] = "Private camera metadata"
        Image.new("RGB", (2500, 1250), "red").save(self.root / "originals/one.jpg", exif=exif)
        config = {"originals": "originals", "crops": "crops", "categories": {"tags": "תגיות"},
                  "geocoding": {"endpoint": "https://example.invalid"}, "basemap": "diagram",
                  "streetData": "data/roads.json?v=1", "streetLabels": "data/labels.json"}
        entry = {"coordinates": [31.77, 35.21], "address": "Private address", "note": "Local note",
                 "tags": {"tags": ["כתב יד"]}}
        for name, value in (("config.json", config), ("data/annotations.json", {"one.jpg": entry}),
                            ("data/roads.json", {}), ("data/labels.json", {})):
            (self.root / name).write_text(json.dumps(value))
        (self.root / "index.html").write_text('''<!doctype html><html><head>
<script src="editor.js?v=1"></script><script src="app.js"></script></head><body>
<div id="map"></div><button id="editor-button">Edit</button>
<section id="archive-editor"><section><input><img src="private.png"></section></section>
<dialog id="close-editor-dialog">Private form</dialog><p>Public archive</p></body></html>''')
        for name in ("app.js", "styles.css", "map-display.js", export_site.FONT, "vendor/LICENSE"):
            (self.root / name).write_text("fixture")
        (self.root / ".editor/private.json").write_text("private")
        self.addCleanup(patch.stopall)
        patch.object(export_site, "ROOT", self.root).start()
        patch.object(export_site.prepare, "ROOT", self.root).start()

    def build(self):
        with contextlib.redirect_stdout(io.StringIO()):
            export_site.export_site()

    def test_export_is_read_only_complete_and_preserves_source(self):
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.build()
        dist = self.root / "dist"
        config = json.loads((dist / "config.json").read_text())
        self.assertTrue(config["readOnly"])
        self.assertNotIn("geocoding", config)
        self.assertNotIn("originals", config)
        html = (dist / "index.html").read_text()
        for text in ("editor.js", "archive-editor", "editor-button", "close-editor-dialog", "private.png"):
            self.assertNotIn(text, html)
        self.assertIn("Public archive", html)
        photo, = json.loads((dist / "data/photos.json").read_text())
        self.assertEqual(photo["tags"], {"tags": ["כתב יד"]})
        self.assertEqual(photo["coordinates"], [31.77, 35.21])
        for key in ("address", "exifCoordinates", "note"):
            self.assertNotIn(key, photo)
        with Image.open(dist / photo["image"]) as image:
            self.assertEqual(image.size, (1024, 2048))
            self.assertFalse(image.getexif())
        self.assertTrue((dist / photo["thumbnail"].split("?")[0]).is_file())
        with ZipFile(self.root / "signs-publish.zip") as bundle:
            self.assertIn("index.html", bundle.namelist())
            self.assertIn("vendor/LICENSE", bundle.namelist())
            self.assertNotIn("data/annotations.json", bundle.namelist())
            self.assertFalse(any(name.startswith(("originals/", ".editor/")) for name in bundle.namelist()))
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data, path)

    def test_failed_export_keeps_previous_package(self):
        self.build()
        before = (self.root / "signs-publish.zip").read_bytes()
        html = (self.root / "dist/index.html").read_bytes()
        (self.root / "data/roads.json").unlink()
        with self.assertRaises(ValueError):
            self.build()
        self.assertEqual((self.root / "signs-publish.zip").read_bytes(), before)
        self.assertEqual((self.root / "dist/index.html").read_bytes(), html)
