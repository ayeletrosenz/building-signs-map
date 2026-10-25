#!/usr/bin/env python3
"""Local-only archive editor. Published files remain an ordinary static website."""
import argparse
import copy
import hashlib
import json
import os
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlsplit
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

import prepare

ROOT = Path(__file__).resolve().parents[1]
METADATA = ("config.json", "data/annotations.json", "data/photos.json")
save_lock = threading.RLock()
geocode_lock = threading.Lock()
last_geocode = 0.0


class EditorError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def revision():
    digest = hashlib.sha256()
    for name in METADATA:
        digest.update(name.encode())
        digest.update((ROOT / name).read_bytes())
    return digest.hexdigest()


def editor_state():
    with save_lock:
        return {"revision": revision(), "config": prepare.read_json(ROOT / "config.json", {}),
                "annotations": prepare.read_json(ROOT / "data/annotations.json", {}),
                "photos": prepare.read_json(ROOT / "data/photos.json", [])}


def text(value, maximum=100):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise EditorError("יש להזין טקסט תקין וקצר.")
    return value.strip()


def tag_values(value):
    if not isinstance(value, list) or len(value) > 250:
        raise EditorError("ערכי הסינון אינם תקינים.")
    return list(dict.fromkeys(text(v) for v in value))


def save_editor(payload):
    if not isinstance(payload, dict):
        raise EditorError("נתוני השמירה אינם תקינים.")
    with save_lock:
        current = editor_state()
        if payload.get("revision") != current["revision"]:
            raise EditorError("הקבצים השתנו מאז פתיחת העורך. הטיוטה נשמרה; יש לטעון מחדש את נתוני הפרויקט לפני שמירה.", 409)
        config = copy.deepcopy(current["config"])
        annotations = copy.deepcopy(current["annotations"])
        categories, choices = payload.get("categories"), payload.get("categoryValues")
        edits = payload.get("annotations")
        if not isinstance(categories, dict) or len(categories) > 50 or not isinstance(choices, dict) or not isinstance(edits, dict):
            raise EditorError("מבנה הקטגוריות או התמונות אינו תקין.")
        for key, label in categories.items():
            if not isinstance(key, str) or not key or len(key) > 100 or key in ("__proto__", "prototype", "constructor"):
                raise EditorError("מזהה הקטגוריה אינו תקין.")
            config.setdefault("categories", {})[key] = text(label, 80)
        for key, values in choices.items():
            if key not in config["categories"]:
                raise EditorError("הקטגוריה אינה קיימת.")
            config.setdefault("categoryValues", {})[key] = tag_values(values)
        known = {photo["id"] for photo in current["photos"]} | set(annotations)
        for filename, entry in edits.items():
            if filename not in known or not isinstance(entry, dict):
                raise EditorError("התמונה אינה קיימת בארכיון.")
            previous = annotations.setdefault(filename, {"coordinates": None, "tags": {}})
            if "coordinates" in entry:
                value = entry["coordinates"]
                if value is not None and not prepare.valid_coordinates(value):
                    raise EditorError("הקואורדינטות אינן תקינות.")
                previous["coordinates"] = value
            if "address" in entry:
                if not isinstance(entry["address"], str) or len(entry["address"]) > 400:
                    raise EditorError("הכתובת אינה תקינה.")
                previous["address"] = entry["address"].strip()
            if "tags" in entry:
                if not isinstance(entry["tags"], dict):
                    raise EditorError("התגיות אינן תקינות.")
                for category, values in entry["tags"].items():
                    if category not in config["categories"] and category not in previous.get("tags", {}):
                        raise EditorError("קטגוריית התגית אינה קיימת.")
                    previous.setdefault("tags", {})[category] = tag_values(values)
        # Prepare before committing any metadata; failed crops or formats leave files intact.
        photos, annotations = prepare.prepare(config, annotations, save=False)
        if revision() != current["revision"]:
            raise EditorError("הקבצים השתנו בזמן השמירה. הטיוטה נשמרה; יש לטעון מחדש.", 409)
        originals = {name: (ROOT / name).read_bytes() for name in METADATA}
        backup = ROOT / ".editor/backups" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8])
        for name, data in originals.items():
            destination = backup / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        try:
            for name, data in zip(METADATA, (config, annotations, photos)):
                prepare.write_json(ROOT / name, data)
        except Exception:
            for name, data in originals.items():
                temporary = (ROOT / name).with_suffix(".restore")
                temporary.write_bytes(data)
                os.replace(temporary, ROOT / name)
            raise
        return editor_state()


def geocode(address):
    global last_geocode
    address = text(address, 300)
    endpoint = prepare.read_json(ROOT / "config.json", {}).get("geocoding", {}).get("endpoint", "https://nominatim.openstreetmap.org/search")
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost")):
        raise EditorError("כתובת שירות החיפוש אינה תקינה.")
    cache_key = hashlib.sha256((endpoint + "|he|" + " ".join(address.casefold().split())).encode()).hexdigest()
    cache_path = ROOT / ".editor/geocode-cache.json"
    with geocode_lock:
        cache = prepare.read_json(cache_path, {})
        if cache_key in cache:
            return {"results": cache[cache_key], "cached": True}
        delay = 1.1 - (time.monotonic() - last_geocode)
        if delay > 0:
            time.sleep(delay)
        query = urlencode({"q": address, "format": "jsonv2", "addressdetails": 1, "limit": 5, "accept-language": "he"})
        request_url = endpoint + ("&" if "?" in endpoint else "?") + query
        headers = {"User-Agent": "SignsPhotographyArchive/1.0 (local photography archive editor)", "Accept": "application/json"}
        last_geocode = time.monotonic()
        try:
            try:
                with urlopen(Request(request_url, headers=headers), timeout=20) as response:
                    data = json.load(response)
            except URLError as error:
                if isinstance(error, HTTPError):
                    error.close()
                    raise
                # System curl handles TLS on macOS installations with older Python SSL.
                last_geocode = time.monotonic()
                time.sleep(1.1)
                last_geocode = time.monotonic()
                data = json.loads(subprocess.check_output([
                    "curl", "--fail", "--silent", "--show-error", "--max-time", "20",
                    "--user-agent", headers["User-Agent"], request_url
                ], stderr=subprocess.DEVNULL))
            if not isinstance(data, list):
                raise ValueError("Invalid geocoding response")
            results = []
            for item in data[:5]:
                point = [float(item["lat"]), float(item["lon"])]
                if prepare.valid_coordinates(point):
                    results.append({"label": str(item.get("display_name", address)), "coordinates": point})
        except (URLError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            raise EditorError("חיפוש הכתובת נכשל. יש לבדוק את החיבור או לבחור מיקום ידנית במפה.", 502) from exc
        cache[cache_key] = results
        prepare.write_json(cache_path, cache)
        return {"results": results, "cached": False}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        path=urlsplit(self.path).path
        if path.lstrip("/") in METADATA or path=="/" or path.endswith((".html", ".js", ".css")):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def trusted_request(self):
        port = self.server.server_port
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        if host not in allowed or (origin is not None and origin != "http://" + host):
            raise EditorError("הגישה מותרת רק מתוך העורך המקומי.", 403)
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise EditorError("הגישה מותרת רק מתוך העורך המקומי.", 403)

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def api(self, write=False):
        try:
            self.trusted_request()
            path = urlsplit(self.path)
            if write:
                if path.path != "/api/editor":
                    raise EditorError("לא נמצא.", 404)
                if self.headers.get_content_type() != "application/json":
                    raise EditorError("נדרש JSON.", 415)
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 2_000_000:
                    raise EditorError("גודל הבקשה אינו תקין.", 413)
                payload = json.loads(self.rfile.read(length))
                self.send_json(save_editor(payload))
            elif path.path == "/api/editor":
                self.send_json(editor_state())
            elif path.path == "/api/geocode":
                address = parse_qs(path.query).get("address", [""])[0]
                self.send_json(geocode(address))
            else:
                raise EditorError("לא נמצא.", 404)
        except EditorError as exc:
            self.send_json({"error": str(exc)}, exc.status)
        except (ValueError, TypeError) as exc:
            self.send_json({"error": "הנתונים אינם תקינים: " + str(exc)}, 400)
        except Exception:
            self.send_json({"error": "השמירה נכשלה. הקבצים הקודמים נשמרו, והטיוטה נשארה בדפדפן."}, 500)

    def do_GET(self):
        if urlsplit(self.path).path.startswith("/api/"):
            return self.api()
        self.trusted_static()

    def do_HEAD(self):
        self.trusted_static(head=True)

    def trusted_static(self, head=False):
        try:
            self.trusted_request()
        except EditorError as exc:
            return self.send_json({"error": str(exc)}, exc.status)
        parts = Path(unquote(urlsplit(self.path).path)).parts
        candidate = Path(self.translate_path(self.path)).resolve()
        if any(part.startswith(".") for part in parts) or not candidate.is_relative_to(ROOT) or candidate.suffix == ".py":
            return self.send_error(404)
        if candidate.is_dir() and not (candidate / "index.html").exists():
            return self.send_error(404)
        return super().do_HEAD() if head else super().do_GET()

    def do_POST(self):
        self.api(write=True)

    def log_message(self, format, *args):
        # Do not log query strings containing addresses.
        if "/api/geocode" in self.path:
            return
        super().log_message(format, *args)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Archive and local editor: http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
