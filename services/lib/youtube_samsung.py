"""Conservative Samsung YouTube-on-TV observer for BS5c.

Only confirmed HA entity video identity and measured playback position are accepted.
SamsungTV Smart's launch acknowledgement, power state and guessed elapsed time
are *not* watch events. This runs on BS5c even when Home Media is closed.
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging
import os
import re
import time
from ipaddress import ip_address
from urllib.parse import parse_qs, urlsplit

import aiohttp

log = logging.getLogger(__name__)
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
DEFAULT_ENTITY = "media_player.youtube_on_frame_65"
PRESENCE_ENTITY = "person.thomas"


def samsung_history_enabled(config):
    """Require an explicit boolean opt-in; absent/malformed settings are OFF."""
    return isinstance(config, dict) and config.get("include_samsung") is True


def is_user_home(state):
    """The actual person entity must report home. Unknown/away always fail closed."""
    return isinstance(state, dict) and state.get("entity_id") == PRESENCE_ENTITY and state.get("state") == "home"



def video_id_from_content(content):
    if not isinstance(content, str):
        return ""
    content = content.strip()
    if VIDEO_ID.fullmatch(content):
        return content
    try:
        parsed = urlsplit(content)
        if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
            return ""
        host = (parsed.hostname or "").lower()
        if host == "youtu.be":
            value = parsed.path.strip("/").split("/")[0]
        elif host in ("youtube.com", "www.youtube.com", "m.youtube.com"):
            value = (parsed.path.split("/")[2] if parsed.path.startswith(("/shorts/", "/embed/"))
                     else parse_qs(parsed.query).get("v", [""])[0])
        else:
            return ""
        return value if VIDEO_ID.fullmatch(value) else ""
    except (ValueError, IndexError):
        return ""


def sample_youtube_state(state, *, now_ms=None, device_id="lounge"):
    """Return a verified observed event or None; no playback-time extrapolation."""
    if not isinstance(state, dict) or state.get("state") not in ("playing", "paused"):
        return None
    attrs = state.get("attributes")
    if not isinstance(attrs, dict):
        return None
    ident = video_id_from_content(attrs.get("media_content_id"))
    if not ident:
        return None
    try:
        position = float(attrs["media_position"])
        duration = float(attrs.get("media_duration") or 0)
        if not (1 <= position <= 86_400 and 0 <= duration <= 86_400):
            return None
    except (ValueError, TypeError, KeyError, OverflowError):
        return None
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    last_updated = state.get("last_updated")
    if not isinstance(last_updated, str):
        return None
    try:
        updated = dt.datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
        if updated.tzinfo is None or abs(now_ms - int(updated.timestamp() * 1000)) > 300_000:
            return None
    except (ValueError, OverflowError):
        return None
    title = str(attrs.get("media_title") or "")[:300].replace("\n", " ").replace("\r", " ")
    channel = str(attrs.get("media_artist") or "")[:150].replace("\n", " ").replace("\r", " ")
    return {
        "video_id": ident, "device_id": device_id, "title": title, "channel": channel,
        "position_ms": int(min(position, duration) * 1000) if duration else int(position * 1000),
        "duration_ms": int(duration * 1000), "watched_at_ms": now_ms,
        "ended": bool(duration and position >= duration * 0.9),
    }


def _private_ha_api_url(raw):
    parsed = urlsplit(raw.rstrip("/"))
    if parsed.scheme not in ("http", "https") or parsed.username or parsed.password or not parsed.hostname:
        return ""
    host = parsed.hostname.lower()
    try:
        allowed = ip_address(host).is_private
    except ValueError:
        allowed = host.endswith(".local") or host == "homeassistant"
    if not allowed or parsed.query or parsed.fragment or parsed.path not in ("", "/", "/api"):
        return ""
    return raw.rstrip("/") + ("" if parsed.path == "/api" else "/api") + "/states/"


class SamsungWatchMonitor:
    def __init__(self, history, session, *, host, token, entity=DEFAULT_ENTITY):
        self.history = history
        self.session = session
        self.host = host
        self.token = token
        self.entity = entity
        self.last_signal = None

    @staticmethod
    def enabled():
        # Existing BS5c settings page persists this to /etc/beosound5c/config.json.
        # The config loader caches until a normal Save & Restart; fail closed.
        try:
            from .config import cfg
            return samsung_history_enabled(cfg("youtube_history", default={}))
        except (OSError, ValueError, TypeError, RuntimeError):
            return False

    async def _ha_state(self, entity):
        async with self.session.get(self.host + entity,
                                    headers={"Authorization": "Bearer " + self.token},
                                    timeout=aiohttp.ClientTimeout(total=8),
                                    allow_redirects=False) as response:
            if response.status != 200:
                log.debug("Samsung HA observer unavailable for %s (HTTP %s)", entity, response.status)
                return None
            data = await response.json()
            return data if isinstance(data, dict) else None

    async def sample(self):
        if not self.enabled():
            self.last_signal = None
            return
        # Presence first, then actual media state. Recheck presence immediately
        # before a write so a departure between requests cannot log a watch.
        present = await self._ha_state(PRESENCE_ENTITY)
        if not is_user_home(present):
            self.last_signal = None
            return
        video = await self._ha_state(self.entity)
        event = sample_youtube_state(video)
        if event is None:
            self.last_signal = None
            return
        present = await self._ha_state(PRESENCE_ENTITY)
        if not is_user_home(present) or not self.enabled():
            self.last_signal = None
            return
        signal = (event["video_id"], event["position_ms"], video["state"])
        if signal == self.last_signal:
            return
        self.history.upsert(event)
        self.last_signal = signal

    async def run(self):
        while True:
            try:
                await self.sample()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.debug("Samsung YouTube state unavailable: %s", type(exc).__name__)
            await asyncio.sleep(30)


def install(app, history):
    """Observe Home Assistant only when an existing LAN token is configured."""
    url = _private_ha_api_url(os.getenv("BS5C_YOUTUBE_HA_URL") or
                              os.getenv("HA_URL", "http://homeassistant.local:8123"))
    token = os.getenv("BS5C_YOUTUBE_HA_TOKEN") or os.getenv("HA_TOKEN", "")
    entity = os.getenv("BS5C_YOUTUBE_SAMSUNG_ENTITY", DEFAULT_ENTITY).strip()
    if not url or not token or not re.fullmatch(r"media_player\.[a-z0-9_]+", entity):
        log.info("Samsung YouTube observer disabled (HA LAN URL/token/entity not configured)")
        return False

    async def start(_app):
        session = aiohttp.ClientSession()
        monitor = SamsungWatchMonitor(history, session, host=url, token=token, entity=entity)
        app["youtube_samsung_monitor"] = monitor
        app["youtube_samsung_task"] = asyncio.create_task(monitor.run(), name="youtube-samsung")

    async def stop(_app):
        task = app.get("youtube_samsung_task")
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        monitor = app.get("youtube_samsung_monitor")
        if monitor:
            await monitor.session.close()

    app.on_startup.append(start)
    app.on_cleanup.append(stop)
    return True
