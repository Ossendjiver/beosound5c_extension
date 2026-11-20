"""BS5c-owned SmartTube collections with versioned per-entry updates.

All writers (Kodi and Android) use compare-and-swap. Tombstones preserve
deletions across temporarily disconnected clients. The server does not touch
Kodi network caches or stream manifests.
"""
from __future__ import annotations
import json
import re
import sqlite3
import time

from aiohttp import web
from .youtube_history import _authorised

COLLECTIONS = {"follows": 200, "watch_later": 500, "playlists": 200, "home_playlists": 200}
IDENT = re.compile(r"^[A-Za-z0-9_.:@/-]{1,128}$")
DEVICE = re.compile(r"^[A-Za-z0-9_-]{1,48}$")


def validate_change(value, collection):
    if collection not in COLLECTIONS or not isinstance(value, dict):
        raise ValueError("Invalid collection")
    if set(value) != {"id", "device_id", "base_version", "deleted", "item"}:
        raise ValueError("Invalid collection change")
    item_id, device = value["id"], value["device_id"]
    if not isinstance(item_id, str) or not IDENT.fullmatch(item_id):
        raise ValueError("Invalid item ID")
    if not isinstance(device, str) or not DEVICE.fullmatch(device):
        raise ValueError("Invalid device ID")
    if type(value["base_version"]) is not int or value["base_version"] < 0:
        raise ValueError("Invalid base version")
    if type(value["deleted"]) is not bool:
        raise ValueError("Invalid deletion flag")
    item = value["item"]
    if not isinstance(item, dict) or len(json.dumps(item, ensure_ascii=False)) > 60000:
        raise ValueError("Invalid item data")
    # Permit SmartTube metadata without accepting arbitrary nested objects or URLs.
    if any(not isinstance(k, str) or len(k) > 64 or
           not isinstance(v, (str, int, bool, float, list)) for k, v in item.items()):
        raise ValueError("Invalid item shape")
    if collection in ("follows", "watch_later"):
        if item and item.get("id") != item_id:
            raise ValueError("ID mismatch")
    if collection in ("playlists", "home_playlists"):
        videos = item.get("videos", [])
        if not isinstance(videos, list) or len(videos) > 500 or any(
                not isinstance(v, dict) or
                not isinstance(v.get("videoId", v.get("id")), str) or
                not re.fullmatch(r"[A-Za-z0-9_-]{11}", v.get("videoId", v.get("id"))) for v in videos):
            raise ValueError("Invalid playlist videos")
    return value


class Collections:
    def __init__(self, connection):
        self.db = connection
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS youtube_collections (
                collection TEXT NOT NULL,
                item_id TEXT NOT NULL,
                item_json TEXT NOT NULL,
                device_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                deleted INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                PRIMARY KEY(collection, item_id)
            );
        """)

    def snapshot(self, collection):
        if collection not in COLLECTIONS:
            raise ValueError("Unsupported collection")
        rows = self.db.execute(
            "SELECT * FROM youtube_collections WHERE collection=? ORDER BY item_id",
            (collection,))
        return [{"id": r["item_id"], "item": json.loads(r["item_json"]),
                 "device_id": r["device_id"], "version": r["version"],
                 "deleted": bool(r["deleted"]), "updated_at_ms": r["updated_at_ms"]}
                for r in rows]

    def change(self, collection, value):
        e = validate_change(value, collection)
        with self.db:
            previous = self.db.execute(
                "SELECT version FROM youtube_collections WHERE collection=? AND item_id=?",
                (collection, e["id"])).fetchone()
            current = previous["version"] if previous else 0
            if e["base_version"] != current:
                return {"accepted": False, "conflict": True, "version": current}
            if (not previous or self.db.execute("SELECT deleted FROM youtube_collections WHERE collection=? AND item_id=?", (collection, e["id"])).fetchone()["deleted"]) and not e["deleted"]:
                count = self.db.execute(
                    "SELECT COUNT(*) FROM youtube_collections WHERE collection=? AND deleted=0",
                    (collection,)).fetchone()[0]
                if count >= COLLECTIONS[collection]:
                    raise ValueError("Collection capacity exceeded")
            now = int(time.time()*1000)
            self.db.execute("""
                INSERT INTO youtube_collections
                    (collection,item_id,item_json,device_id,version,deleted,updated_at_ms)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(collection,item_id) DO UPDATE SET
                    item_json=excluded.item_json, device_id=excluded.device_id,
                    version=excluded.version, deleted=excluded.deleted,
                    updated_at_ms=excluded.updated_at_ms
            """, (collection, e["id"], json.dumps(e["item"], ensure_ascii=False),
                  e["device_id"], current + 1, int(e["deleted"]), now))
        return {"accepted": True, "conflict": False, "version": current + 1}


async def handle_collections(request):
    if not _authorised(request):
        return web.json_response({"error": "YouTube library unavailable"}, status=403)
    name = request.match_info["collection"]
    if name not in COLLECTIONS:
        return web.json_response({"error": "Unsupported collection"}, status=404)
    store = request.app["youtube_collections"]
    if request.method == "GET":
        if request.query:
            return web.json_response({"error": "Unexpected query"}, status=400)
        return web.json_response({"items": store.snapshot(name)}, headers={"Cache-Control": "no-store"})
    try:
        if request.content_length is not None and request.content_length > 65536:
            return web.json_response({"error": "Too large"}, status=413)
        raw = await request.content.read(65537)
        if len(raw) > 65536 or not request.content.at_eof():
            return web.json_response({"error": "Too large"}, status=413)
        update = json.loads(raw)
        result = store.change(name, update)
        return web.json_response(result, status=409 if result["conflict"] else 200,
                                 headers={"Cache-Control": "no-store"})
    except (ValueError, TypeError, UnicodeDecodeError):
        return web.json_response({"error": "Invalid collection update"}, status=400)


def install(app, history):
    app["youtube_collections"] = Collections(history.db)
    app.router.add_get("/library/youtube/collections/{collection}", handle_collections)
    app.router.add_post("/library/youtube/collections/{collection}", handle_collections)
