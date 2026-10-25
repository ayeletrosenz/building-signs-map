#!/usr/bin/env python3
"""Download a small, reusable OSM street snapshot around the archive."""
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[1]


def prepare():
    photos = json.loads((ROOT / "data/photos.json").read_text())
    points = [photo["coordinates"] for photo in photos if photo.get("coordinates")]
    if not points:
        raise ValueError("Place at least one photograph before downloading streets.")
    south, west = (min(p[i] for p in points) for i in (0, 1))
    north, east = (max(p[i] for p in points) for i in (0, 1))
    # Include the surrounding blocks and enough coverage for wide browser windows.
    config = json.loads((ROOT / "config.json").read_text())
    padding = float(config.get("streetCoveragePadding", 2))
    lat_margin = max(north - south, .008) * padding
    lng_margin = max(east - west, .008) * padding
    bbox = [round(south - lat_margin, 7), round(west - lng_margin, 7),
            round(north + lat_margin, 7), round(east + lng_margin, 7)]
    kinds = "motorway|motorway_link|trunk|trunk_link|primary|primary_link|secondary|secondary_link|tertiary|tertiary_link|residential|unclassified|living_street|pedestrian|service|footway|steps|path"
    query = '[out:json][timeout:45];way["highway"~"^(' + kinds + ')$"]["area"!="yes"](' + ",".join(map(str, bbox)) + ');out geom;'
    request = Request("https://overpass-api.de/api/interpreter?" + urlencode({"data": query}),
                      headers={"User-Agent": "SignsPhotographyArchive/1.0", "Accept": "application/json"})
    try:
        with urlopen(request, timeout=75) as response:
            result = json.load(response)
    except URLError:
        # macOS system Python can have an outdated TLS stack; curl uses system TLS.
        result = json.loads(subprocess.check_output([
            "curl", "-fsSL", "--connect-timeout", "15", "--max-time", "75",
            "--user-agent", "SignsPhotographyArchive/1.0", request.full_url
        ]))
    if result.get("remark"):
        raise ValueError("Overpass did not complete the download: " + result["remark"])
    grouped = {}
    labels = []
    segment_count = 0
    for way in result.get("elements", []):
        points = [[round(p["lon"], 7), round(p["lat"], 7)] for p in way.get("geometry", [])]
        if len(points) < 2:
            continue
        grouped.setdefault(way["tags"]["highway"], []).append(points)
        tags = way["tags"]
        name = tags.get("name:he") or tags.get("name")
        if name and tags["highway"] not in ("service", "path", "steps", "footway"):
            labels.append({"type": "Feature", "id": way["id"],
                           "properties": {"name": name, "highway": tags["highway"]},
                           "geometry": {"type": "LineString", "coordinates": points}})
        segment_count += 1
    if not grouped:
        raise ValueError("No streets returned; existing street data was preserved.")
    # One layer per street class keeps zooming and mobile rendering lightweight.
    features = [{"type": "Feature", "properties": {"highway": kind},
                 "geometry": {"type": "MultiLineString", "coordinates": lines}}
                for kind, lines in grouped.items()]
    # GeoJSON bbox order is longitude, latitude.
    archive = {"type": "FeatureCollection", "bbox": [bbox[1], bbox[0], bbox[3], bbox[2]],
               "attribution": "© OpenStreetMap contributors",
               "license": "https://opendatacommons.org/licenses/odbl/1-0/",
               "source": "https://www.openstreetmap.org/copyright",
               "downloadedAt": datetime.now(timezone.utc).isoformat(),
               "sourceWayCount": segment_count, "features": features}
    output = ROOT / "data/streets.geojson"
    output.write_text(json.dumps(archive, separators=(",", ":")) + "\n")
    label_archive = {k: v for k, v in archive.items() if k != "features"}
    label_archive["features"] = labels
    (ROOT / "data/street-labels.geojson").write_text(json.dumps(label_archive, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Saved {segment_count} street segments in {len(features)} layers to {output.relative_to(ROOT)} ({output.stat().st_size // 1024} KB).")
    print(f"Saved {len(labels)} named street segments to data/street-labels.geojson.")


if __name__ == "__main__":
    prepare()
