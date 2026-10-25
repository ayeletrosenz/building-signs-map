"""Editor regression checks. All writes and network requests stay in temporary fixtures."""
import copy
import hashlib
import json
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageCms

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import serve


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="signs-test-")
        self.root = Path(self.temporary.name).resolve()
        self.old_root, self.old_prepare_root = serve.ROOT, serve.prepare.ROOT
        serve.ROOT = serve.prepare.ROOT = self.root
        (self.root / "originals").mkdir()
        (self.root / "crops").mkdir()
        exif = Image.Exif(); exif[274] = 6
        Image.new("RGB", (400, 200), "red").save(self.root / "originals/one.jpg", exif=exif)
        Image.new("RGB", (200, 300), "blue").save(self.root / "originals/two.jpg")
        self.config = {"originals":"originals", "crops":"crops", "categories":{"type":"סוג"},
                       "categoryValues":{}, "keepSetting":{"a":1}}
        self.annotations = {"one.jpg":{"coordinates":[31.775,35.215],"tags":{"type":["קיים"]},"note":"preserve"},
                            "two.jpg":{"coordinates":None,"tags":{}}}
        serve.prepare.write_json(self.root / "config.json", self.config)
        serve.prepare.write_json(self.root / "data/annotations.json", self.annotations)
        serve.prepare.prepare()
        self.hashes = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (self.root / "originals").iterdir()}

    def tearDown(self):
        for name, digest in self.hashes.items():
            self.assertEqual(hashlib.sha256((self.root / "originals" / name).read_bytes()).hexdigest(), digest)
        serve.ROOT, serve.prepare.ROOT = self.old_root, self.old_prepare_root
        self.temporary.cleanup()

    def payload(self):
        state = serve.editor_state()
        return {"revision":state["revision"], "categories":state["config"]["categories"],
                "categoryValues":state["config"].get("categoryValues",{}), "annotations":state["annotations"]}

    def metadata(self):
        return {name:(self.root/name).read_bytes() for name in serve.METADATA}

    def test_save_preserves_fields_and_persists_tags_address_and_categories(self):
        payload = self.payload(); payload["categories"]["custom"] = "חומר"
        payload["categoryValues"]["custom"] = ["אבן", "מתכת"]
        payload["annotations"]["one.jpg"]["tags"]["custom"] = ["אבן"]
        payload["annotations"]["one.jpg"].update(address="רחוב לדוגמה 7, ירושלים", coordinates=[31.776,35.216])
        saved = serve.save_editor(payload)
        self.assertNotEqual(saved["revision"], payload["revision"])
        self.assertEqual(saved["config"]["keepSetting"], {"a":1})
        self.assertEqual(saved["annotations"]["one.jpg"]["note"], "preserve")
        self.assertEqual(saved["photos"][0]["tags"], {"type":["קיים"], "custom":["אבן"]})
        self.assertEqual(saved["photos"][0]["coordinates"], [31.776,35.216])
        self.assertEqual(saved["photos"][0]["address"], "רחוב לדוגמה 7, ירושלים")
        self.assertEqual(serve.editor_state()["annotations"], saved["annotations"])
        self.assertTrue(list((self.root / ".editor/backups").glob("*/data/annotations.json")))
        payload = self.payload(); payload["categories"]["custom"] = "חומר השלט"
        payload["annotations"]["one.jpg"]["tags"]["custom"] = []
        saved = serve.save_editor(payload)
        self.assertEqual(saved["photos"][0]["tags"]["type"], ["קיים"])
        self.assertEqual(saved["photos"][0]["tags"]["custom"], [])

    def test_conflicting_or_invalid_updates_leave_all_metadata_intact(self):
        payload = self.payload(); payload["revision"] = "stale"; before = self.metadata()
        with self.assertRaises(serve.EditorError) as conflict:
            serve.save_editor(payload)
        self.assertEqual(conflict.exception.status, 409)
        self.assertEqual(self.metadata(), before)

        for coords in ([91,0], [0,float("nan")], [True,0]):
            payload = self.payload(); payload["annotations"]["one.jpg"]["coordinates"] = coords
            with self.assertRaises(serve.EditorError): serve.save_editor(payload)
            self.assertEqual(self.metadata(), before)
        payload = self.payload(); payload["annotations"]["../../outside.json"] = {"tags":{}}
        with self.assertRaises(serve.EditorError): serve.save_editor(payload)
        self.assertEqual(self.metadata(), before)

    def test_flat_tag_storage_preserves_legacy_labels_and_photo_details(self):
        payload=self.payload()
        payload["categories"]["tags"]="תגיות"
        payload["categoryValues"]["tags"]=["קיים","כתב יד"]
        payload["annotations"]["one.jpg"]["tags"]={"type":[],"tags":["קיים","כתב יד"]}
        saved=serve.save_editor(payload)
        self.assertEqual(saved["annotations"]["one.jpg"]["tags"]["tags"],["קיים","כתב יד"])
        self.assertEqual(saved["annotations"]["one.jpg"]["coordinates"],[31.775,35.215])
        self.assertEqual(saved["annotations"]["one.jpg"]["note"],"preserve")
        payload=self.payload();payload["annotations"]["one.jpg"]["tags"]["tags"]=["קיים"]
        saved=serve.save_editor(payload)
        self.assertEqual(saved["photos"][0]["tags"]["tags"],["קיים"])
        self.assertIn("כתב יד",saved["config"]["categoryValues"]["tags"])

    def test_failed_preparation_or_disk_commit_preserves_metadata(self):
        before = self.metadata()
        Image.new("RGBA", (40,40), (0,0,0,0)).save(self.root / "crops/one.png")
        with self.assertRaises(ValueError): serve.save_editor(self.payload())
        self.assertEqual(self.metadata(), before)
        (self.root / "crops/one.png").unlink()
        original_writer = serve.prepare.write_json
        def fail_annotations(path, data):
            if path == self.root / "data/annotations.json": raise OSError("simulated disk error")
            original_writer(path, data)
        payload = self.payload(); payload["categories"]["type"] = "שם חדש"
        with patch.object(serve.prepare, "write_json", side_effect=fail_annotations):
            with self.assertRaises(OSError): serve.save_editor(payload)
        self.assertEqual(self.metadata(), before)

    def test_transparent_cutout_holes_profile_orientation_and_fallback(self):
        crop = Image.new("RGBA", (250,150), (0,0,0,0)); draw = ImageDraw.Draw(crop)
        draw.polygon([(20,60),(80,30),(230,70),(180,120),(40,100)], fill=(180,30,80,128))
        draw.rectangle((100,60,140,80), fill=(0,0,0,0))
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        crop.save(self.root / "crops/one.png", icc_profile=profile)
        photos, _ = serve.prepare.prepare()
        thumb_path = self.root / photos[0]["thumbnail"].split("?")[0]
        with Image.open(thumb_path) as thumbnail:
            self.assertEqual(thumbnail.format, "PNG")
            self.assertEqual(thumbnail.mode, "RGBA")
            self.assertEqual(thumbnail.getpixel((0,0))[3], 0)
            self.assertEqual(thumbnail.getpixel((thumbnail.width//2,thumbnail.height//2))[3], 0)
            self.assertTrue(any(90 < alpha < 160 for alpha in thumbnail.getchannel("A").getdata()))
            self.assertEqual(photos[0]["thumbnailWidth"],thumbnail.width)
            self.assertEqual(photos[0]["thumbnailHeight"],thumbnail.height)
            histogram=thumbnail.getchannel("A").histogram()
            self.assertAlmostEqual(photos[0]["thumbnailVisibleArea"],sum(i*n for i,n in enumerate(histogram))/255,places=2)
        self.assertEqual(photos[0]["image"], "originals/one.jpg")
        (self.root / "crops/one.png").unlink(); photos, _ = serve.prepare.prepare()
        with Image.open(self.root / photos[0]["thumbnail"].split("?")[0]) as fallback:
            self.assertEqual(fallback.format, "JPEG")
            self.assertEqual(fallback.size, (96,192))
            self.assertEqual(photos[0]["thumbnailVisibleArea"],96*192)

    def test_crop_export_copy_suffix_and_exact_name_precedence(self):
        copy_crop = self.root / "crops/one copy.png"
        Image.new("RGBA", (120,40), (120,30,80,255)).save(copy_crop)
        photos, _ = serve.prepare.prepare()
        self.assertTrue(photos[0]["thumbnail"].split("?")[0].endswith(".png"))
        self.assertEqual(serve.prepare.find_crop(self.root / "crops/one.jpg"), copy_crop)
        exact = self.root / "crops/one.png"
        Image.new("RGBA", (90,30), (30,120,80,255)).save(exact)
        self.assertEqual(serve.prepare.find_crop(self.root / "crops/one.jpg"), exact)
        exact.unlink()
        Image.new("RGBA", (90,30), (30,120,80,255)).save(self.root / "crops/one copy 2.png")
        with self.assertRaises(ValueError): serve.prepare.find_crop(self.root / "crops/one.jpg")

    def test_http_origin_host_and_private_paths(self):
        class QuietHandler(serve.Handler):
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(("127.0.0.1",0), QuietHandler)
        thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(base+"/api/editor") as response:
                self.assertEqual(response.status,200)
            for path, headers in [("/api/editor",{"Origin":"https://elsewhere.example"}),
                                  ("/api/editor",{"Origin":"null"}),
                                  ("/api/editor",{"Host":"evil.example"}),
                                  ("/.editor/backups/file.json",{}), ("/scripts/serve.py",{})]:
                with self.assertRaises(HTTPError) as failure: urlopen(Request(base+path,headers=headers))
                self.assertIn(failure.exception.code,(403,404))
                failure.exception.close()
            before = self.metadata()
            request = Request(base+"/api/editor",data=json.dumps(self.payload()).encode(),
                              headers={"Content-Type":"application/json","Origin":"https://elsewhere.example"})
            with self.assertRaises(HTTPError) as failure: urlopen(request)
            self.assertEqual(failure.exception.code,403)
            failure.exception.close()
            self.assertEqual(self.metadata(),before)
            with urlopen(base+"/data/photos.json") as response:
                self.assertEqual(response.headers["Cache-Control"],"no-store")
            (self.root / "index.html").write_text("<!doctype html><title>test</title>")
            (self.root / "app.js").write_text("const fixture = true;")
            for path in ("/", "/app.js?v=fixture"):
                with urlopen(base+path) as response:
                    self.assertEqual(response.status,200)
                    self.assertEqual(response.headers["Cache-Control"],"no-store")
        finally: server.shutdown();server.server_close();thread.join()

    def test_geocoding_is_serialized_cached_and_does_not_change_annotations(self):
        calls = []
        class FakeProvider(BaseHTTPRequestHandler):
            def do_GET(self):
                query = parse_qs(urlsplit(self.path).query); calls.append((time.monotonic(),query))
                if query["q"][0] == "failure":
                    self.send_error(503);return
                data = [] if query["q"][0] == "missing" else [{"lat":"31.775","lon":"35.215","display_name":"כתובת בדיקה"}]
                body=json.dumps(data).encode();self.send_response(200);self.end_headers();self.wfile.write(body)
            def log_message(self,*args): pass
        server = ThreadingHTTPServer(("127.0.0.1",0), FakeProvider)
        thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            self.config["geocoding"]={"endpoint":f"http://127.0.0.1:{server.server_port}/search"}
            serve.prepare.write_json(self.root / "config.json",self.config)
            before=self.metadata()
            self.assertEqual(serve.geocode("sample")["results"][0]["coordinates"],[31.775,35.215])
            self.assertTrue(serve.geocode("sample")["cached"])
            self.assertEqual(serve.geocode("missing")["results"],[])
            self.assertEqual(len(calls),2)
            self.assertGreaterEqual(calls[1][0]-calls[0][0],1.0)
            self.assertEqual(set(calls[0][1]),{"q","format","addressdetails","limit","accept-language"})
            self.assertEqual(self.metadata(),before)
            self.assertTrue((self.root / ".editor/geocode-cache.json").exists())
            with self.assertRaises(serve.EditorError) as failure: serve.geocode("failure")
            self.assertEqual(failure.exception.status,502)
            self.assertEqual(len(calls),3)
            self.assertEqual(self.metadata(),before)
        finally: server.shutdown();server.server_close();thread.join()


if __name__ == "__main__":
    unittest.main()
