"""Staged external music listening ledger for HA→BS5c migration.

BS5c persists confirmed OFF-NETWORK listening events without inventing an MA
URI. This module does not write to Music Assistant's playlog and does not
interfere with router/MA queue ownership. All mutations require a configured
shared token; with no token the write API is disabled.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import time

from aiohttp import web

DEFAULT_DB = "/media/local/cache/bs5c_external_listens.sqlite3"
MAX_AGE_SECONDS = 180 * 86400
SOURCE_NAMES = {"AUX", "A.AUX", "CD", "RADIO", "LINE_IN", "LINE-IN"}
EVENT_ID = re.compile(r"^[a-zA-Z0-9_:-]{16,128}$")


def _text(value, length=180):
    return str(value or "").strip()[:length]


def track_key(title, artist):
    folded = (_text(title, 180) + "\0" + _text(artist, 180)).casefold().encode()
    return hashlib.sha256(folded).hexdigest()


def validate_listen(payload, *, now=None):
    """Use strict validation: *only confirmed* listening is admitted."""
    if not isinstance(payload, dict):
        raise ValueError("Expected JSON object")
    now = time.time() if now is None else float(now)
    event_id = _text(payload.get("event_id"), 128)
    if not EVENT_ID.fullmatch(event_id):
        raise ValueError("Invalid event_id")
    if payload.get("external") is not True or payload.get("observations", 0) < 2:
        raise ValueError("At least two observations required")
    if payload.get("confirmation_method") != "time_separated_audio":
        raise ValueError("Unconfirmed audio evidence")
    title, artist = _text(payload.get("title")), _text(payload.get("artist"))
    if not title or not artist:
        raise ValueError("Track title and artist required")
    room = _text(payload.get("room"), 50)
    source = _text(payload.get("source"), 30).upper()
    if not room or source not in SOURCE_NAMES:
        raise ValueError("Eligible room and playback source required")
    first = float(payload.get("first_observed_at") or 0)
    confirmed = float(payload.get("confirmed_at") or 0)
    ended = float(payload.get("ended_at") or 0)
    duration = float(payload.get("listened_seconds") or 0)
    if not (now - MAX_AGE_SECONDS <= first <= confirmed <= ended <= now + 300):
        raise ValueError("Invalid timestamp order or stale event")
    if not (0 <= duration <= ended - first <= 12 * 3600):
        raise ValueError("Invalid listened_seconds")
    providers = payload.get("providers") or []
    if not isinstance(providers, list) or not providers or len(providers) > 4:
        raise ValueError("Recognition provider evidence required")
    providers = [_text(p, 40) for p in providers]
    if not all(re.fullmatch(r"[a-z0-9_-]+", p) for p in providers):
        raise ValueError("Invalid provider names")
    return {"event_id": event_id, "room": room, "source": source,
            "title": title, "artist": artist, "album": _text(payload.get("album")),
            "first_observed_at": first, "confirmed_at": confirmed,
            "ended_at": ended, "listened_seconds": duration,
            "providers": providers, "observations": int(payload["observations"]),
            "track_key": track_key(title, artist)}


class ExternalListenStore:
    def __init__(self, path=DEFAULT_DB):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.executescript("""
            CREATE TABLE IF NOT EXISTS external_listens (
                event_id TEXT PRIMARY KEY,
                room TEXT NOT NULL, source TEXT NOT NULL,
                title TEXT NOT NULL, artist TEXT NOT NULL, album TEXT,
                track_key TEXT NOT NULL,
                first_observed_at REAL NOT NULL,
                confirmed_at REAL NOT NULL, ended_at REAL NOT NULL,
                listened_seconds REAL NOT NULL,
                observations INTEGER NOT NULL, providers TEXT NOT NULL,
                received_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_external_listens_recent
                ON external_listens(ended_at DESC);
            CREATE TABLE IF NOT EXISTS external_favorites (
                track_key TEXT PRIMARY KEY,
                title TEXT NOT NULL, artist TEXT NOT NULL, album TEXT,
                favorited INTEGER NOT NULL, updated_at REAL NOT NULL,
                resolved_uri TEXT
            );
            """)

    def connect(self):
        con = sqlite3.connect(self.path, timeout=5)
        con.row_factory = sqlite3.Row
        return con

    def record(self, payload, *, now=None):
        item = validate_listen(payload, now=now)
        with self.connect() as con:
            cursor = con.execute("""
            INSERT OR IGNORE INTO external_listens(
                event_id,room,source,title,artist,album,track_key,
                first_observed_at,confirmed_at,ended_at,listened_seconds,
                observations,providers,received_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (item["event_id"],item["room"],item["source"],item["title"],
                  item["artist"],item["album"],item["track_key"],
                  item["first_observed_at"],item["confirmed_at"],item["ended_at"],
                  item["listened_seconds"],item["observations"],
                  json.dumps(item["providers"]),time.time()))
        return {"accepted": bool(cursor.rowcount), "duplicate": not bool(cursor.rowcount),
                "event_id": item["event_id"]}

    def recent(self, limit=50):
        with self.connect() as con:
            rows = con.execute("""
                SELECT l.*, f.favorited, f.resolved_uri
                FROM external_listens l
                LEFT JOIN external_favorites f ON f.track_key=l.track_key
                ORDER BY l.ended_at DESC LIMIT ?
            """, (max(1,min(200,int(limit))),)).fetchall()
        return [{**dict(x), "providers":json.loads(x["providers"]),
                 "favorite":bool(x["favorited"])} for x in rows]

    def set_favorite(self, payload):
        title, artist = _text(payload.get("title")), _text(payload.get("artist"))
        if not title or not artist or type(payload.get("favorite")) is not bool:
            raise ValueError("Valid track and boolean favorite required")
        key = track_key(title,artist)
        with self.connect() as con:
            # Do not fabricate a MA URI. A manually confirmed resolution is
            # a separate, explicit operation.
            con.execute("""INSERT INTO external_favorites
                (track_key,title,artist,album,favorited,updated_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(track_key) DO UPDATE SET
                  favorited=excluded.favorited,updated_at=excluded.updated_at
            """,(key,title,artist,_text(payload.get("album")),
                 int(payload["favorite"]),time.time()))
        return {"track_key":key,"favorite":payload["favorite"],"scope":"external"}

    def resolve(self,payload):
        """Explicit mapping only; no fuzzy automatic MA lookup in this step."""
        title, artist = _text(payload.get("title")), _text(payload.get("artist"))
        uri = _text(payload.get("resolved_uri"), 1024)
        if not title or not artist or not uri or "://" not in uri:
            raise ValueError("Explicit Music Assistant URI required")
        key=track_key(title,artist)
        with self.connect() as con:
            con.execute("""INSERT INTO external_favorites
                (track_key,title,artist,album,favorited,updated_at,resolved_uri)
                VALUES(?,?,?,?,0,?,?)
                ON CONFLICT(track_key) DO UPDATE SET resolved_uri=excluded.resolved_uri,
                    updated_at=excluded.updated_at
            """,(key,title,artist,_text(payload.get("album")),time.time(),uri))
        return {"track_key":key,"resolved_uri":uri}


def install(app: web.Application):
    store=ExternalListenStore(os.getenv("BS5C_EXTERNAL_LISTEN_DB", DEFAULT_DB))
    token=os.getenv("BS5C_EXTERNAL_LISTEN_TOKEN", "")

    def require_token(request):
        provided=request.headers.get("Authorization", "")
        if not token or not hmac.compare_digest(provided, "Bearer "+token):
            raise web.HTTPUnauthorized(text="External listening writes disabled or unauthorised")

    async def recent(request):
        require_token(request)
        try:
            limit=int(request.query.get("limit", "50"))
        except ValueError:
            raise web.HTTPBadRequest(text="Invalid limit")
        return web.json_response({"items":store.recent(limit),"host":"bs5c",
                                  "capture_host":"home_assistant"})

    async def ingest(request):
        require_token(request)
        try:
            result=store.record(await request.json())
        except (ValueError,KeyError,TypeError,OverflowError) as exc:
            raise web.HTTPBadRequest(text=str(exc))
        return web.json_response(result)

    async def favorite(request):
        require_token(request)
        try:
            result=store.set_favorite(await request.json())
        except (ValueError,TypeError,KeyError) as exc:
            raise web.HTTPBadRequest(text=str(exc))
        return web.json_response(result)

    async def resolve(request):
        require_token(request)
        try:
            result=store.resolve(await request.json())
        except (ValueError,TypeError,KeyError) as exc:
            raise web.HTTPBadRequest(text=str(exc))
        return web.json_response(result)

    app.router.add_get("/library/external/recent", recent)
    app.router.add_post("/library/external/listens", ingest)
    app.router.add_post("/library/external/favorites", favorite)
    app.router.add_post("/library/external/resolve", resolve)
