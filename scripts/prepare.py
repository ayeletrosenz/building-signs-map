#!/usr/bin/env python3
"""Read originals; write only separate derivatives and editable archive data."""
import hashlib
import io
import json
import math
import copy
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote

from PIL import ExifTags, Image, ImageCms, ImageOps

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parents[1]
FORMATS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".tif", ".tiff", ".bmp", ".heic", ".heif", ".avif"}
WEB_FORMATS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}


def read_json(path, fallback):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else fallback


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def valid_coordinates(value):
    return (isinstance(value, list) and len(value) == 2
            and all(isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n) for n in value)
            and -90 <= value[0] <= 90 and -180 <= value[1] <= 180)


def gps_coordinates(image):
    try:
        gps = image.getexif().get_ifd(ExifTags.IFD.GPSInfo)
        def decimal(values, ref):
            degrees = float(values[0]) + float(values[1]) / 60 + float(values[2]) / 3600
            if isinstance(ref, bytes):
                ref = ref.decode("ascii")
            return -degrees if ref in ("S", "W") else degrees
        if gps.get(1) not in ("N", "S", b"N", b"S") or gps.get(3) not in ("E", "W", b"E", b"W"):
            return None
        coords = [round(decimal(gps[2], gps[1]), 7), round(decimal(gps[4], gps[3]), 7)]
        return coords if valid_coordinates(coords) else None
    except (KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError):
        return None


def web_rgb(image):
    """Bake phone orientation and convert embedded display profiles to sRGB."""
    result = ImageOps.exif_transpose(image)
    profile = image.info.get("icc_profile")
    if profile:
        try:
            result = ImageCms.profileToProfile(result, ImageCms.ImageCmsProfile(io.BytesIO(profile)),
                                               ImageCms.createProfile("sRGB"), outputMode="RGB")
        except (ImageCms.PyCMSError, OSError, ValueError):
            result = result.convert("RGB")
    elif result.mode in ("RGBA", "LA") or "transparency" in result.info:
        rgba = result.convert("RGBA")
        result = Image.new("RGB", rgba.size, "white")
        result.paste(rgba, mask=rgba.getchannel("A"))
    else:
        result = result.convert("RGB")
    return result


def web_thumbnail(image):
    """Retain cutout alpha while applying phone orientation and sRGB conversion."""
    oriented = ImageOps.exif_transpose(image)
    alpha = oriented.convert("RGBA").getchannel("A") if (oriented.mode in ("RGBA", "LA") or "transparency" in oriented.info) else None
    if alpha is not None:
        bbox = alpha.getbbox()
        if bbox is None:
            raise ValueError("crop is entirely transparent")
        oriented = oriented.crop(bbox)
        oriented.thumbnail((192, 192), Image.Resampling.LANCZOS)
        alpha = oriented.convert("RGBA").getchannel("A")
        # Color conversion must not flatten semi-transparent pixels onto white.
        rgb = web_rgb(oriented.convert("RGB"))
        rgb.putalpha(alpha)
        result = rgb
    else:
        oriented.thumbnail((192, 192), Image.Resampling.LANCZOS)
        result = web_rgb(oriented)
    return result, alpha is not None


def url(path):
    return quote(path.relative_to(ROOT).as_posix(), safe="/")


def find_crop(expected):
    """Prefer exact names; accept common editor export names such as 'name copy.png'."""
    if expected.is_file():
        return expected
    siblings = [p for p in expected.parent.iterdir() if p.is_file() and p.suffix.lower() in FORMATS] if expected.parent.is_dir() else []
    alternatives = [p for p in siblings if p.stem.casefold() == expected.stem.casefold()]
    if not alternatives:
        alternatives = [p for p in siblings if re.sub(r" copy(?: \d+)?$", "", p.stem, flags=re.IGNORECASE).casefold() == expected.stem.casefold()]
    if len(alternatives) > 1:
        raise ValueError("multiple crops match this filename; keep one or use the exact filename")
    return alternatives[0] if alternatives else None


def prepare(config=None, annotations=None, save=True):
    config = copy.deepcopy(config if config is not None else read_json(ROOT / "config.json", {}))
    originals = (ROOT / config.get("originals", "signs_images")).resolve()
    crops = (ROOT / config.get("crops", "cropped_images")).resolve()
    if not originals.is_dir():
        raise ValueError("Original photo directory does not exist: " + str(originals))
    annotations_path = ROOT / "data/annotations.json"
    annotations = copy.deepcopy(annotations if annotations is not None else read_json(annotations_path, {}))
    if not isinstance(annotations, dict):
        raise ValueError("data/annotations.json must be an object keyed by filename.")
    (ROOT / "assets/thumbnails").mkdir(parents=True, exist_ok=True)
    (ROOT / "assets/converted").mkdir(parents=True, exist_ok=True)
    photos, errors, crop_count = [], [], 0
    for path in sorted(originals.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in FORMATS:
            continue
        key = path.relative_to(originals).as_posix()
        entry = annotations.setdefault(key, {"coordinates": None, "tags": {k: [] for k in config["categories"]}})
        try:
            manual = entry.get("coordinates")
            if manual is not None and not valid_coordinates(manual):
                raise ValueError("coordinates must be null or [latitude, longitude] in valid ranges")
            tags = entry.get("tags", {})
            if not isinstance(tags, dict) or any(not isinstance(v, list) or any(not isinstance(t, str) or not t.strip() for t in v) for v in tags.values()):
                raise ValueError("tags must contain arrays of nonempty strings")
            with Image.open(path) as original:
                gps = gps_coordinates(original)
                dimensions = original.size
                image_url = url(path)
                digest = hashlib.sha256(key.encode()).hexdigest()[:20]
                if path.suffix.lower() not in WEB_FORMATS:
                    converted = ROOT / "assets/converted" / (digest + ".jpg")
                    if not converted.exists() or converted.stat().st_mtime < path.stat().st_mtime:
                        web_rgb(original).save(converted, quality=95)
                    image_url = url(converted)
                crop = find_crop(crops / key) or path
                crop_count += crop != path
                fingerprint = hashlib.sha256((str(crop) + str(crop.stat().st_mtime_ns) + str(crop.stat().st_size) + "v2-alpha").encode()).hexdigest()[:12]
                stamp = ROOT / "assets/thumbnails" / (digest + ".fingerprint")
                with Image.open(crop) as source:
                    has_alpha = source.mode in ("RGBA", "LA") or "transparency" in source.info
                    thumb = ROOT / "assets/thumbnails" / (digest + (".png" if has_alpha else ".jpg"))
                    if not thumb.exists() or not stamp.exists() or stamp.read_text() != fingerprint:
                        small, has_alpha = web_thumbnail(source)
                        if has_alpha:
                            small.save(thumb, optimize=True)
                        else:
                            small.save(thumb, quality=85, optimize=True)
                        stamp.write_text(fingerprint)
                with Image.open(thumb) as thumbnail:
                    thumb_width, thumb_height = thumbnail.size
                    if thumbnail.mode in ("RGBA", "LA") or "transparency" in thumbnail.info:
                        histogram = thumbnail.convert("RGBA").getchannel("A").histogram()
                        visible_area = sum(alpha * count for alpha, count in enumerate(histogram)) / 255
                    else:
                        visible_area = thumb_width * thumb_height
                coords = manual if manual is not None else gps
                photos.append({"id": key, "filename": key, "image": image_url,
                               "thumbnail": url(thumb) + "?v=" + fingerprint,
                               "thumbnailWidth": thumb_width, "thumbnailHeight": thumb_height,
                               "thumbnailVisibleArea": round(visible_area, 3),
                               "coordinates": coords, "coordinateSource": "manual" if manual is not None else "exif" if gps else None,
                               "exifCoordinates": gps, "address": entry.get("address", ""),
                               "width": dimensions[0], "height": dimensions[1], "tags": tags})
        except Exception as exc:
            errors.append(key + ": " + str(exc))
    if errors:
        raise ValueError("Preparation stopped; existing archive data was preserved.\n" + "\n".join(errors)
                         + "\nFor HEIC/HEIF support, install pillow-heif in the same environment.")
    if save:
        write_json(annotations_path, annotations)
        write_json(ROOT / "data/photos.json", photos)
    located = sum(p["coordinates"] is not None for p in photos)
    print(f"Prepared {len(photos)} photographs: {located} located, {len(photos) - located} need placement.")
    print(f"Using {crop_count} cropped thumbnails; {len(photos) - crop_count} fall back to originals.")
    print("Original files are read only. Thumbnails: assets/thumbnails. Tags and manual locations: data/annotations.json.")
    return photos, annotations


if __name__ == "__main__":
    try:
        prepare()
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
