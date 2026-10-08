#!/usr/bin/env python3
"""
Context-aware local library/recommendation service for BeoSound 5c.

The service deliberately keeps all preference learning on the local BS5c:
- observes the router's now-playing state;
- stores listening outcomes in SQLite;
- samples Home Assistant weather and recent Garmin running state;
- ranks Music Assistant library tracks with a lightweight contextual model;
- exposes recommendations to Home Media and the BS5c UI;
- supplies non-waking UI prompts for news/music/post-run yoga.

HTTP API (default port 8788):
  GET  /library/context
  GET  /library/recommend/music?room=lounge&limit=20
  GET  /library/suggestions?room=lounge
  POST /library/event
  POST /library/action
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import math
import os
import random
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web

# Allow running both from repo and installed ~/beosound5c/services.
HERE = Path(__file__).resolve().parent

from lib.config import cfg
from lib.background_tasks import BackgroundTaskSet
from lib import music_mood

log = logging.getLogger("beo-library")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

PORT = int(os.getenv("BS5C_LIBRARY_PORT", "8788"))
ROUTER_MEDIA = "http://127.0.0.1:8770/router/media"
ROUTER_EVENT = "http://127.0.0.1:8770/router/event"
ROUTER_BROADCAST = "http://127.0.0.1:8770/router/broadcast"
MASS_COMMAND = "http://127.0.0.1:8783/command"
MASS_LIBRARY = "http://127.0.0.1:8783/playlists"
CACHE_LIBRARY = Path("/media/local/cache/mass_playlists.json")
DB_PATH = Path(os.getenv("BS5C_LIBRARY_DB", "/media/local/cache/library_recommender.sqlite3"))

ACTIVE_STATES = {"playing", "paused", "buffering"}
RUN_WORDS = ("run", "running", "trail run", "treadmill")
IGNORE_MEDIA_STATES = {"idle", "off", "unavailable", "unknown", ""}


def _library_cfg() -> dict[str, Any]:
    raw = cfg("library", default={}) or {}
    return raw if isinstance(raw, dict) else {}


def _ha_base() -> str:
    raw = (os.getenv("HA_URL") or cfg("home_assistant", "url", default="http://homeassistant.local:8123")).strip().rstrip("/")
    return raw if raw.endswith("/api") else raw + "/api"


def _headers() -> dict[str, str]:
    token = os.getenv("HA_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"} if token else {}


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _parse_iso(value: str) -> float | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.timestamp()
    except ValueError:
        return None


def _weather_bucket(condition: str) -> str:
    value = (condition or "").lower()
    if any(x in value for x in ("rain", "shower", "storm", "drizzle")):
        return "rain"
    if any(x in value for x in ("sun", "clear")):
        return "clear"
    if any(x in value for x in ("cloud", "overcast", "fog", "mist")):
        return "cloud"
    return value or "unknown"


def _time_bucket(hour: int) -> str:
    if hour < 6:
        return "late"
    if hour < 10:
        return "morning"
    if hour < 17:
        return "day"
    if hour < 21:
        return "evening"
    return "night"


def _weekpart(now: dt.datetime) -> str:
    return "weekend" if now.weekday() >= 5 else "weekday"


def _music_item(item: dict[str, Any]) -> bool:
    media_type = str(item.get("media_type") or item.get("type") or "track").casefold()
    if media_type not in {"track", "song", "music", ""}:
        return False
    words = " ".join(str(item.get(k) or "") for k in ("name", "title", "artist", "genre", "channel"))
    return not re.search(r"\b(podcast|audiobook|hypnosis|meditation|sleep cove|morning news|spoken word)\b", words, re.I)


class LocalModel:
    """Trusted seed baseline with conservative learning and capped artist discovery.

    Explicit choices can expand the familiar pool. Passive observations cannot
    promote random automatic selections into seeds. Context is used only once
    enough deliberate listening evidence exists.
    """

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS listens (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts REAL NOT NULL,
              title TEXT NOT NULL,
              artist TEXT NOT NULL,
              album TEXT NOT NULL,
              uri TEXT NOT NULL,
              seconds REAL NOT NULL,
              reward REAL NOT NULL,
              hour INTEGER NOT NULL,
              time_bucket TEXT NOT NULL,
              weekpart TEXT NOT NULL,
              weather TEXT NOT NULL,
              temperature REAL,
              room TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_listens_ts ON listens(ts);
            CREATE INDEX IF NOT EXISTS idx_listens_artist ON listens(artist);
            CREATE TABLE IF NOT EXISTS kv (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS prompt_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts REAL NOT NULL,
              prompt_id TEXT NOT NULL,
              kind TEXT NOT NULL,
              action TEXT NOT NULL,
              room TEXT NOT NULL
            );
            """
        )
        if "origin" not in {row[1] for row in self.db.execute("PRAGMA table_info(listens)")}:
            self.db.execute("ALTER TABLE listens ADD COLUMN origin TEXT NOT NULL DEFAULT 'legacy'")
        if "weekday" not in {row[1] for row in self.db.execute("PRAGMA table_info(listens)")}:
            self.db.execute("ALTER TABLE listens ADD COLUMN weekday INTEGER NOT NULL DEFAULT -1")
        for column in ("mood_energy", "mood_valence"):
            if column not in {row[1] for row in self.db.execute("PRAGMA table_info(listens)")}:
                self.db.execute(f"ALTER TABLE listens ADD COLUMN {column} REAL")
        self.db.commit()

    def put_kv(self, key: str, value: Any) -> None:
        self.db.execute(
            "INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )
        self.db.commit()

    def get_kv(self, key: str, default: Any = None) -> Any:
        row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except Exception:
            return default

    def record_listen(self, item: dict[str, Any], seconds: float, context: dict[str, Any]) -> None:
        if not _music_item(item):
            return
        title = str(item.get("title") or item.get("name") or "").strip()
        artist = str(item.get("artist") or "").strip()
        if not title and not artist:
            return
        duration = _safe_float(item.get("duration")) or 0.0
        origin = str(item.get("selection_origin") or "unknown")
        reason = str(item.get("end_reason") or "unknown")
        meaningful = seconds >= 90 or (duration > 0 and seconds >= duration * 0.45)
        if reason == "dislike":
            reward = -4.0
        elif reason == "skip" and seconds < 25:
            reward = -2.0
        elif meaningful:
            reward = 1.0 if origin == "manual" else 0.05 if origin == "automatic" else 0.15
        else:
            reward = 0.0  # Pauses, transfers, stops and buffering are not dislikes.
        now = dt.datetime.now()
        self.db.execute(
            """INSERT INTO listens(ts,title,artist,album,uri,seconds,reward,hour,time_bucket,
               weekpart,weather,temperature,room,origin,weekday,mood_energy,mood_valence) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                time.time(), title, artist, str(item.get("album") or ""),
                str(item.get("uri") or ""), float(seconds), reward, now.hour,
                context.get("time_bucket", _time_bucket(now.hour)),
                context.get("weekpart", _weekpart(now)),
                context.get("weather", "unknown"), context.get("temperature"),
                context.get("room", ""), origin, context.get("weekday", now.weekday()),
                (item.get("listening_mood") or {}).get("energy"), (item.get("listening_mood") or {}).get("valence"),
            ),
        )
        cutoff = time.time() - 180 * 86400
        self.db.execute("DELETE FROM listens WHERE ts < ?", (cutoff,))
        self.db.commit()

    def record_prompt(self, prompt_id: str, kind: str, action: str, room: str) -> None:
        self.db.execute(
            "INSERT INTO prompt_events(ts,prompt_id,kind,action,room) VALUES(?,?,?,?,?)",
            (time.time(), prompt_id, kind, action, room),
        )
        self.db.commit()

    def _history(self) -> list[sqlite3.Row]:
        return list(self.db.execute(
            "SELECT * FROM listens WHERE ts >= ? ORDER BY ts DESC LIMIT 2500",
            (time.time() - 180 * 86400,),
        ))

    @staticmethod
    def _candidate_key(item: dict[str, Any]) -> tuple[str, str]:
        return (
            str(item.get("artist") or "").strip().casefold(),
            str(item.get("name") or item.get("title") or "").strip().casefold(),
        )

    def rank(self, candidates: list[dict[str, Any]], context: dict[str, Any], limit: int) -> list[dict[str, Any]]:
        feedback = self.get_kv("music_feedback", {})
        rejected = {self._candidate_key(i) for i in candidates if feedback.get(i.get("uri")) == "dislike"}
        candidates = [i for i in candidates if self._candidate_key(i) not in rejected]
        deduplicated = {}
        for item in candidates:
            key = self._candidate_key(item)
            existing = deduplicated.get(key)
            if existing is None or item.get("favorite") and not existing.get("favorite"):
                deduplicated[key] = item
            elif item.get("trusted"):
                existing["trusted"] = True
        candidates = list(deduplicated.values())
        history = self._history()
        feedback = self.get_kv("music_feedback", {})
        meaningful = [r for r in history if r["origin"] == "manual" and r["reward"] >= 1]
        contextual = len(meaningful) >= 20 and len({r["uri"] or r["title"] for r in meaningful}) >= 5
        known = {(r["artist"].casefold(), r["title"].casefold()) for r in meaningful}
        seeds = [i for i in candidates if _music_item(i) and (i.get("trusted") or i.get("favorite") or self._candidate_key(i) in known or feedback.get(i.get("uri")) == "like")]
        seed_artists = {self._candidate_key(i)[0] for i in seeds if self._candidate_key(i)[0]}
        if not seeds:
            return []  # Never substitute random catalogue entries for an empty baseline.
        mood = context.get("mood")
        mood_profiles = self.get_kv("mood_profiles", {})
        now_ts = time.time()
        familiar, discovery = [], []
        for item in candidates:
            if not _music_item(item) or not item.get("uri"):
                continue
            key = self._candidate_key(item)
            identity = item["uri"]
            if feedback.get(identity) == "dislike":
                continue
            trusted = item.get("trusted") or item.get("favorite") or key in known or feedback.get(identity) == "like"
            if not trusted and key[0] not in seed_artists and not (mood and mood["ring"] == "discover" and music_mood.profile(item, mood_profiles) and music_mood.adjustment(item, mood, mood_profiles) >= -2):
                continue  # Discovery is constrained to artists already represented by seeds.
            score = 12.0 if item.get("favorite") else 10.0 if trusted else 0.0
            if mood:
                score += music_mood.adjustment(item, mood, mood_profiles)
            for row in history:
                if row["artist"].casefold() != key[0] or not key[0]:
                    continue
                same_track = row["title"].casefold() == key[1]
                reward = float(row["reward"])
                if row["origin"] == "legacy":
                    reward = max(0.0, reward) * 0.05  # Old observations had no reliable skip/origin signal.
                if reward < 0 and not same_track:
                    reward *= 0.15
                similarity = 1.0
                if mood and row["mood_energy"] is not None and row["mood_valence"] is not None:
                    distance = math.hypot(mood["energy"]-row["mood_energy"], mood["valence"]-row["mood_valence"])
                    similarity *= 1 + max(0, 1-distance)*2
                if contextual:
                    hour_delta = abs(int(context.get("hour", 12)) - int(row["hour"]))
                    hour_delta = min(hour_delta, 24 - hour_delta)
                    similarity *= 1.25 if hour_delta <= 2 else 1.0
                    if context.get("weekday") == row["weekday"]: similarity *= 1.2
                    if context.get("room") == row["room"]: similarity *= 1.15
                    if context.get("weather") not in (None, "unknown") and context.get("weather") == row["weather"]: similarity *= 1.1
                score += reward * similarity * 0.5 ** (max(0.0, now_ts - row["ts"]) / (28 * 86400))
                if same_track and now_ts - row["ts"] < 2 * 3600:
                    score -= 1.5  # Modest temporary suppression; favourites remain eligible.
            score += random.Random(f"{identity}:{int(now_ts // 3600)}").uniform(-0.1, 0.1)
            (familiar if trusted else discovery).append((score, item))
        familiar.sort(key=lambda pair: pair[0], reverse=True)
        discovery.sort(key=lambda pair: pair[0], reverse=True)
        fraction = mood["discovery_fraction"] if mood else 0.1
        discovery_count = min(int(limit * fraction), len(discovery), int(len(familiar) * fraction / (1 - fraction)))
        chosen = [i for _, i in familiar[:max(0, limit - discovery_count)]]
        # Discovery never fills a missing familiar baseline beyond its 10% quota.
        if chosen:
            for _, item in discovery[:discovery_count]:
                chosen.insert(min(len(chosen), 9), item)
        return chosen[:max(1, limit)]



class LibraryService:
    def __init__(self):
        self.cfg = _library_cfg()
        self.model = LocalModel(DB_PATH)
        self.session: aiohttp.ClientSession | None = None
        self.context: dict[str, Any] = self._base_context()
        self._current_media: dict[str, Any] | None = None
        self._current_started = 0.0
        self._external_media: dict[str, Any] | None = None
        self._external_started = 0.0
        self._last_run_marker = ""
        self._last_broadcast_prompt = ""
        self._last_broadcast_ts = 0.0
        self._seed_metadata: dict[str, dict[str, Any]] = self.model.get_kv("favorite_tracks", {})
        self._seed_sync_ts = 0.0
        self._automatic_keys: set[tuple[str, str]] = set()
        self._mood_sessions = {}
        self._manual_choices = {}
        self._background = BackgroundTaskSet(log, label="library")

    def _base_context(self, room: str = "") -> dict[str, Any]:
        now = dt.datetime.now()
        return {
            "hour": now.hour,
            "time_bucket": _time_bucket(now.hour),
            "weekpart": _weekpart(now),
            "weekday": now.weekday(),
            "weather": "unknown",
            "temperature": None,
            "room": room,
            "post_run": False,
            "post_run_actionable": False,
            "run_marker": "",
        }

    async def start(self, _app: web.Application) -> None:
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12))
        self._background.spawn(self._context_loop(), name="library_context")
        self._background.spawn(self._seed_loop(), name="library_seed_sync")
        self._background.spawn(self._listening_loop(), name="library_listening")
        self._background.spawn(self._suggestion_loop(), name="library_suggestions")

    async def stop(self, _app: web.Application) -> None:
        await self._background.cancel_all()
        if self._current_media:
            self._finish_current()
        if self._external_media:
            self._finish_external()
        if self.session:
            await self.session.close()
            self.session = None
        self.model.db.close()

    async def _ha_states(self) -> list[dict[str, Any]]:
        if not self.session or not _headers():
            return []
        try:
            async with self.session.get(f"{_ha_base()}/states", headers=_headers()) as resp:
                if resp.status != 200:
                    log.debug("HA states returned %d", resp.status)
                    return []
                data = await resp.json()
                return data if isinstance(data, list) else []
        except Exception as exc:
            log.debug("HA context unavailable: %s", exc)
            return []

    def _detect_recent_run(self, states: list[dict[str, Any]]) -> tuple[bool, str]:
        configured = {
            str(value).strip()
            for value in self.cfg.get("garmin_run_entities", [])
            if str(value).strip()
        }
        best_ts = 0.0
        best_marker = ""
        now = time.time()
        window = max(15, int(self.cfg.get("post_run_window_minutes", 90))) * 60

        for state in states:
            entity = str(state.get("entity_id") or "")
            attrs = state.get("attributes") or {}
            friendly = str(attrs.get("friendly_name") or "")
            value = str(state.get("state") or "")
            blob = " ".join([
                entity, friendly, value,
                str(attrs.get("activity_type") or ""),
                str(attrs.get("activity") or ""),
                str(attrs.get("sport") or ""),
                str(attrs.get("last_activity") or ""),
            ]).casefold()

            if configured:
                if entity not in configured:
                    continue
            else:
                looks_garmin = "garmin" in blob or "last_activity" in entity or "activity" in entity
                if not looks_garmin:
                    continue
            if not any(word in blob for word in RUN_WORDS):
                continue

            changed = _parse_iso(str(state.get("last_changed") or "")) or _parse_iso(str(state.get("last_updated") or ""))
            for key in ("start_time", "end_time", "timestamp", "last_activity_time", "activity_time"):
                changed = max(changed or 0.0, _parse_iso(str(attrs.get(key) or "")) or 0.0)
            if not changed or now - changed > window or changed > now + 300:
                continue
            if changed > best_ts:
                best_ts = changed
                best_marker = f"{entity}:{int(changed)}"

        return bool(best_marker), best_marker

    async def _refresh_context(self) -> None:
        states = await self._ha_states()
        context = self._base_context()
        weather_entity = str(self.cfg.get("weather_entity") or "").strip()
        weather = None
        if weather_entity:
            weather = next((s for s in states if s.get("entity_id") == weather_entity), None)
        if weather is None:
            weather = next((s for s in states if str(s.get("entity_id") or "").startswith("weather.")), None)
        if weather:
            context["weather"] = _weather_bucket(str(weather.get("state") or ""))
            context["temperature"] = _safe_float((weather.get("attributes") or {}).get("temperature"))

        post_run, marker = self._detect_recent_run(states)
        context["post_run"] = post_run
        context["run_marker"] = marker
        handled = self.model.get_kv("handled_run_marker", "")
        context["post_run_actionable"] = post_run and bool(marker) and marker != handled
        self.context = context
        if marker and marker != self._last_run_marker:
            log.info("Recent Garmin run detected marker=%s actionable=%s", marker, context["post_run_actionable"])
            self._last_run_marker = marker

    async def _context_loop(self) -> None:
        while True:
            try:
                await self._refresh_context()
            except Exception:
                log.exception("Context refresh failed")
            await asyncio.sleep(60)

    async def _broadcast_suggestion(self, suggestion: dict[str, Any]) -> None:
        """Push a suggestion to the already-running Chromium UI without waking the display."""
        if not self.session:
            return
        try:
            async with self.session.post(
                ROUTER_BROADCAST,
                json={"type": "context_suggestion", "data": suggestion},
            ) as resp:
                if resp.status >= 400:
                    log.debug("Suggestion broadcast returned HTTP %d", resp.status)
        except Exception as exc:
            log.debug("Suggestion broadcast failed: %s", exc)

    async def _suggestion_loop(self) -> None:
        # Broadcasting to the router WebSocket is deliberately display-neutral:
        # it never calls router wake/backlight endpoints.
        await asyncio.sleep(8)
        while True:
            try:
                suggestion = self.suggestion(str(cfg("device", default="bs5c")))
                prompt_id = str(suggestion.get("id") or "")
                now = time.time()
                # Re-push the same still-relevant prompt every 10 min so a UI
                # reconnect sees it, but avoid nagging continuously.
                if prompt_id and (
                    prompt_id != self._last_broadcast_prompt
                    or now - self._last_broadcast_ts >= 600
                ):
                    await self._broadcast_suggestion(suggestion)
                    self._last_broadcast_prompt = prompt_id
                    self._last_broadcast_ts = now
            except Exception:
                log.exception("Suggestion broadcast failed")
            await asyncio.sleep(45)

    async def _router_media(self) -> dict[str, Any]:
        if not self.session:
            return {}
        try:
            async with self.session.get(ROUTER_MEDIA) as resp:
                if resp.status != 200:
                    return {}
                data = await resp.json()
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _learning_item(self, media: dict[str, Any]) -> dict[str, Any]:
        item = dict(media)
        key = self.model._candidate_key(item)
        session = self._mood_sessions.get(key)
        if session and time.time() - session["ts"] < 7200:
            item["listening_mood"] = session["mood"]
        if time.time() - self._manual_choices.get(key, 0) < 1800:
            item["selection_origin"] = "manual"
        if item.get("selection_origin") not in {"manual", "automatic"}:
            item["selection_origin"] = "automatic" if self.model._candidate_key(item) in self._automatic_keys else "unknown"
        return item

    def _finish_current(self) -> None:
        if not self._current_media or not self._current_started:
            return
        seconds = max(0.0, time.monotonic() - self._current_started)
        if seconds >= 5:
            self.model.record_listen(self._learning_item(self._current_media), seconds, self.context)
        self._current_media = None
        self._current_started = 0.0

    async def _listening_loop(self) -> None:
        while True:
            try:
                media = await self._router_media()
                state = str(media.get("state") or "").lower()
                key = (
                    str(media.get("uri") or media.get("track_uri") or ""),
                    str(media.get("title") or ""),
                    str(media.get("artist") or ""),
                )
                current_key = (
                    str((self._current_media or {}).get("uri") or (self._current_media or {}).get("track_uri") or ""),
                    str((self._current_media or {}).get("title") or ""),
                    str((self._current_media or {}).get("artist") or ""),
                )
                active = state == "playing"
                if active and key != current_key:
                    self._finish_current()
                    self._current_media = dict(media)
                    self._current_started = time.monotonic()
                elif not active and self._current_media:
                    self._finish_current()
            except Exception:
                log.exception("Listening observation failed")
            await asyncio.sleep(12)

    def _load_library(self) -> list[dict[str, Any]]:
        raw: Any = None
        for path in (CACHE_LIBRARY, Path("/home/thomas/beosound5c/web/json/mass_playlists.json")):
            try:
                if path.exists():
                    raw = json.loads(path.read_text())
                    break
            except Exception:
                pass
        if not isinstance(raw, list):
            raw = []

        items: dict[str, dict[str, Any]] = {}

        trusted_names = {str(n).casefold() for n in self.cfg.get("trusted_playlists", ["Trusted music", "All favorited tracks"])}
        def walk(node: Any, inherited_artist: str = "", trusted: bool = False, playlist_tags: str = "", playlist_mode: bool = False) -> None:
            if not isinstance(node, dict): return
            artist = str(node.get("artist") or inherited_artist or "")
            playlist_mode = playlist_mode or node.get("id") == "playlists"
            if playlist_mode and isinstance(node.get("tracks"), list) and node.get("id") != "playlists":
                playlist_tags = playlist_tags + " " + str(node.get("name") or "")
            trusted = trusted or str(node.get("name") or "").casefold() in trusted_names
            children = node.get("tracks")
            uri = str(node.get("url") or node.get("uri") or "")
            if uri and not isinstance(children, list):
                previous = items.get(uri, {})
                items[uri] = {
                    "name": str(node.get("name") or "Unknown"), "title": str(node.get("name") or "Unknown"),
                    "artist": artist, "album": str(node.get("album") or ""), "uri": uri,
                    "image": str(node.get("image") or ""), "media_type": str(node.get("media_type") or "track"),
                    "trusted": trusted or previous.get("trusted", False),
                    "favorite": bool(node.get("favorite") or previous.get("favorite", False)),
                    "genres": node.get("genres") or node.get("genre") or previous.get("genres", ""),
                    "playlist_tags": (str(previous.get("playlist_tags") or "") + " " + playlist_tags).strip(),
                }
            if isinstance(children, list):
                for child in children: walk(child, artist, trusted, playlist_tags, playlist_mode)
        for root in raw:
            if isinstance(root, dict) and str(root.get("id") or "") in {"songs", "playlists"}:
                walk(root)
        for uri, item in self._seed_metadata.items():
            previous = items.get(uri, {})
            items[uri] = {**previous, **item, "trusted": bool(previous.get("trusted") or item.get("trusted"))}
        return list(items.values())

    def _context_for_room(self, room: str) -> dict[str, Any]:
        value = dict(self.context)
        value["room"] = room.strip().lower()
        return value

    async def _seed_loop(self) -> None:
        while True:
            try: await asyncio.wait_for(self._refresh_seed_metadata(), timeout=60)
            except Exception: log.warning("Favourite sync unavailable; retaining cached seeds", exc_info=False)
            await asyncio.sleep(900)

    async def _refresh_seed_metadata(self) -> None:
        if not self.session or time.monotonic() - self._seed_sync_ts < 900: return
        configured = (os.getenv("MASS_WS_URL") or os.getenv("BS5C_MASS_WS_URL") or "").strip()
        host = (os.getenv("PLAYER_IP") or cfg("player", "ip", default="") or "localhost").strip()
        base = configured.replace("wss://", "https://").replace("ws://", "http://").removesuffix("/ws") if configured else f"http://{host}:8095"
        token = os.getenv("MASS_TOKEN", "").strip()
        headers = {"Authorization": "Bearer " + token} if token else {}
        metadata = {}
        # Read-only paginated favourites. Cached trusted playlists work if MA is offline.
        for offset in range(0, 10000, 500):
            async with self.session.post(base.rstrip("/") + "/api", headers=headers,
                json={"command": "music/tracks/library_items", "args": {"favorite": True, "limit": 500, "offset": offset}}) as resp:
                resp.raise_for_status()
                page = await resp.json()
            if not isinstance(page, list): raise ValueError("Invalid MA favourites response")
            for item in page:
                if not item.get("favorite"): continue
                uri = str(item.get("uri") or "")
                if not uri: continue
                artists = item.get("artists") or []
                metadata[uri] = {"uri": uri, "name": str(item.get("name") or ""), "title": str(item.get("name") or ""),
                    "artist": str(artists[0].get("name") or "") if artists else "",
                    "media_type": str(item.get("media_type") or "track"), "favorite": True,
                    "genres": (item.get("metadata") or {}).get("genres") or []}
            if len(page) < 500: break
        self._seed_metadata = metadata
        self.model.put_kv("favorite_tracks", metadata)
        self._seed_sync_ts = time.monotonic()

    def pattern_mood(self, room: str) -> dict | None:
        context = self._context_for_room(room)
        weighted = []
        for row in self.model._history():
            if row["reward"] <= 0 or row["mood_energy"] is None or row["mood_valence"] is None: continue
            hour_delta = abs(int(context.get("hour", 12))-row["hour"])
            hour_delta = min(hour_delta, 24-hour_delta)
            weight = float(row["reward"]) * 0.5 ** ((time.time()-row["ts"])/(28*86400))
            weight *= 2 if hour_delta <= 2 else .5
            if context.get("weekday") == row["weekday"]: weight *= 1.2
            if context.get("room") == row["room"]: weight *= 1.15
            if context.get("weather") not in (None,"unknown") and context.get("weather") == row["weather"]: weight *= 1.1
            weighted.append((weight,row["mood_energy"],row["mood_valence"]))
        if len(weighted) < 5: return None
        total = sum(w for w,_,_ in weighted)
        e, v = sum(w*e for w,e,_ in weighted)/total, sum(w*v for w,_,v in weighted)/total
        return music_mood.selection(math.degrees(math.atan2(2*e-1,2*v-1)), .5)

    async def handle_mood(self, request: web.Request) -> web.Response:
        mood = self.pattern_mood(request.query.get("room", "lounge"))
        return web.json_response({"suggested": mood, "source": "listening_patterns" if mood else "not_enough_history"}, headers={"Access-Control-Allow-Origin":"*"})

    async def recommend_music(self, room: str, limit: int, mood: dict | None = None) -> list[dict[str, Any]]:
        candidates = self._load_library()
        context = self._context_for_room(room)
        if mood or self.pattern_mood(room): context["mood"] = mood or self.pattern_mood(room)
        picks = self.model.rank(candidates, context, max(1, min(limit, 50)))
        self._automatic_keys = {self.model._candidate_key(item) for item in picks}
        return picks

    def suggestion(self, room: str) -> dict[str, Any]:
        context = self._context_for_room(room)
        if context.get("post_run_actionable"):
            marker = context.get("run_marker") or "run"
            suggestion = {
                "id": f"yoga:{marker}",
                "kind": "yoga",
                "question": "Time for some yoga?",
                "options": [
                    {"id": "lounge", "label": "Lounge"},
                    {"id": "bedroom", "label": "Bedroom"},
                    {"id": "dismiss", "label": "No thanks"},
                ],
                "context": context,
            }
        else:
            hour = int(context.get("hour", 12))
            if hour < 10:
                day = dt.date.today().isoformat()
                suggestion = {
                    "id": f"news:{day}",
                    "kind": "news",
                    "question": "Would you like to play the news?",
                    "options": [
                        {"id": "play", "label": "Yes"},
                        {"id": "dismiss", "label": "No thanks"},
                    ],
                    "context": context,
                }
            else:
                bucket = hour // 3
                suggestion = {
                    "id": f"music:{dt.date.today().isoformat()}:{bucket}",
                    "kind": "music",
                    "question": "Would you like to play some music?",
                    "options": [
                        {"id": "play", "label": "Yes"},
                        {"id": "dismiss", "label": "No thanks"},
                    ],
                    "context": context,
                }

        if self.model.get_kv("handled_prompt_id", "") == suggestion["id"]:
            return {}
        return suggestion

    async def _play_music_local(self) -> dict[str, Any]:
        assert self.session
        picks = await self.recommend_music(str(cfg("device", default="bs5c")), 20)
        if not picks:
            raise RuntimeError("No Music Assistant recommendations are available")
        results = []
        for index, item in enumerate(picks):
            payload = {
                "command": "play_now" if index == 0 else "enqueue",
                "url": item["uri"],
            }
            async with self.session.post(MASS_COMMAND, json=payload) as resp:
                body = await resp.json(content_type=None)
                if resp.status >= 400 or body.get("status") == "error":
                    if index == 0:
                        raise RuntimeError(body.get("message") or body.get("reason") or f"MASS HTTP {resp.status}")
                    break
                results.append(body)
            if index == 0:
                await asyncio.sleep(0.3)
        return {"count": len(results), "first": picks[0]}

    async def _play_news_local(self) -> None:
        assert self.session
        async with self.session.post(ROUTER_EVENT, json={"action": "news", "device_type": "Audio"}) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"Router HTTP {resp.status}")

    def _yoga_target_url(self, room: str) -> str:
        targets = self.cfg.get("yoga_targets") or {}
        if isinstance(targets, dict):
            configured = str(targets.get(room) or "").strip()
            if configured:
                return configured.rstrip("/")
        env_name = "KODI_HOST" if room == "lounge" else "BEDROOM_KODI_HOST"
        host = os.getenv(env_name, "").strip()
        if not host:
            return ""
        if host.startswith("http://") or host.startswith("https://"):
            return host.rstrip("/")
        port_name = "KODI_PORT" if room == "lounge" else "BEDROOM_KODI_PORT"
        port = os.getenv(port_name, os.getenv("KODI_PORT", "8080")).strip() or "8080"
        return f"http://{host}:{port}"

    async def _kodi_rpc(self, base_url: str, method: str, params: dict[str, Any]) -> Any:
        assert self.session
        user = os.getenv("KODI_USER", "").strip()
        password = os.getenv("KODI_PASSWORD", "")
        auth = aiohttp.BasicAuth(user, password) if user else None
        async with self.session.post(
            base_url.rstrip("/") + "/jsonrpc",
            json={"jsonrpc": "2.0", "id": "bs5c-library", "method": method, "params": params},
            auth=auth,
        ) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"Kodi HTTP {resp.status}")
            payload = await resp.json(content_type=None)
        if payload.get("error"):
            raise RuntimeError(str(payload["error"].get("message") or payload["error"]))
        return payload.get("result")

    @staticmethod
    def _choose_post_run_yoga(files: list[dict[str, Any]]) -> dict[str, Any] | None:
        candidates = []
        for item in files:
            if str(item.get("filetype") or "").lower() == "directory":
                continue
            text = " ".join([
                str(item.get("label") or ""),
                str(item.get("title") or ""),
                str(item.get("file") or ""),
            ]).casefold().replace("_", " ").replace("-", " ")
            if not all(word in text for word in ("post", "run", "yoga")):
                continue
            score = 0
            if "7min" in text or "7 min" in text or "7 minute" in text:
                score += 20
            if "yoga for runners" in text:
                score += 8
            if text.endswith(".mp4"):
                score += 2
            candidates.append((score, item))
        return max(candidates, key=lambda pair: pair[0])[1] if candidates else None

    async def _run_yoga(self, room: str) -> None:
        """Play the post-run exercise directly on the selected room's Kodi.

        If no Kodi target is configured, retain an optional HA-script fallback
        for installations where room routing is owned by Home Assistant.
        """
        assert self.session
        base_url = self._yoga_target_url(room)
        directory = str(
            self.cfg.get("post_run_yoga_directory")
            or "smb://LOUNGE/SSD/Exercise/"
        ).strip()

        if base_url:
            result = await self._kodi_rpc(
                base_url,
                "Files.GetDirectory",
                {
                    "directory": directory,
                    "media": "video",
                    "properties": ["title", "file"],
                },
            )
            files = result.get("files", []) if isinstance(result, dict) else []
            selected = self._choose_post_run_yoga(files)
            if not selected:
                raise RuntimeError(
                    f"No post-run yoga video found in {directory}"
                )
            path = str(selected.get("file") or "")
            if not path:
                raise RuntimeError("Kodi returned no post-run yoga file")
            await self._kodi_rpc(base_url, "Player.Open", {"item": {"file": path}})
            log.info("Started post-run yoga room=%s file=%s", room, path)
            return

        script = str(self.cfg.get("post_run_yoga_script") or "").strip()
        if not script:
            raise RuntimeError(
                f"No Kodi yoga target is configured for {room}; configure library.yoga_targets"
            )
        if not _headers():
            raise RuntimeError("HA_TOKEN is required for post-run yoga fallback")
        payload = {
            "entity_id": script,
            "variables": {
                "room": room,
                "exercise_path": directory,
                "title": "7min post run yoga",
            },
        }
        async with self.session.post(
            f"{_ha_base()}/services/script/turn_on",
            headers=_headers(),
            json=payload,
        ) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"Home Assistant yoga script returned HTTP {resp.status}")

    async def handle_context(self, request: web.Request) -> web.Response:
        room = request.query.get("room", "")
        return web.json_response(self._context_for_room(room), headers={"Access-Control-Allow-Origin": "*"})

    async def handle_recommend(self, request: web.Request) -> web.Response:
        room = request.query.get("room", "")
        try:
            limit = int(request.query.get("limit", "20"))
        except ValueError:
            limit = 20
        mood = None
        if "angle" in request.query or "radius" in request.query:
            try: mood = music_mood.selection(request.query["angle"], request.query["radius"])
            except (KeyError, TypeError, ValueError): raise web.HTTPBadRequest(text="Invalid mood coordinates")
        picks = await self.recommend_music(room, limit, mood)
        classified = sum(music_mood.profile(i, self.model.get_kv("mood_profiles", {})) is not None for i in picks)
        return web.json_response(
            {
                "model": "trusted-baseline-v2",
                "baseline": {"trusted_playlists": self.cfg.get("trusted_playlists", ["Trusted music", "All favorited tracks"]), "favourites": True, "discovery_fraction": 0.1, "seed_required": True},
                "context": self._context_for_room(room),
                "mood": mood,
                "mood_coverage": {"classified": classified, "total": len(picks), "notice": "Mood data limited; familiar mix" if mood and classified < len(picks) else ""},
                "uris": [item["uri"] for item in picks],
                "items": picks,
            },
            headers={"Access-Control-Allow-Origin": "*"},
        )

    async def handle_suggestions(self, request: web.Request) -> web.Response:
        room = request.query.get("room", "")
        return web.json_response(
            {"primary": self.suggestion(room), "context": self._context_for_room(room)},
            headers={"Access-Control-Allow-Origin": "*"},
        )

    def _finish_external(self) -> None:
        if not self._external_media or not self._external_started:
            return
        elapsed = max(0.0, time.monotonic() - self._external_started)
        duration = _safe_float(self._external_media.get("duration")) or 0.0
        if duration > 0:
            elapsed = min(elapsed, duration + 30.0)
        else:
            elapsed = min(elapsed, 1800.0)
        if elapsed >= 5:
            room = str(self._external_media.get("room") or "")
            self.model.record_listen(
                self._learning_item(self._external_media),
                elapsed,
                self._context_for_room(room),
            )
        self._external_media = None
        self._external_started = 0.0

    async def handle_event(self, request: web.Request) -> web.Response:
        payload = await request.json()
        event_type = str(payload.get("type") or "")
        if event_type == "selection":
            if _music_item(payload) and str(payload.get("uri") or "").strip():
                self._manual_choices[self.model._candidate_key(payload)] = time.time()
            return web.json_response({"status":"ok"}, headers={"Access-Control-Allow-Origin":"*"})
        if event_type == "mood_session":
            try: mood = music_mood.selection(payload["angle"], payload["radius"])
            except (KeyError, TypeError, ValueError): raise web.HTTPBadRequest(text="Invalid mood coordinates")
            items = payload.get("items")
            if not isinstance(items, list) or len(items) > 50: raise web.HTTPBadRequest(text="Invalid mood items")
            for item in items:
                if isinstance(item, dict) and _music_item(item):
                    self._mood_sessions[self.model._candidate_key(item)] = {"mood": mood, "ts": time.time()}
            return web.json_response({"status":"ok"}, headers={"Access-Control-Allow-Origin":"*"})
        if event_type == "mood_profile":
            uri = str(payload.get("uri") or "").strip()
            try: mood = music_mood.selection(payload["angle"], payload["radius"])
            except (KeyError, TypeError, ValueError): raise web.HTTPBadRequest(text="Invalid mood coordinates")
            if not uri: raise web.HTTPBadRequest(text="Track URI required")
            profiles = self.model.get_kv("mood_profiles", {})
            profiles[uri] = {"energy": mood["energy"], "valence": mood["valence"]}
            self.model.put_kv("mood_profiles", profiles)
            return web.json_response({"status": "ok"}, headers={"Access-Control-Allow-Origin": "*"})
        if event_type == "feedback":
            uri = str(payload.get("uri") or "").strip()
            action = str(payload.get("action") or "")
            if not uri or action not in {"like", "dislike", "clear"}: raise web.HTTPBadRequest(text="Invalid feedback")
            feedback = self.model.get_kv("music_feedback", {})
            if action == "clear": feedback.pop(uri, None)
            else: feedback[uri] = action
            self.model.put_kv("music_feedback", feedback)
            return web.json_response({"status": "ok"})
        if event_type == "listen_start":
            key = (
                str(payload.get("title") or "").casefold(),
                str(payload.get("artist") or "").casefold(),
                str(payload.get("room") or "").casefold(),
            )
            previous = self._external_media or {}
            previous_key = (
                str(previous.get("title") or "").casefold(),
                str(previous.get("artist") or "").casefold(),
                str(previous.get("room") or "").casefold(),
            )
            if key != previous_key:
                self._finish_external()
                self._external_media = {
                    "title": str(payload.get("title") or ""),
                    "name": str(payload.get("title") or ""),
                    "artist": str(payload.get("artist") or ""),
                    "album": str(payload.get("album") or ""),
                    "uri": str(payload.get("uri") or ""),
                    "duration": _safe_float(payload.get("duration")) or 0.0,
                    "room": str(payload.get("room") or ""),
                    "media_type": str(payload.get("media_type") or "track"),
                    "selection_origin": str(payload.get("selection_origin") or "unknown"),
                }
                self._external_started = time.monotonic()
            elif payload.get("selection_origin") == "manual" and self._external_media:
                self._external_media["selection_origin"] = "manual"
            return web.json_response({"status": "ok", "observing": True}, headers={"Access-Control-Allow-Origin": "*"})
        if event_type == "listen_stop":
            if payload.get("room") and self._external_media and payload["room"] != self._external_media.get("room"):
                return web.json_response({"status":"ok", "ignored":True}, headers={"Access-Control-Allow-Origin":"*"})
            if self._external_media: self._external_media["end_reason"] = str(payload.get("reason") or "unknown")
            self._finish_external()
            return web.json_response({"status": "ok", "observing": False}, headers={"Access-Control-Allow-Origin": "*"})

        prompt_id = str(payload.get("prompt_id") or "")
        kind = str(payload.get("kind") or "")
        action = str(payload.get("action") or "")
        room = str(payload.get("room") or "")
        if prompt_id:
            self.model.record_prompt(prompt_id, kind, action, room)
            self.model.put_kv("handled_prompt_id", prompt_id)
        if kind == "yoga" and action in {"dismiss", "lounge", "bedroom", "accepted"}:
            marker = self.context.get("run_marker")
            if marker:
                self.model.put_kv("handled_run_marker", marker)
                self.context["post_run_actionable"] = False
        return web.json_response({"status": "ok"}, headers={"Access-Control-Allow-Origin": "*"})

    async def handle_action(self, request: web.Request) -> web.Response:
        payload = await request.json()
        kind = str(payload.get("kind") or "")
        action = str(payload.get("action") or "play")
        prompt_id = str(payload.get("prompt_id") or "")
        room = str(payload.get("room") or "")
        if action == "dismiss":
            if prompt_id:
                self.model.record_prompt(prompt_id, kind, action, room)
                self.model.put_kv("handled_prompt_id", prompt_id)
            if kind == "yoga":
                marker = self.context.get("run_marker")
                if marker:
                    self.model.put_kv("handled_run_marker", marker)
                    self.context["post_run_actionable"] = False
            return web.json_response({"status": "ok", "dismissed": True}, headers={"Access-Control-Allow-Origin": "*"})

        result: dict[str, Any] = {}
        if kind == "music":
            result = await self._play_music_local()
        elif kind == "news":
            await self._play_news_local()
        elif kind == "yoga":
            target = action if action in {"lounge", "bedroom"} else room
            if target not in {"lounge", "bedroom"}:
                raise web.HTTPBadRequest(text="Yoga target must be lounge or bedroom")
            await self._run_yoga(target)
            marker = self.context.get("run_marker")
            if marker:
                self.model.put_kv("handled_run_marker", marker)
                self.context["post_run_actionable"] = False
        else:
            raise web.HTTPBadRequest(text="Unknown library action")

        if prompt_id:
            self.model.record_prompt(prompt_id, kind, action, room)
            self.model.put_kv("handled_prompt_id", prompt_id)
        return web.json_response({"status": "ok", **result}, headers={"Access-Control-Allow-Origin": "*"})

    async def options(self, _request: web.Request) -> web.Response:
        return web.Response(headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        })


def create_app() -> web.Application:
    service = LibraryService()
    app = web.Application()
    app["library_service"] = service
    app.router.add_get("/library/mood", service.handle_mood)
    app.router.add_get("/library/context", service.handle_context)
    app.router.add_get("/library/recommend/music", service.handle_recommend)
    app.router.add_get("/library/suggestions", service.handle_suggestions)
    app.router.add_post("/library/event", service.handle_event)
    app.router.add_post("/library/action", service.handle_action)
    app.router.add_route("OPTIONS", "/library/{tail:.*}", service.options)
    app.on_startup.append(service.start)
    app.on_cleanup.append(service.stop)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=PORT, access_log=None)
