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
import heapq
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
from lib import skip_policy
from lib import music_mood
from lib import music_familiarity
from lib.mood_mix import MoodMixes
from lib import mix_policy
from lib import music_features, provider_profiles, music_relations
from lib import session_intent
from lib.session_feedback import SessionFeedback
from lib.prompt_playback import Guard as PromptPlaybackGuard

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
            CREATE INDEX IF NOT EXISTS idx_listens_room_ts ON listens(room,ts);
            CREATE TABLE IF NOT EXISTS track_relations (
              source_id INTEGER NOT NULL, target_id INTEGER NOT NULL,
              source_key TEXT NOT NULL, target_key TEXT NOT NULL, room TEXT NOT NULL,
              ts REAL NOT NULL, sign INTEGER NOT NULL, weight REAL NOT NULL,
              UNIQUE(source_id,target_key,sign)
            );
            CREATE INDEX IF NOT EXISTS idx_track_relations_source ON track_relations(source_key,ts);
            CREATE TABLE IF NOT EXISTS feedback_receipts (event_id TEXT PRIMARY KEY, ts REAL NOT NULL);
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
        if "end_reason" not in {row[1] for row in self.db.execute("PRAGMA table_info(listens)")}:
            self.db.execute("ALTER TABLE listens ADD COLUMN end_reason TEXT NOT NULL DEFAULT 'unknown'")
        self.db.commit()
        self.playlist_relations = music_relations.Playlists()

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

    def record_listen(self, item: dict[str, Any], seconds: float, context: dict[str, Any], *, event_id=None, event_ts=None):
        if not _music_item(item):
            return
        title = str(item.get("title") or item.get("name") or "").strip()
        artist = str(item.get("artist") or "").strip()
        if not title and not artist:
            return
        duration = _safe_float(item.get("duration")) or 0.0
        origin = str(item.get("selection_origin") or "unknown")
        reason = str(item.get("end_reason") or "unknown")
        stamp = time.time() if event_ts is None else event_ts
        recent = self.db.execute('SELECT * FROM listens WHERE room=? AND ts BETWEEN ? AND ? AND reward>0 ORDER BY ts DESC LIMIT 10',
            (context.get('room', ''), stamp-3600, stamp)).fetchall()
        replay = origin == 'manual' and any(mix_policy.same_recording(item, dict(r)) for r in recent)
        reward = session_intent.reward(seconds, duration, origin, reason, replay)
        now = dt.datetime.fromtimestamp(stamp)
        with self.db:
            if event_id:
                cursor = self.db.execute("INSERT OR IGNORE INTO feedback_receipts VALUES (?,?)", (event_id,stamp))
                if not cursor.rowcount:
                    return False
            cursor=self.db.execute(
                """INSERT INTO listens(ts,title,artist,album,uri,seconds,reward,hour,time_bucket,
                   weekpart,weather,temperature,room,origin,weekday,mood_energy,mood_valence,end_reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    stamp, title, artist, str(item.get("album") or ""),
                    str(item.get("uri") or ""), float(seconds), reward, now.hour,
                    context.get("time_bucket", _time_bucket(now.hour)),
                    context.get("weekpart", _weekpart(now)),
                    context.get("weather", "unknown"), context.get("temperature"),
                    context.get("room", ""), origin, context.get("weekday", now.weekday()),
                    (item.get("listening_mood") or {}).get("energy"), (item.get("listening_mood") or {}).get("valence"), reason,
                ),
            )
            current=self.db.execute('SELECT * FROM listens WHERE id=?',(cursor.lastrowid,)).fetchone()
            rows=self.db.execute('SELECT * FROM listens WHERE room=? AND id!=? AND ts<=? ORDER BY ts DESC,id DESC LIMIT 6',
                                     (current['room'],current['id'],stamp)).fetchall()
            previous=music_relations.predecessor(rows,current,self.playlist_relations.identity)
            music_relations.record(self.db,previous,current,self.playlist_relations.identity)
            cutoff = time.time() - 180 * 86400
            self.db.execute("DELETE FROM listens WHERE ts < ?", (cutoff,))
            self.db.execute("DELETE FROM track_relations WHERE ts < ?", (cutoff,))
            self.db.execute("DELETE FROM feedback_receipts WHERE ts < ?", (time.time()-30*86400,))
        return True

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
        sound = music_features.Similarity()
        recent_intent = session_intent.accepted(history, context.get('intent_catalogue', candidates), context.get('room', ''), time.time())
        votes = context.get('session_votes') or []
        familiarity = music_familiarity.Prior(self.get_kv("music_familiarity", {}), history)
        feedback = self.get_kv("music_feedback", {})
        meaningful = [r for r in history if r["origin"] == "manual" and r["reward"] >= 1]
        contextual = len(meaningful) >= 20 and len({r["uri"] or r["title"] for r in meaningful}) >= 5
        known = {(r["artist"].casefold(), r["title"].casefold()) for r in meaningful}
        seeds = [i for i in candidates if _music_item(i) and (i.get("trusted") or i.get("favorite") or self._candidate_key(i) in known or feedback.get(i.get("uri")) == "like" or familiarity.baseline(i))]
        # A manually selected session seed remains a preference anchor after
        # its queue item is excluded. Otherwise long-form sessions with no
        # historical favourites lose their entire baseline on the first refill.
        explicit_seed = context.get('seed_features') or {}
        selected_artist = ''
        if explicit_seed.get('trusted') and explicit_seed.get('uri') and _music_item(explicit_seed):
            seeds.append(explicit_seed)
            selected_artist = self._candidate_key(explicit_seed)[0]
        seed_artists = {self._candidate_key(i)[0] for i in seeds if self._candidate_key(i)[0]}
        if not seeds:
            return []  # Never substitute random catalogue entries for an empty baseline.
        mood = context.get("mood")
        mood_profiles = self.get_kv("mood_profiles", {})
        now_ts = time.time()
        skip_history = list(self.db.execute(
            "SELECT * FROM listens WHERE ts>=? AND origin!='legacy' AND (end_reason='skip' OR reward=-2)",
            (now_ts-7*86400,)))
        relations=music_relations.Prior(self.playlist_relations,
            self.db.execute('SELECT * FROM track_relations WHERE ts>=?',(now_ts-music_relations.RETENTION,)),
            context.get('room',''),now_ts)
        relation_root=context.get('relation_seed') or context.get('seed_features') or {}
        if not relation_root:
            relation_root=next((dict(r) for r in history if r['room']==context.get('room') and r['reward']>0),{})
        radio_distances = {}
        familiar, discovery = [], []
        for item in candidates:
            if not _music_item(item) or not item.get("uri"):
                continue
            if any(v['vote'] == -1 and mix_policy.same_recording(item, v['item']) for v in votes):
                continue
            if context.get('radio'):
                distance = music_features.radio_distance(item, explicit_seed)
                if distance is None:continue
                radio_distances[item['uri']] = distance
            if mood and not music_mood.compatible(item, mood, mood_profiles):
                continue  # Familiarity never admits an unknown or incompatible mood.
            key = self._candidate_key(item)
            identity = item["uri"]
            blocked, skip_penalty = skip_policy.recent(item, skip_history, now_ts)
            if blocked: continue
            if feedback.get(identity) == "dislike":
                continue
            trusted = item.get("trusted") or item.get("favorite") or key in known or feedback.get(identity) == "like" or familiarity.baseline(item) or bool(selected_artist and key[0] == selected_artist)
            if not trusted and key[0] not in seed_artists and not context.get('radio') and not (mood and mood["discovery_fraction"] >= .1 and music_mood.profile(item, mood_profiles) and music_mood.adjustment(item, mood, mood_profiles) >= -2):
                continue  # Discovery is constrained to artists already represented by seeds.
            score = 12.0 if item.get("favorite") else 10.0 if trusted else 0.0
            score += familiarity.boost(item) - skip_penalty
            if context.get("seed_artist") == key[0] and key[0]:
                score += 3.5
            if mood:
                score += music_mood.adjustment(item, mood, mood_profiles)
            score += .5 * sound(item, explicit_seed)
            score += session_intent.bonus(item, recent_intent, votes, {}, similarity=sound)
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
        bands={}
        def band(item):
            uri=item['uri']
            if uri not in bands:
                if context.get('radio'):
                    bands[uri]=int(radio_distances[uri]/.1)
                    return bands[uri]
                bands[uri]=int(music_mood.distance(item,mood,mood_profiles)/.05) if mood else 0
            return bands[uri]
        fraction = mood["discovery_fraction"] if mood else 0.1
        discovery_count = min(int(math.floor(limit * fraction + .5)), len(discovery), int(len(familiar) * fraction / (1 - fraction)))
        from lib import queue_spacing
        artist_names = {item['uri']: queue_spacing.artist(item) for _,item in familiar+discovery}
        slots=queue_spacing.slots(min(len(familiar),max(0,limit-discovery_count)), discovery_count)
        pools={'familiar':familiar,'discovery':discovery}
        chosen=[];anchor=relation_root
        recent=[queue_spacing.artist(i) for i in context.get('queue_previous', [])][-3:]
        root_artist=queue_spacing.artist(context.get('relation_seed') or context.get('seed_features') or {})
        if root_artist and (not recent or recent[-1]!=root_artist):recent.append(root_artist)
        for category in slots:
            pool=pools[category]
            # Re-evaluate each immediate predecessor. A -> B aversion must not
            # suppress B globally after choosing C; neither signal crosses a mood band.
            # Refine a bounded shortlist acoustically, rather than comparing
            # every library embedding with every upcoming track on the device.
            shortlist=heapq.nsmallest(64, queue_spacing.spaced(pool,recent,artist_names),
                key=lambda pair:(band(pair[1]),-(pair[0]+relations.boost(anchor,pair[1]))))
            selected=min(shortlist,key=lambda pair:(band(pair[1]),-(pair[0]+relations.boost(anchor,pair[1])+.7*sound(pair[1],anchor))))
            pool.remove(selected);chosen.append(selected[1]);anchor=selected[1]
            recent.append(queue_spacing.artist(selected[1]));recent=recent[-3:]
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
        self._prompt_playback = PromptPlaybackGuard()
        self._seed_metadata: dict[str, dict[str, Any]] = self.model.get_kv("favorite_tracks", {})
        self._seed_sync_ts = 0.0
        self._familiarity_status = self.model.get_kv("music_familiarity_status", {})
        self._automatic_keys: set[tuple[str, str]] = set()
        self._mood_sessions = {}
        self._audio_feature_stamp = None
        self._audio_feature_cache = {}
        self.mixes = MoodMixes(self._mix_command, self._mix_recommend, self._mix_record,
            load=lambda: self.model.get_kv("mix_sessions_v1", {}),
            save=lambda data: self.model.put_kv("mix_sessions_v1", data))
        self.session_feedback = SessionFeedback(lambda: self.model.get_kv('queue_feedback_v1', {}),
            lambda data: self.model.put_kv('queue_feedback_v1', data))
        self._manual_choices = {tuple(k.split('\0')):v for k,v in self.model.get_kv('manual_choices_v1',{}).items() if isinstance(v,dict) and v.get('expires',0)>time.time()}
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
        self._background.spawn(self._mix_loop(), name="library_mood_mix")
        self._background.spawn(self._seed_loop(), name="library_seed_sync")
        self._background.spawn(self._familiarity_loop(), name="library_familiarity_sync")
        self._background.spawn(self._metadata_loop(), name="library_metadata_sync")
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
        media_keys = {s.get('entity_id') for s in states if str(s.get('entity_id', '')).startswith('media_player.')}
        for key in list(self._prompt_playback.states):
            if key.startswith('media_player.') and states and key not in media_keys:
                self._prompt_playback.states.pop(key, None)
                self._prompt_playback.paused.pop(key, None)
        for state in states:
            if state.get('entity_id') in media_keys:
                self._prompt_playback.observe(state['entity_id'], state.get('state'), time.time(), state.get('last_changed'))
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
                if suggestion.get('clear') and self._last_broadcast_prompt:
                    await self._broadcast_suggestion(suggestion)
                    self._last_broadcast_prompt = ''
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
        mark=self._manual_choices.get(key)
        matches_room=not isinstance(mark,dict) or not mark.get('room') or mark['room']==str(item.get('room') or 'lounge')
        valid=mark.get('expires',0)>time.time() if isinstance(mark,dict) else time.time()-float(mark or 0)<1800
        if valid and matches_room:
            item["selection_origin"] = "manual"
        if item.get("selection_origin") not in {"manual", "automatic"}:
            item["selection_origin"] = "automatic" if self.model._candidate_key(item) in self._automatic_keys else "unknown"
        return item

    def _persist_manual_choices(self):
        self._manual_choices={k:v for k,v in self._manual_choices.items() if isinstance(v,dict) and v.get('expires',0)>time.time()}
        self.model.put_kv('manual_choices_v1',{'\0'.join(k):v for k,v in self._manual_choices.items()})

    def _start_learning_item(self, media):
        item=self._learning_item(media)
        if item.get('selection_origin')=='manual' and self._manual_choices.pop(self.model._candidate_key(item),None) is not None:
            self._persist_manual_choices()
        return item

    def _finish_current(self) -> None:
        if not self._current_media or not self._current_started:
            return
        seconds = max(0.0, time.monotonic() - self._current_started)
        if seconds >= 5:
            self.model.record_listen(self._learning_item(self._current_media), seconds, self._context_for_room(str(self._current_media.get('room') or 'lounge')))
        self._current_media = None
        self._current_started = 0.0

    async def _listening_loop(self) -> None:
        while True:
            try:
                media = await self._router_media()
                state = str(media.get("state") or "").lower()
                self._prompt_playback.observe('router', state, time.time(), media.get('last_changed'))
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
                    self._current_media = self._start_learning_item(media)
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
                    "version": str(node.get("version") or previous.get("version") or ""),
                    "duration": _safe_float(node.get("duration")) or previous.get("duration", 0),
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
            items[uri] = music_features.merge(previous, item)
        for entry in self.model.get_kv("music_familiarity", {}).values():
            item = entry.get("item", {})
            uri = item.get("uri")
            if not uri: continue
            previous = items.get(uri, {})
            items[uri] = music_features.merge(item, previous)
        for uri, metadata in self.model.get_kv('track_metadata', {}).items():
            if uri in items:
                items[uri] = music_features.merge(items[uri], metadata)
        # A worker writes this sidecar atomically; the playback process does no DSP.
        feature_path = Path(os.getenv('BS5C_AUDIO_FEATURES_FILE') or self.cfg.get('audio_features_file') or CACHE_LIBRARY.parent / 'audio_features.json')
        try:
            stat = feature_path.stat()
            stamp = (str(feature_path), stat.st_mtime_ns, stat.st_size)
            if stamp != getattr(self, '_audio_feature_stamp', None):
                payload = json.loads(feature_path.read_text())
                if payload.get('version') != 1 or not isinstance(payload.get('tracks'), dict):
                    raise ValueError('Invalid audio feature sidecar')
                feature_cache = {}
                for uri, entry in payload['tracks'].items():
                    if not isinstance(entry, dict):
                        continue
                    features = music_features.calibrate(music_features.clean(entry.get('audio_features')), payload.get('mood_calibration'))
                    feature_cache[uri] = features
                    # Only exact MA provider mappings supplied by the analyser;
                    # artist/title similarity is never an identity assertion.
                    aliases = entry.get('aliases', [])
                    if isinstance(aliases, list):
                        for alias in aliases:
                            if isinstance(alias, str) and 0 < len(alias) <= 1024:
                                feature_cache.setdefault(alias, features)
                self._audio_feature_calibration = payload.get('mood_calibration')
                self._audio_feature_cache = feature_cache
                self._audio_feature_stamp = stamp
            for uri, features in self._audio_feature_cache.items():
                if uri in items and features:
                    items[uri]['audio_features'] = features
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        provider_path = Path(os.getenv('BS5C_PROVIDER_PROFILES_FILE') or CACHE_LIBRARY.parent / 'provider_profiles.json')
        try:
            stat = provider_path.stat()
            stamp = (str(provider_path), stat.st_mtime_ns, stat.st_size, getattr(self, '_audio_feature_stamp', None))
            if stamp != getattr(self, '_provider_profile_stamp', None):
                self._provider_profile_cache = provider_profiles.load(json.loads(provider_path.read_text()), getattr(self, '_audio_feature_calibration', None))
                self._provider_profile_stamp = stamp
            for uri, (metadata, source) in self._provider_profile_cache.items():
                if uri in items:
                    incoming = dict(metadata)
                    # A short provider preview never replaces the richer local profile.
                    if source == 'ma_preview' and items[uri].get('audio_features'):
                        incoming.pop('audio_features', None)
                    items[uri] = music_features.merge(items[uri], incoming)
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        playlist_stamp=(str(path),path.stat().st_mtime_ns,path.stat().st_size) if path.exists() else None
        if playlist_stamp != getattr(self,'_playlist_relation_stamp',None):
            self.model.playlist_relations=music_relations.Playlists(raw,list(items.values()))
            self._playlist_relation_stamp=playlist_stamp
        if not self.model.get_kv('track_relations_backfill_v1',False):
            with self.model.db:
                music_relations.backfill(self.model.db,self.model.playlist_relations.identity)
                self.model.db.execute("INSERT INTO kv(key,value) VALUES('track_relations_backfill_v1','true') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
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
                    "duration": _safe_float(item.get("duration")) or 0,
                    "media_type": str(item.get("media_type") or "track"), "favorite": True,
                    "genres": (item.get("metadata") or {}).get("genres") or []}
            if len(page) < 500: break
        self._seed_metadata = metadata
        self.model.put_kv("favorite_tracks", metadata)
        self._seed_sync_ts = time.monotonic()

    async def _familiarity_loop(self) -> None:
        while True:
            last_check = self.model.get_kv("music_familiarity_last_check", 0)
            wait = max(0, float(last_check) + 86400 - time.time())
            if wait:
                await asyncio.sleep(wait)
            try:
                await asyncio.wait_for(self._refresh_familiarity(), timeout=120)
            except asyncio.CancelledError: raise
            except Exception:
                log.warning("Familiarity sync unavailable; retaining cached evidence", exc_info=False)
            await asyncio.sleep(86400)

    async def _metadata_loop(self) -> None:
        # MA API only; never open its SQLite database or interrupt playback.
        await asyncio.sleep(30)
        while True:
            try:
                await self._refresh_track_metadata()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning('Track metadata enrichment unavailable; retaining cache')
            await asyncio.sleep(900)

    async def _refresh_track_metadata(self) -> None:
        if not self.session:
            return
        configured = (os.getenv('MASS_WS_URL') or os.getenv('BS5C_MASS_WS_URL') or '').strip()
        host = (os.getenv('PLAYER_IP') or cfg('player', 'ip', default='') or 'localhost').strip()
        base = configured.replace('wss://', 'https://').replace('ws://', 'http://').removesuffix('/ws') if configured else f'http://{host}:8095'
        token = os.getenv('MASS_TOKEN', '').strip()
        headers = {'Authorization': 'Bearer '+token} if token else {}
        cache = self.model.get_kv('track_metadata', {})
        now = time.time()
        batch = max(0, min(40, int(self.cfg.get('metadata_enrichment_batch', 24))))
        candidates = [i for i in self._load_library() if i.get('uri') and
                      now-float(cache.get(i['uri'], {}).get('_checked_at', 0)) >= 30*86400]
        # Prioritize seeds, then missing descriptors. Rotate by last attempt.
        candidates.sort(key=lambda i: (float(cache.get(i['uri'], {}).get('_checked_at', 0)),
                                      not (i.get('trusted') or i.get('favorite'))))
        for item in candidates[:batch]:
            uri = item['uri']
            try:
                async with self.session.post(base.rstrip('/')+'/api', headers=headers,
                    timeout=aiohttp.ClientTimeout(total=4),
                    json={'command':'music/item_by_uri', 'args':{'uri':uri, 'allow_update_metadata':False}}) as response:
                    response.raise_for_status()
                    detail = await response.json()
                if not isinstance(detail, dict) or not detail.get('uri'):
                    raise ValueError('Invalid MA track metadata')
                cache[uri] = music_features.merge(cache.get(uri, {}), music_features.metadata(detail))
                cache[uri]['_checked_at'] = now
            except asyncio.CancelledError:
                raise
            except Exception:
                # Retry failed reads next day, not every 15 minutes.
                cache.setdefault(uri, {})['_checked_at'] = now-29*86400
            await asyncio.sleep(.1)
        if candidates[:batch]:
            self.model.put_kv('track_metadata', cache)  # One local transaction per batch.

    async def _refresh_familiarity(self) -> None:
        if not self.session: return
        configured = (os.getenv("MASS_WS_URL") or os.getenv("BS5C_MASS_WS_URL") or "").strip()
        host = (os.getenv("PLAYER_IP") or cfg("player", "ip", default="") or "localhost").strip()
        base = configured.replace("wss://", "https://").replace("ws://", "http://").removesuffix("/ws") if configured else f"http://{host}:8095"
        token = os.getenv("MASS_TOKEN", "").strip()
        headers = {"Authorization": "Bearer " + token} if token else {}
        async def command(name, args):
            async with self.session.post(base.rstrip("/") + "/api", headers=headers,
                json={"command": name, "args": args}) as response:
                response.raise_for_status()
                return await response.json()
        evidence, status = await music_familiarity.collect(command, self.model.get_kv("music_familiarity", {}))
        self.model.put_kv("music_familiarity", evidence)
        self.model.put_kv("music_familiarity_status", status)
        self._familiarity_status = status
        self.model.put_kv("music_familiarity_last_check", time.time())

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
        return music_mood.selection(math.degrees(math.atan2(2*e-1,2*v-1)), .250001)

    async def handle_mood(self, request: web.Request) -> web.Response:
        mood = self.pattern_mood(request.query.get("room", "lounge"))
        return web.json_response({"suggested": mood, "source": "listening_patterns" if mood else "not_enough_history"}, headers={"Access-Control-Allow-Origin":"*"})

    async def recommend_music(self, room: str, limit: int, mood: dict | None = None) -> list[dict[str, Any]]:
        candidates = self._load_library()
        context = self._context_for_room(room)
        if mood or self.pattern_mood(room): context["mood"] = mood or self.pattern_mood(room)
        playing=self._external_media if self._external_media and self._external_media.get('room')==room else self._current_media
        if playing and str(playing.get('room') or 'lounge')==room:
            context['relation_seed'] = music_features.merge(next((c for c in candidates if mix_policy.same_recording(c, playing)), {}), playing)
        picks = self.model.rank(candidates, context, max(1, min(limit, 50)))
        self._automatic_keys = {self.model._candidate_key(item) for item in picks}
        return picks

    async def _mix_command(self, command, **payload):
        assert self.session
        async with self.session.post(MASS_COMMAND, json={"command": command, **payload}) as response:
            result = await response.json()
            if response.status >= 400:
                raise ValueError(result.get("reason") or "Mix queue request failed")
            return result

    async def _mix_recommend(self, room, limit, mood, seed, policy=None):
        candidates = self._load_library()
        context = self._context_for_room(room)
        policy = policy or {}
        radio = policy.get('mode') == 'radio'
        context['radio'] = radio
        selected = mood if radio else mood or self.pattern_mood(room)
        if selected:
            context["mood"] = selected
        if seed and seed.get("uri"):
            cached_seed = next((c for c in candidates if c.get('uri') == seed['uri']), {})
            # A manually chosen library item need not be in the playlist catalogue.
            profile = getattr(self, '_provider_profile_cache', {}).get(seed['uri'])
            if profile:
                cached_seed = music_features.merge(cached_seed, profile[0])
            features = getattr(self, '_audio_feature_cache', {}).get(seed['uri'])
            if features:
                cached_seed = music_features.merge(cached_seed, {'audio_features': features})
            seed = music_features.merge(cached_seed, seed)
            seed = dict(seed, trusted=True, media_type=seed.get("media_type") or "track")
            candidates = [seed] + [c for c in candidates if c.get("uri") != seed["uri"]]
            context["seed_artist"] = mix_policy.identity(seed)[0]
            context['seed_features'] = seed
        # Keep descriptors for already-played songs as intent evidence even
        # though recording/length rules subsequently remove them from picks.
        context['intent_catalogue'] = candidates
        candidates = [c for c in candidates if mix_policy.similar_length(c, seed or {})]
        policy = policy or {}
        current = policy.get('relation_seed') or seed or {}
        context['relation_seed'] = music_features.merge(next((c for c in candidates if c.get('uri') == current.get('uri')), {}), current)
        context['session_votes'] = [dict(v, item=music_features.merge(next((c for c in candidates if mix_policy.same_recording(c, v['item'])), {}), v['item']))
            for v in self.session_feedback.votes(policy.get('queue_id', ''),
                (self.mixes.sessions.get(policy.get('queue_id', '')) or {}).get('session_id'))]
        excluded = set(policy.get("exclude", []))
        previous = policy.get("previous", [])
        context['queue_previous'] = previous
        versions = policy.get("versions", [])
        # Filter before ranking, so lower ranked eligible tracks are not starved.
        previous_index = mix_policy.RecordingIndex(previous)
        version_index = mix_policy.RecordingIndex(versions)
        candidates = [c for c in candidates if c.get("uri") not in excluded
                      and not previous_index.matches(c)
                      and not version_index.matches(c, alternate_only=True)]
        picks = self.model.rank(candidates, context, limit)
        self._automatic_keys.update(self.model._candidate_key(item) for item in picks)
        return picks

    def _mix_record(self, mood, picks):
        for item in picks:
            self._mood_sessions[self.model._candidate_key(item)] = {"mood": mood, "ts": time.time()}

    async def _mix_loop(self):
        while True:
            for queue in list(self.mixes.sessions):
                try:
                    await self.mixes.tick(queue)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.warning("Mood mix monitor: %s", str(exc)[:300])
            await asyncio.sleep(1)

    async def handle_mix(self, request):
        try:
            if request.method == "GET":
                queue = request.query.get('queue_id', '')
                result = self.mixes.public(queue)
                if queue and len(queue) <= 512 and request.query.get('feedback') == '1':
                    snapshot = await self.mixes.snapshot(queue)
                    result['feedback'] = self.session_feedback.state(queue, snapshot, (self.mixes.sessions.get(queue) or {}).get('session_id'))
                return web.json_response(result, headers={"Access-Control-Allow-Origin": "*"})
            data = await request.json()
            queue = str(data.get("queue_id") or "").strip()
            if not queue or len(queue) > 512:
                raise ValueError("A queue is required")
            if data.get("action") == "begin":
                result = await self.mixes.begin(queue, str(data.get("room") or "lounge"), data.get("seed"), data.get("mode", "mood"))
            elif data.get("action") == "update":
                result = await self.mixes.update(queue, data["angle"], data["radius"])
            elif data.get("action") == "stop":
                self.mixes.stop(queue)
                result = self.mixes.public(queue)
            elif data.get('action') == 'feedback':
                async with self.mixes.lock(queue):
                    snapshot = await self.mixes.snapshot(queue)
                    session = self.mixes.sessions.get(queue) or {}
                    state = self.session_feedback.state(queue, snapshot, session.get('session_id'))
                    voted = self.session_feedback.vote(queue, state, data.get('session_id'), data.get('current_item_id'), data.get('vote'))
                    if session and not session['awaiting_choice']:
                        session['generation'] += 1
                        session.update(pending_refresh=True, refresh_status='pending')
                        session.pop('next_refill', None)
                        session.pop('retry_after', None)
                        self.mixes.persist()
                    result = self.mixes.public(queue) | {'feedback': voted}
            else:
                raise ValueError("Unknown mix action")
            return web.json_response(result, headers={"Access-Control-Allow-Origin": "*"})
        except (ValueError, KeyError, TypeError) as exc:
            log.warning('Mood mix request rejected: %s', str(exc)[:300])
            raise web.HTTPBadRequest(text=str(exc))

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

        if suggestion['kind'] == 'music' and self._prompt_playback.blocked(time.time()):
            return {'clear': True, 'kind': 'music'}
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
        queue = str(results[0].get("queue_id") or "") if results else ""
        if queue:
            await self.mixes.begin(queue, str(cfg("device", default="bs5c")), picks[0], mode="radio")
        return {"count": len(results), "first": picks[0], "continuous": bool(queue)}

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
        mood = mood or self.pattern_mood(room)
        picks = await self.recommend_music(room, limit, mood)
        classified = sum(music_mood.profile(i, self.model.get_kv("mood_profiles", {})) is not None for i in picks)
        return web.json_response(
            {
                "model": "mood-first-acoustic-v5",
                "familiarity": {"max_boost": music_familiarity.MAX_BOOST, "status": self._familiarity_status},
                "baseline": {"trusted_playlists": self.cfg.get("trusted_playlists", ["Trusted music", "All favorited tracks"]), "favourites": True, "most_played_playlist_prefix": "most played", "discovery_fraction": 0.1, "seed_required": True},
                "context": self._context_for_room(room),
                "mood": mood,
                "mood_coverage": {"classified": classified, "total": len(picks),
                    "notice": "No mood-compatible tracks with sufficient metadata" if mood and not picks else "",
                    "unknown_moods_excluded": bool(mood)},
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

    async def handle_status(self, request: web.Request) -> web.Response:
        path = Path(os.getenv('BS5C_PROVIDER_PROFILES_FILE') or CACHE_LIBRARY.parent / 'provider_profiles.json').with_suffix('.status.json')
        try:
            provider = json.loads(path.read_text())
        except (OSError, ValueError):
            provider = {}
        return web.json_response({'library': {
            'status': 'running', 'mix_sessions': len(self.mixes.sessions),
            'listens': self.model.db.execute('SELECT count(*) FROM listens').fetchone()[0],
            'relationships': self.model.db.execute('SELECT count(*) FROM track_relations').fetchone()[0],
            'music_prompts_suppressed': self._prompt_playback.blocked(time.time())},
            'provider': provider}, headers={'Access-Control-Allow-Origin': '*'})

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
        if event_type == "listen_feedback":
            event_id = str(payload.get("event_id") or "")
            try:
                stamp = float(payload["timestamp_ms"])/1000
                seconds = float(payload.get("seconds") or 0)
                duration = float(payload.get("duration") or 0)
                if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}",event_id) or payload.get("reason") != "skip": raise ValueError()
                if not math.isfinite(stamp) or not time.time()-30*86400 <= stamp <= time.time()+300: raise ValueError()
                if not math.isfinite(seconds) or not math.isfinite(duration) or seconds < 0 or duration < 0: raise ValueError()
                if not payload.get("title") or not _music_item(payload): raise ValueError()
            except (KeyError,TypeError,ValueError): raise web.HTTPBadRequest(text="Invalid listening feedback")
            item=dict(payload,end_reason="skip",selection_origin="reported")
            accepted=self.model.record_listen(item,min(seconds,duration or 12*3600),
                self._context_for_room(str(payload.get("room") or "phone")),event_id=event_id,event_ts=stamp)
            if accepted and self._external_media and skip_policy.same(item,self._external_media):
                self._external_media=None; self._external_started=0.0
            return web.json_response({"status":"ok","duplicate":accepted is False},headers={"Access-Control-Allow-Origin":"*"})
        if event_type == "selection":
            if _music_item(payload) and str(payload.get("uri") or "").strip():
                key=self.model._candidate_key(payload)
                queued=payload.get('action') in ('play_next','queue_add','queue_item','add','next')
                self._manual_choices[key]={'expires':time.time()+(72*3600 if queued else 1800),'room':str(payload.get('room') or '')}
                for media in (self._current_media,self._external_media):
                    if not queued and media and self.model._candidate_key(media)==key:
                        media['selection_origin']='manual'
                        self._manual_choices.pop(key,None)
                self._persist_manual_choices()
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
                self._external_media=self._start_learning_item(self._external_media)
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
    app.router.add_get("/library/mix", service.handle_mix)
    app.router.add_post("/library/mix", service.handle_mix)
    app.router.add_get("/library/mood", service.handle_mood)
    app.router.add_get("/library/context", service.handle_context)
    app.router.add_get("/library/status", service.handle_status)
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
