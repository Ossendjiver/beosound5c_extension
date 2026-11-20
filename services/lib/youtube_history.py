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
MAX_EVENTS = 50000
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
            CREATE TABLE IF NOT EXISTS youtube_watch_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id TEXT NOT NULL,
                device_id TEXT NOT NULL,
                title TEXT NOT NULL,
                channel TEXT NOT NULL,
                position_ms INTEGER NOT NULL,
                duration_ms INTEGER NOT NULL,
                watched_at_ms INTEGER NOT NULL,
                ended INTEGER NOT NULL,
                UNIQUE(video_id, device_id, watched_at_ms)
            );
            CREATE INDEX IF NOT EXISTS idx_youtube_watch_events_date
                ON youtube_watch_events(watched_at_ms DESC);
            -- Legacy data has one most recent position per video; retain it on upgrade.
            INSERT OR IGNORE INTO youtube_watch_events
                (video_id,device_id,title,channel,position_ms,duration_ms,watched_at_ms,ended)
            SELECT video_id,device_id,title,channel,position_ms,duration_ms,watched_at_ms,ended
                FROM youtube_history;
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
            self._append_event(e)
            if cursor.rowcount:
                count = self.db.execute("SELECT COUNT(*) FROM youtube_history").fetchone()[0]
                if count > MAX_ITEMS:
                    self.db.execute("""DELETE FROM youtube_history WHERE video_id IN (
                        SELECT video_id FROM youtube_history
                        ORDER BY watched_at_ms ASC, video_id ASC LIMIT ?)""", (count-MAX_ITEMS,))
        return bool(cursor.rowcount)

    def _append_event(self, e):
        # Progress reports every 30 seconds belong to one viewing session,
        # not thirty separate "watches". Takeout's original view timestamps
        # always remain separate, even when the same video was replayed.
        if e["device_id"] != "takeout":
            previous = self.db.execute("""
                SELECT id, watched_at_ms, position_ms, ended
                FROM youtube_watch_events WHERE video_id=? AND device_id=?
                ORDER BY watched_at_ms DESC LIMIT 1
            """, (e["video_id"], e["device_id"])).fetchone()
            if (previous and not previous["ended"] and
                    0 <= e["watched_at_ms"] - previous["watched_at_ms"] < 6 * 3600 * 1000 and
                    e["position_ms"] >= previous["position_ms"]):
                self.db.execute("""
                    UPDATE youtube_watch_events SET watched_at_ms=?, title=?, channel=?,
                        position_ms=?, duration_ms=?, ended=? WHERE id=?
                """, (e["watched_at_ms"], e["title"] or "", e["channel"] or "",
                      e["position_ms"], e["duration_ms"], int(e["ended"]), previous["id"]))
                return
        self.db.execute("""
            INSERT OR IGNORE INTO youtube_watch_events
                (video_id,device_id,title,channel,position_ms,duration_ms,watched_at_ms,ended)
            VALUES (?,?,?,?,?,?,?,?)
        """, (e["video_id"], e["device_id"], e["title"], e["channel"],
              e["position_ms"], e["duration_ms"], e["watched_at_ms"], int(e["ended"])))
        count = self.db.execute("SELECT COUNT(*) FROM youtube_watch_events").fetchone()[0]
        if count > MAX_EVENTS:
            self.db.execute("""DELETE FROM youtube_watch_events WHERE id IN (
                SELECT id FROM youtube_watch_events
                ORDER BY watched_at_ms ASC, id ASC LIMIT ?)""", (count - MAX_EVENTS,))

    def import_events(self, events):
        """All-or-nothing backfill; never overwrite a newer live resume position."""
        validated = [validate_event(event) for event in events]
        with self.db:
            for event in validated:
                self._append_event(event)
                self.db.execute("""
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
                """, (event["video_id"], event["device_id"], event["title"], event["channel"],
                      event["position_ms"], event["duration_ms"], event["watched_at_ms"], int(event["ended"])))
        return len(validated)

    def entries(self, limit=100, video_id=None, events=False):
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("Invalid limit")
        if video_id is not None and (not isinstance(video_id, str) or not ID.fullmatch(video_id)):
            raise ValueError("Invalid video ID")
        table = "youtube_watch_events" if events else "youtube_history"
        if video_id is not None:
            query = (f"SELECT * FROM {table} WHERE video_id=? ORDER BY watched_at_ms DESC LIMIT ?"
                     if events else f"SELECT * FROM {table} WHERE video_id=?")
            args = (video_id, limit) if events else (video_id,)
        else:
            query = f"SELECT * FROM {table} ORDER BY watched_at_ms DESC, video_id LIMIT ?"
            args = (limit,)
        rows = self.db.execute(query, args)
        return [{k: v for k, v in {**dict(row), "ended": bool(row["ended"])}.items() if k != "id"}
                for row in rows]

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
            if set(request.query) - {"limit", "video_id", "events"}:
                raise ValueError("Unexpected query field")
            limit = int(request.query.get("limit", "100"))
            events = request.query.get("events", "0")
            if events not in ("0", "1"):
                raise ValueError("Invalid events mode")
            items = history.entries(limit=limit, video_id=request.query.get("video_id"),
                                    events=events == "1")
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


async def handle_history_import(request):
    if not _authorised(request):
        return web.json_response({"error": "YouTube history unavailable"}, status=403)
    try:
        if request.content_length is not None and request.content_length > 65000:
            return web.json_response({"error": "Request too large"}, status=413)
        payload = await request.content.read(65001)
        if len(payload) > 65000 or not request.content.at_eof():
            return web.json_response({"error": "Request too large"}, status=413)
        import json
        data = json.loads(payload)
        if not isinstance(data, dict) or set(data) != {"events"}:
            raise ValueError("Invalid import payload")
        events = data["events"]
        if not isinstance(events, list) or not 1 <= len(events) <= 100:
            raise ValueError("Expected 1–100 events")
        count = request.app["youtube_history"].import_events(events)
        return web.json_response({"accepted": count}, headers={"Cache-Control": "no-store"})
    except (ValueError, TypeError, UnicodeDecodeError, KeyError):
        return web.json_response({"error": "Invalid history import"}, status=400)


def install(app):
    """Attach opt-in history to the BS5c library service."""
    if len(os.environ.get("BS5C_YOUTUBE_HISTORY_KEY", "")) < 32:
        return False
    history = YouTubeHistory(os.environ.get("BS5C_YOUTUBE_HISTORY_DB", DB_DEFAULT))
    app["youtube_history"] = history
    app.router.add_get("/library/youtube/history", handle_history)
    app.router.add_post("/library/youtube/history", handle_history)
    app.router.add_post("/library/youtube/history/import", handle_history_import)
    from . import youtube_collections
    youtube_collections.install(app, history)
    # Optional independent Home Assistant watcher; uses a real YouTube-on-TV
    # playback entity, never SamsungTV Smart's unverified launch acknowledgement.
    from . import youtube_samsung
    youtube_samsung.install(app, history)

    async def cleanup(_app):
        history.close()

    app.on_cleanup.append(cleanup)
    return True
