#!/usr/bin/env python3
"""Build a local, read-only website package. Never upload or publish anything."""
import hashlib
import shutil
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit
from zipfile import ZIP_DEFLATED, ZipFile

from PIL import Image

import prepare

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_CONFIG = (
    "defaultCenter", "defaultZoom", "basemap", "streetData", "streetLabels",
    "areaPadding", "panPadding", "tileUrl", "tileAttribution", "maxZoom",
)
PUBLIC_PHOTO = (
    "id", "coordinates", "tags", "width", "height", "thumbnailWidth",
    "thumbnailHeight", "thumbnailVisibleArea",
)
FONT = "TheBasics-Medium.f09ac54aeaa52c1d39feebf6e99e784c.woff2"


class PublicHTML(HTMLParser):
    """Remove local editor forms and script while preserving the map markup."""
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.output = []
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        remove = attributes.get("id") in {"archive-editor", "close-editor-dialog", "editor-button"}
        remove |= tag == "script" and urlsplit(attributes.get("src", "")).path == "editor.js"
        if self.depth or remove:
            if tag not in self.VOID:
                self.depth += 1
        else:
            self.output.append(self.get_starttag_text())

    def handle_startendtag(self, tag, attrs):
        if not self.depth:
            self.output.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        if self.depth:
            if tag not in self.VOID:
                self.depth -= 1
        else:
            self.output.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.depth:
            self.output.append(data)

    def handle_decl(self, decl):
        if not self.depth:
            self.output.append(f"<!{decl}>")

    def handle_entityref(self, name):
        self.handle_data(f"&{name};")

    def handle_charref(self, name):
        self.handle_data(f"&#{name};")

    def handle_comment(self, data):
        if not self.depth:
            self.output.append(f"<!--{data}-->")


def source_path(url):
    parsed = urlsplit(url)
    if parsed.scheme or parsed.netloc:
        raise ValueError(f"Expected a local file: {url}")
    path = (ROOT / unquote(parsed.path)).resolve()
    if not path.is_relative_to(ROOT) or not path.is_file():
        raise ValueError(f"Missing or invalid site file: {url}")
    return path


def copy_asset(url, destination):
    source = source_path(url)
    target = destination / source.relative_to(ROOT)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def export_site():
    config = prepare.read_json(ROOT / "config.json", {})
    annotations = prepare.read_json(ROOT / "data/annotations.json", {})
    photos, _ = prepare.prepare(config, annotations, save=False)
    if not photos:
        raise ValueError("There are no photos to export.")
    with tempfile.TemporaryDirectory(prefix=".publish-build-", dir=ROOT) as temporary:
        stage = Path(temporary) / "dist"
        stage.mkdir()
        for name in ("styles.css", "app.js", "map-display.js", FONT):
            copy_asset(name, stage)
        shutil.copytree(ROOT / "vendor", stage / "vendor", ignore=shutil.ignore_patterns(".*"))
        html = PublicHTML()
        html.feed((ROOT / "index.html").read_text(encoding="utf-8"))
        html.close()
        (stage / "index.html").write_text("".join(html.output), encoding="utf-8")
        public_config = {key: config[key] for key in PUBLIC_CONFIG if key in config}
        public_config["readOnly"] = True
        if config.get("basemap") == "diagram":
            for key in ("streetData", "streetLabels"):
                copy_asset(config[key], stage)
        prepare.write_json(stage / "config.json", public_config)
        viewing = stage / "assets/photos"
        viewing.mkdir(parents=True)
        exported = []
        for photo in photos:
            source = source_path(photo["image"])
            stem = hashlib.sha256(photo["id"].encode()).hexdigest()[:20]
            with Image.open(source) as original:
                image = prepare.web_rgb(original)
                image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
                image.info.clear()
                image.save(viewing / f"{stem}.jpg", "JPEG", quality=88, optimize=True, progressive=True)
            copy_asset(photo["thumbnail"], stage)
            item = {key: photo[key] for key in PUBLIC_PHOTO if key in photo}
            item.update(image=f"assets/photos/{stem}.jpg", thumbnail=photo["thumbnail"])
            exported.append(item)
        prepare.write_json(stage / "data/photos.json", exported)
        # Empty marker also makes the folder suitable for hosts using Jekyll by default.
        (stage / ".nojekyll").touch()
        archive = Path(temporary) / "signs-publish.zip"
        with ZipFile(archive, "w", ZIP_DEFLATED) as bundle:
            for path in sorted(stage.rglob("*")):
                if path.is_file():
                    bundle.write(path, path.relative_to(stage).as_posix())
        destination = ROOT / "dist"
        if destination.exists():
            shutil.rmtree(destination)
        shutil.move(str(stage), destination)
        shutil.move(str(archive), ROOT / "signs-publish.zip")
    size = sum(path.stat().st_size for path in destination.rglob("*") if path.is_file())
    print(f"Ready locally: dist/ and signs-publish.zip ({len(exported)} signs, {size / 1024**2:.1f} MB).")
    print("No uploads or publication performed. Only saved project metadata is included.")


if __name__ == "__main__":
    export_site()
