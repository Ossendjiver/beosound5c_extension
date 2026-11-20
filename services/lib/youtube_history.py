"""Unified YouTube history on BS5c. No Google account or streaming URL storage.

Separate SQLite database preserves existing music-library tables. Requests must
pass a dedicated, >=32 character BS5C_YOUTUBE_HISTORY_KEY. Feature is off
without that key. Video history and resumability are cross-room, not Google sync.
"""
from __future__ import annotations

import hmac
import os
import re
import sqlite3
import time
from pathlib import Path

from aiohttp import web

ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
SOURCE = re.compile(r"^[A-Za-z0-9_-]{1,48}$")
MAX_MS = 24 * 60 * 60 * 1000
MAX_FUTURE = 5 * 60 * 1000
MAX_ITEMS = 20000
FIELDS = {"video_id", "device_id", "title", "channel", "position_ms",
          "duration_ms", "watched_at_ms", "ended"}
REQUIRED = FIELDS - {"title", "channel"}
DB_DEFAULT = "/media/local/cache/youtube_history.sqlite3"


def validate_event(raw, *, now_ms=None):
    if not isinstance(raw, dict) or set(raw) - FIELDS or not REQUIRED.issubset(raw):
        raise ValueError("Unexpected or missing history fields")
    video_id, device_id = raw["video_id"], raw["device_id"]
    if not isinstance(video_id, str) or not ID.fullmatch(video_id):
        raise ValueError("Invalid video_id")
    if not isinstance(device_id, str) or not SOURCE.fullmatch(device_id):
        raise ValueError("Invalid device_id")
    for name, limit in (("title", 300), ("channel", 150)):
        value = raw.get(name, "")
        if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value):
            raise ValueError("Invalid metadata")
    for name in ("position_ms", "duration_ms", "watched_at_ms"):
        if type(raw[name]) is not int or raw[name] < 0:
            raise ValueError("Invalid time")
    if raw["duration_ms"] > MAX_MS or raw["position_ms"] > MAX_MS:
        raise ValueError("Duration out of bounds")
    if type(raw["ended"]) is not bool:
        raise ValueError("Invalid ended flag")
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    if raw["watched_at_ms"] > now_ms + MAX_FUTURE:
        raise ValueError("Timestamp in future")
    return {
        "video_id": video_id, "device_id": device_id,
        "title": raw.get("title", ""), "channel": raw.get("channel", ""),
        "position_ms": min(raw["position_ms"], raw["duration_ms"]) if raw["duration_ms"] else raw["position_ms"],
        "duration_ms": raw["duration_ms"], "watched_at_ms": raw["watched_at_ms"],
        "ended": raw["ended"],
    }


class YouTubeHistory:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS youtube_history (
                video_id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                channel TEXT NOT NULL DEFAULT '',
                position_ms INTEGER NOT NULL,
                duration_ms INTEGER NOT NULL,
                watched_at_ms INTEGER NOT NULL,
                ended INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_youtube_history_watched
                ON youtube_history(watched_at_ms DESC);
        """)

    def upsert(self, event):
        e = validate_event(event)
        with self.db:
            cursor = self.db.execute("""
                INSERT INTO youtube_history
                (video_id,device_id,title,channel,position_ms,duration_ms,watched_at_ms,ended)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(video_id) DO UPDATE SET
                  device_id=excluded.device_id,
                  title=COALESCE(NULLIF(excluded.title,''), youtube_history.title),
                  channel=COALESCE(NULLIF(excluded.channel,''), youtube_history.channel),
                  position_ms=excluded.position_ms,
                  duration_ms=CASE WHEN excluded.duration_ms=0 THEN youtube_history.duration_ms ELSE excluded.duration_ms END,
                  watched_at_ms=excluded.watched_at_ms,
                  ended=excluded.ended
                WHERE excluded.watched_at_ms > youtube_history.watched_at_ms
            """, (e["video_id"], e["device_id"], e["title"], e["channel"],
                  e["position_ms"], e["duration_ms"], e["watched_at_ms"], int(e["ended"])))
            if cursor.rowcount:
                count = self.db.execute("SELECT COUNT(*) FROM youtube_history").fetchone()[0]
                if count > MAX_ITEMS:
                    self.db.execute("""DELETE FROM youtube_history WHERE video_id IN (
                        SELECT video_id FROM youtube_history
                        ORDER BY watched_at_ms ASC, video_id ASC LIMIT ?)""", (count-MAX_ITEMS,))
        return bool(cursor.rowcount)

    def entries(self, limit=100, video_id=None):
        if video_id is not None:
            if not ID.fullmatch(video_id):
                raise ValueError("Invalid video ID")
            rows = self.db.execute("SELECT * FROM youtube_history WHERE video_id=?", (video_id,))
        else:
            if type(limit) is not int or not 1 <= limit <= 500:
                raise ValueError("Invalid limit")
            rows = self.db.execute(
                "SELECT * FROM youtube_history ORDER BY watched_at_ms DESC, video_id LIMIT ?", (limit,))
        return [{**dict(row), "ended": bool(row["ended"])} for row in rows]

    def close(self):
        self.db.close()


def _authorised(request):
    key = os.environ.get("BS5C_YOUTUBE_HISTORY_KEY", "")
    supplied = request.headers.get("X-HomeMedia-YouTube-Key", "")
    return len(key) >= 32 and hmac.compare_digest(supplied, key)


async def handle_history(request):
    if not _authorised(request):
        return web.json_response({"error": "YouTube history unavailable"}, status=403)
    history = request.app["youtube_history"]
    if request.method == "GET":
        try:
            if set(request.query) - {"limit", "video_id"}:
                raise ValueError("Unexpected query field")
            limit = int(request.query.get("limit", "100"))
            items = history.entries(limit=limit, video_id=request.query.get("video_id"))
            return web.json_response({"items": items}, headers={"Cache-Control": "no-store"})
        except (ValueError, TypeError):
            return web.json_response({"error": "Invalid history query"}, status=400)
    try:
        if request.content_length is not None and request.content_length > 4096:
            return web.json_response({"error": "Request too large"}, status=413)
        payload = await request.content.read(4097)
        if len(payload) > 4096 or not request.content.at_eof():
            return web.json_response({"error": "Request too large"}, status=413)
        import json
        raw = json.loads(payload)
        event = validate_event(raw)
        updated = history.upsert(event)
        return web.json_response({"accepted": True, "updated": updated}, headers={"Cache-Control": "no-store"})
    except (ValueError, TypeError, UnicodeDecodeError):
        return web.json_response({"error": "Invalid playback event"}, status=400)


def install(app):
    """Attach opt-in history to the BS5c library service."""
    if len(os.environ.get("BS5C_YOUTUBE_HISTORY_KEY", "")) < 32:
        return False
    history = YouTubeHistory(os.environ.get("BS5C_YOUTUBE_HISTORY_DB", DB_DEFAULT))
    app["youtube_history"] = history
    app.router.add_get("/library/youtube/history", handle_history)
    app.router.add_post("/library/youtube/history", handle_history)
    from . import youtube_collections
    youtube_collections.install(app, history)

    async def cleanup(_app):
        history.close()

    app.on_cleanup.append(cleanup)
    return True
