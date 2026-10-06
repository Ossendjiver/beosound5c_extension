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
import sqlite3
import time
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web

# Allow running both from repo and installed ~/beosound5c/services.
HERE = Path(__file__).resolve().parent
import sys
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from lib.config import cfg

log = logging.getLogger("beo-library")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

PORT = int(os.getenv("BS5C_LIBRARY_PORT", "8788"))
ROUTER_MEDIA = "http://127.0.0.1:8770/router/media"
ROUTER_EVENT = "http://127.0.0.1:8770/router/event"
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


class LocalModel:
    """Small persistent contextual recommender.

    This is intentionally transparent rather than a black-box network. Each
    completed listening session becomes a training sample. Ranking is a
    recency-decayed weighted nearest-neighbour estimate across artist, hour,
    weather, temperature, room and weekday/weekend context, with repeat
    suppression and a small exploration window.
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
        title = str(item.get("title") or "").strip()
        artist = str(item.get("artist") or "").strip()
        if not title and not artist:
            return
        duration = _safe_float(item.get("duration")) or 0.0
        # A fast skip is negative evidence; a meaningful listen is positive.
        if seconds < 25:
            reward = -1.0
        elif seconds >= 90 or (duration > 0 and seconds >= duration * 0.45):
            reward = 1.0
        else:
            reward = 0.25
        now = dt.datetime.now()
        self.db.execute(
            """INSERT INTO listens(ts,title,artist,album,uri,seconds,reward,hour,time_bucket,
               weekpart,weather,temperature,room) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                time.time(), title, artist, str(item.get("album") or ""),
                str(item.get("uri") or ""), float(seconds), reward, now.hour,
                context.get("time_bucket", _time_bucket(now.hour)),
                context.get("weekpart", _weekpart(now)),
                context.get("weather", "unknown"), context.get("temperature"),
                context.get("room", ""),
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
        if not candidates:
            return []
        history = self._history()
        now_ts = time.time()
        current_hour = int(context.get("hour", dt.datetime.now().hour))
        current_weather = str(context.get("weather") or "unknown")
        current_temp = _safe_float(context.get("temperature"))
        current_room = str(context.get("room") or "").casefold()
        current_weekpart = str(context.get("weekpart") or "")

        recent_keys = {
            (str(row["artist"]).casefold(), str(row["title"]).casefold())
            for row in history[:80]
        }

        scored: list[tuple[float, dict[str, Any]]] = []
        for item in candidates:
            artist_key, title_key = self._candidate_key(item)
            if not title_key or not item.get("uri"):
                continue
            score = 0.15
            matching_samples = 0
            for row in history:
                row_artist = str(row["artist"]).casefold()
                if artist_key and row_artist != artist_key:
                    continue
                # Artist-level learned preference is the base signal.
                age_days = max(0.0, (now_ts - float(row["ts"])) / 86400.0)
                decay = 0.5 ** (age_days / 28.0)
                similarity = 1.0
                hour_delta = abs(current_hour - int(row["hour"]))
                hour_delta = min(hour_delta, 24 - hour_delta)
                similarity *= 1.8 if hour_delta <= 2 else (1.25 if hour_delta <= 4 else 0.8)
                if str(row["weather"]) == current_weather:
                    similarity *= 1.35
                if current_weekpart and str(row["weekpart"]) == current_weekpart:
                    similarity *= 1.2
                if current_room and str(row["room"]).casefold() == current_room:
                    similarity *= 1.25
                row_temp = _safe_float(row["temperature"])
                if current_temp is not None and row_temp is not None:
                    similarity *= max(0.75, 1.2 - min(abs(current_temp - row_temp), 15.0) / 40.0)
                score += float(row["reward"]) * decay * similarity
                matching_samples += 1
                if matching_samples >= 50:
                    break

            if (artist_key, title_key) in recent_keys:
                score -= 4.5
            # Do not let catalogue order become deterministic when the model is young.
            score += random.Random(f"{title_key}:{int(now_ts // 10800)}").uniform(-0.18, 0.18)
            scored.append((score, item))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        # 12% local exploration from the middle-ranked catalogue keeps learning fresh.
        rng = random.Random(int(now_ts // 3600))
        if len(scored) > max(limit, 8) and rng.random() < 0.12:
            start = max(1, len(scored) // 5)
            end = max(start + 1, min(len(scored), len(scored) * 3 // 5))
            exploration_item = rng.choice(scored[start:end])
            scored = [exploration_item] + [pair for pair in scored if pair is not exploration_item]
        return [item for _, item in scored[: max(1, limit)]]


class LibraryService:
    def __init__(self):
        self.cfg = _library_cfg()
        self.model = LocalModel(DB_PATH)
        self.session: aiohttp.ClientSession | None = None
        self.context: dict[str, Any] = self._base_context()
        self._current_media: dict[str, Any] | None = None
        self._current_started = 0.0
        self._last_run_marker = ""
        self._tasks: list[asyncio.Task] = []

    def _base_context(self, room: str = "") -> dict[str, Any]:
        now = dt.datetime.now()
        return {
            "hour": now.hour,
            "time_bucket": _time_bucket(now.hour),
            "weekpart": _weekpart(now),
            "weather": "unknown",
            "temperature": None,
            "room": room,
            "post_run": False,
            "post_run_actionable": False,
            "run_marker": "",
        }

    async def start(self, _app: web.Application) -> None:
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12))
        self._tasks = [
            asyncio.create_task(self._context_loop(), name="library_context"),
            asyncio.create_task(self._listening_loop(), name="library_listening"),
        ]

    async def stop(self, _app: web.Application) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._current_media:
            self._finish_current()
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

    def _finish_current(self) -> None:
        if not self._current_media or not self._current_started:
            return
        seconds = max(0.0, time.monotonic() - self._current_started)
        if seconds >= 5:
            self.model.record_listen(self._current_media, seconds, self.context)
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
                active = state in ACTIVE_STATES
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
            return []

        items: dict[str, dict[str, Any]] = {}

        def walk(node: Any, inherited_artist: str = "") -> None:
            if not isinstance(node, dict):
                return
            artist = str(node.get("artist") or inherited_artist or "")
            children = node.get("tracks")
            uri = str(node.get("url") or "")
            if uri and not isinstance(children, list):
                key = uri
                items[key] = {
                    "name": str(node.get("name") or "Unknown"),
                    "title": str(node.get("name") or "Unknown"),
                    "artist": artist,
                    "album": str(node.get("album") or ""),
                    "uri": uri,
                    "image": str(node.get("image") or ""),
                }
            if isinstance(children, list):
                for child in children:
                    walk(child, artist)

        for root in raw:
            # Songs plus playlist leaves provide good variety without treating
            # album/artist folder play URLs as individual recommendations.
            if isinstance(root, dict) and str(root.get("id") or "") in {"songs", "playlists"}:
                walk(root)
        return list(items.values())

    def _context_for_room(self, room: str) -> dict[str, Any]:
        value = dict(self.context)
        value["room"] = room.strip().lower()
        return value

    async def recommend_music(self, room: str, limit: int) -> list[dict[str, Any]]:
        candidates = self._load_library()
        return self.model.rank(candidates, self._context_for_room(room), max(1, min(limit, 50)))

    def suggestion(self, room: str) -> dict[str, Any]:
        context = self._context_for_room(room)
        if context.get("post_run_actionable"):
            marker = context.get("run_marker") or "run"
            return {
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
        hour = int(context.get("hour", 12))
        if hour < 10:
            day = dt.date.today().isoformat()
            return {
                "id": f"news:{day}",
                "kind": "news",
                "question": "Would you like to play the news?",
                "options": [{"id": "play", "label": "Yes"}, {"id": "dismiss", "label": "No thanks"}],
                "context": context,
            }
        bucket = hour // 3
        return {
            "id": f"music:{dt.date.today().isoformat()}:{bucket}",
            "kind": "music",
            "question": "Would you like to play some music?",
            "options": [{"id": "play", "label": "Yes"}, {"id": "dismiss", "label": "No thanks"}],
            "context": context,
        }

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

    async def _run_yoga(self, room: str) -> None:
        assert self.session
        script = str(self.cfg.get("post_run_yoga_script") or "script.home_media_post_run_yoga").strip()
        if not _headers():
            raise RuntimeError("HA_TOKEN is required for post-run yoga")
        hint = str(self.cfg.get("post_run_yoga_path") or "LOUNGE/SSD/Exercise/7min post run yoga")
        payload = {
            "entity_id": script,
            "variables": {"room": room, "exercise_path": hint, "title": "7min post run yoga"},
        }
        async with self.session.post(f"{_ha_base()}/services/script/turn_on", headers=_headers(), json=payload) as resp:
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
        picks = await self.recommend_music(room, limit)
        return web.json_response(
            {
                "model": "local-context-knn-v1",
                "context": self._context_for_room(room),
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

    async def handle_event(self, request: web.Request) -> web.Response:
        payload = await request.json()
        prompt_id = str(payload.get("prompt_id") or "")
        kind = str(payload.get("kind") or "")
        action = str(payload.get("action") or "")
        room = str(payload.get("room") or "")
        if prompt_id:
            self.model.record_prompt(prompt_id, kind, action, room)
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
