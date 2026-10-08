#!/usr/bin/env python3
"""ASCII-art world map of BS5c beacon locations, rendered as Braille in the terminal.

Downloads Natural Earth coastlines (cached under ~/.cache/beosound5c/), queries
the beacon D1 DB for the latest beacon per device_id, looks up lat/lon via
ip-api.com (cached), auto-zooms to the bounding box of active beacons, and
renders a Braille map with colored markers and a legend.

Usage:  ./tools/beacon-map.py            # one-shot render
        ./tools/beacon-map.py --loop 30  # re-render every 30s (basic "blink")
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = Path.home() / ".cache" / "beosound5c"
COASTLINE_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
    "master/geojson/ne_50m_coastline.geojson"
)
COASTLINE_PATH = CACHE_DIR / "ne_50m_coastline.geojson"
IP_CACHE_PATH = CACHE_DIR / "ip_geo_cache.json"

# Authoritative device_id → name mapping (see memory/home-beacons.md).
HOME_DEVICES = {
    "2069f145-2200-4066-89e5-8283b8b6e45a": "Kitchen",
    "faedd8ec-7c4a-4875-87b5-1cefe3769161": "Office",
    "8e671b06-063c-4fd2-a16f-e9105475c60f": "Church",
}

BRAILLE_BASE = 0x2800
BRAILLE_BITS = {
    (0, 0): 0x01, (0, 1): 0x02, (0, 2): 0x04, (0, 3): 0x40,
    (1, 0): 0x08, (1, 1): 0x10, (1, 2): 0x20, (1, 3): 0x80,
}

ANSI = {"reset": "\x1b[0m", "dim": "\x1b[2m", "bold": "\x1b[1m",
        "green": "\x1b[92m", "yellow": "\x1b[93m", "cyan": "\x1b[96m",
        "blue": "\x1b[34m"}


def ensure_coastline() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if COASTLINE_PATH.exists():
        return
    print(f"Downloading coastline data → {COASTLINE_PATH} …", file=sys.stderr)
    urllib.request.urlretrieve(COASTLINE_URL, COASTLINE_PATH)


def load_ip_cache() -> dict:
    if IP_CACHE_PATH.exists():
        return json.loads(IP_CACHE_PATH.read_text())
    return {}


def save_ip_cache(cache: dict) -> None:
    IP_CACHE_PATH.write_text(json.dumps(cache, indent=2))


def geolocate(ip: str, cache: dict) -> dict | None:
    if ip in cache:
        return cache[ip]
    url = f"http://ip-api.com/json/{ip}?fields=status,lat,lon,city,regionName,country"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.load(resp)
    except Exception as e:
        print(f"geo lookup failed for {ip}: {e}", file=sys.stderr)
        return None
    if data.get("status") != "success":
        return None
    cache[ip] = data
    return data


def query_beacons() -> list[dict]:
    cmd = [
        "npx", "wrangler", "d1", "execute", "beosound5c-beacons", "--remote",
        "--command",
        "SELECT b1.*, (SELECT COUNT(*) FROM beacons b3 WHERE b3.device_id = b1.device_id) "
        "AS beacon_count FROM beacons b1 WHERE received_at = "
        "(SELECT MAX(received_at) FROM beacons b2 WHERE b2.device_id = b1.device_id) "
        "ORDER BY received_at DESC LIMIT 50;",
        "--json",
    ]
    res = subprocess.run(
        cmd, cwd=REPO_ROOT / "workers" / "beacon-worker",
        text=True, capture_output=True)
    if res.returncode != 0:
        raise RuntimeError(
            f"wrangler exited {res.returncode}\nstderr:\n{res.stderr}")
    out = res.stdout
    # wrangler mixes chatter and JSON; extract the JSON array.
    start = out.index("[")
    end = out.rindex("]")
    data = json.loads(out[start:end + 1])
    return data[0]["results"]


def enrich(rows: list[dict]) -> list[dict]:
    cache = load_ip_cache()
    beacons = []
    for r in rows:
        geo = geolocate(r["public_ip"], cache)
        if not geo:
            continue
        beacons.append({
            "device_id": r["device_id"],
            "name": HOME_DEVICES.get(r["device_id"], "unknown"),
            "version": r["version"],
            "lat": geo["lat"],
            "lon": geo["lon"],
            "city": geo.get("city", ""),
            "region": geo.get("regionName", ""),
            "country": geo.get("country", ""),
            "count": r["beacon_count"],
            "public_ip": r["public_ip"],
            "received_at": r["received_at"],
        })
    save_ip_cache(cache)
    return beacons


def compute_bbox(beacons: list[dict], pad_frac: float = 0.30) -> tuple[float, float, float, float]:
    lats = [b["lat"] for b in beacons]
    lons = [b["lon"] for b in beacons]
    lat_min, lat_max = min(lats), max(lats)
    lon_min, lon_max = min(lons), max(lons)
    lat_pad = max((lat_max - lat_min) * pad_frac, 0.5)
    lon_pad = max((lon_max - lon_min) * pad_frac, 0.5)
    return lat_min - lat_pad, lat_max + lat_pad, lon_min - lon_pad, lon_max + lon_pad


def make_projector(bbox, W: int, H: int):
    """Equirectangular with cos(lat) compensation, aspect-preserving letterbox."""
    lat_min, lat_max, lon_min, lon_max = bbox
    lat_c = (lat_min + lat_max) / 2
    cos_lat = math.cos(math.radians(lat_c))
    dx_km = (lon_max - lon_min) * cos_lat * 111.0
    dy_km = (lat_max - lat_min) * 111.0
    scale = min(W / dx_km, H / dy_km)
    px_w = dx_km * scale
    px_h = dy_km * scale
    off_x = (W - px_w) / 2
    off_y = (H - px_h) / 2

    def project(lat: float, lon: float) -> tuple[float, float]:
        x = off_x + (lon - lon_min) * cos_lat * 111.0 * scale
        y = off_y + (lat_max - lat) * 111.0 * scale
        return x, y
    return project


def draw_line(buf, x0, y0, x1, y1, W, H) -> None:
    x0, y0 = int(round(x0)), int(round(y0))
    x1, y1 = int(round(x1)), int(round(y1))
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        if 0 <= x0 < W and 0 <= y0 < H:
            buf[y0][x0] = True
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def draw_coastlines(buf, W, H, project, bbox) -> None:
    lat_min, lat_max, lon_min, lon_max = bbox
    # Coarse cull box with a 1° margin.
    with open(COASTLINE_PATH) as f:
        coast = json.load(f)
    for feat in coast["features"]:
        geom = feat["geometry"]
        if geom["type"] == "LineString":
            lines = [geom["coordinates"]]
        elif geom["type"] == "MultiLineString":
            lines = geom["coordinates"]
        else:
            continue
        for line in lines:
            prev = None
            for lon, lat in line:
                if not (lon_min - 2 <= lon <= lon_max + 2
                        and lat_min - 2 <= lat <= lat_max + 2):
                    prev = None
                    continue
                x, y = project(lat, lon)
                if prev is not None:
                    draw_line(buf, prev[0], prev[1], x, y, W, H)
                prev = (x, y)


def render(buf, W, H, overlays) -> str:
    cols = W // 2
    rows = H // 4
    ov = {}
    for col, row, ch, color in overlays:
        cc, rr = int(col // 2), int(row // 4)
        ov.setdefault((cc, rr), (ch, color))
    lines = []
    for r in range(rows):
        parts = []
        for c in range(cols):
            if (c, r) in ov:
                ch, color = ov[(c, r)]
                parts.append(f"{color}{ANSI['bold']}{ch}{ANSI['reset']}")
                continue
            bits = 0
            for dx in range(2):
                for dy in range(4):
                    if buf[r * 4 + dy][c * 2 + dx]:
                        bits |= BRAILLE_BITS[(dx, dy)]
            parts.append(chr(BRAILLE_BASE + bits) if bits else " ")
        lines.append("".join(parts))
    return "\n".join(lines)


def render_map(beacons: list[dict]) -> str:
    try:
        tw = os.get_terminal_size().columns
        th = os.get_terminal_size().lines
    except OSError:
        tw, th = 100, 30
    map_rows = max(th - 12, 14)
    W, H = tw * 2, map_rows * 4
    bbox = compute_bbox(beacons)
    project = make_projector(bbox, W, H)
    buf = [[False] * W for _ in range(H)]
    draw_coastlines(buf, W, H, project, bbox)
    overlays = []
    for b in beacons:
        x, y = project(b["lat"], b["lon"])
        color = ANSI["green"] if b["name"] != "unknown" else ANSI["yellow"]
        overlays.append((x, y, "\u25cf", color))  # ●
    map_str = render(buf, W, H, overlays)
    # Legend.
    legend = [f"\n{ANSI['bold']}Beacons ({len(beacons)}) — bbox "
              f"lat {bbox[0]:.2f}..{bbox[1]:.2f}  lon {bbox[2]:.2f}..{bbox[3]:.2f}"
              f"{ANSI['reset']}"]
    for b in sorted(beacons, key=lambda x: (x["name"] == "unknown", x["name"],
                                            x["city"])):
        dot_color = ANSI["green"] if b["name"] != "unknown" else ANSI["yellow"]
        dot = f"{dot_color}{ANSI['bold']}\u25cf{ANSI['reset']}"
        name = b["name"] if b["name"] != "unknown" \
            else f"unknown/{b['device_id'][:8]}"
        where = f"{b['city']}, {b['region']}"
        legend.append(f"  {dot} {name:22s} {where:38s} "
                      f"v={b['version']:15s} n={b['count']}")
    return map_str + "\n" + "\n".join(legend)


def main():
    ensure_coastline()
    loop = None
    if "--loop" in sys.argv:
        loop = int(sys.argv[sys.argv.index("--loop") + 1])
    while True:
        rows = query_beacons()
        beacons = enrich(rows)
        if not beacons:
            print("No beacons to show.", file=sys.stderr)
            return
        if loop:
            # clear screen + home cursor.
            sys.stdout.write("\x1b[2J\x1b[H")
        print(render_map(beacons))
        if not loop:
            return
        time.sleep(loop)


if __name__ == "__main__":
    main()
