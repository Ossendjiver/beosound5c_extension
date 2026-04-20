#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import urllib.parse

import aiohttp
import websockets

# Robust pathing
current_dir = os.path.dirname(os.path.abspath(__file__))
while current_dir != "/" and "lib" not in os.listdir(current_dir):
    current_dir = os.path.dirname(current_dir)
if "lib" in os.listdir(current_dir):
    sys.path.insert(0, current_dir)

from lib.config import cfg
from lib.player_base import PlayerBase

logger = logging.getLogger("beo-player-mass")


def _mass_ws_url() -> str:
    configured = (os.getenv("MASS_WS_URL") or os.getenv("BS5C_MASS_WS_URL") or "").strip()
    if configured:
        return configured
    host = (os.getenv("PLAYER_IP") or cfg("player", "ip", default="") or "").strip()
    if host:
        if host.startswith("ws://") or host.startswith("wss://"):
            return host
        return f"ws://{host}:8095/ws"
    return "ws://localhost:8095/ws"


def _ha_api_base() -> str:
    base = (
        os.getenv("HA_URL")
        or cfg("home_assistant", "url", default="http://homeassistant.local:8123")
        or "http://homeassistant.local:8123"
    ).strip().rstrip("/")
    return base if base.endswith("/api") else f"{base}/api"


def _env_list(name: str) -> list[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return []
    try:
        value = json.loads(raw)
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
    except json.JSONDecodeError:
        pass
    return [item.strip() for item in raw.split(",") if item.strip()]


class MassPlayer(PlayerBase):
    id = "mass"
    port = 8781

    def __init__(self):
        super().__init__()
        self.websocket = None
        self._connected = False
        self.state = "idle"
        self._current_track_uri = ""
        self._current_track_id = ""
        self._mass_uri = _mass_ws_url()
        self._mass_token = os.getenv("MASS_TOKEN", "").strip()
        self._target_player_id = (
            os.getenv("MASS_PLAYER_ID")
            or os.getenv("BS5C_MASS_TARGET_PLAYER_ID")
            or ""
        ).strip()
        self._ha_api_base = _ha_api_base()
        self._ha_token = os.getenv("HA_TOKEN", "").strip()
        self._fallback_entity = os.getenv("HASS_FALLBACK_ENTITY", "").strip()
        self._volume_priority = _env_list("HASS_VOLUME_PRIORITY")

    async def on_start(self):
        logger.info("Starting MASS player backend")
        self._spawn(self._maintain_connection(), name="mass_player_connection")

    @property
    def _mass_http_base(self) -> str:
        return self._mass_uri.replace("/ws", "").replace("ws://", "http://").replace("wss://", "https://")

    def _get_img(self, item):
        if not isinstance(item, dict):
            return ""
        images = item.get("metadata", {}).get("images", [])
        if not isinstance(images, list) or not images:
            return ""
        best = next(
            (
                img for img in images
                if isinstance(img, dict) and img.get("type") in ("thumb", "landscape", "poster")
            ),
            images[0],
        )
        if not isinstance(best, dict):
            return ""
        path = str(best.get("path") or "").strip()
        provider = str(best.get("provider") or "library").strip() or "library"
        if not path:
            return ""
        clean = urllib.parse.unquote(path)
        if "tidal" in provider.lower() and not clean.endswith(".jpg"):
            clean = clean + "x750.jpg" if clean.endswith("750") else clean.rstrip("/") + "/750x750.jpg"
        encoded = (
            urllib.parse.quote(urllib.parse.quote(clean, safe=""), safe="")
            if clean.startswith("http")
            else urllib.parse.quote(clean, safe="")
        )
        return f"{self._mass_http_base}/imageproxy?path={encoded}&provider={provider}&size=256"

    @staticmethod
    def _extract_current_media(payload):
        current_media = payload.get("current_media")
        return current_media if isinstance(current_media, dict) else {}

    @staticmethod
    def _extract_media_item(payload):
        current_media = MassPlayer._extract_current_media(payload)
        media_item = current_media.get("media_item")
        return media_item if isinstance(media_item, dict) else {}

    @staticmethod
    def _extract_artist_name(item):
        artists = item.get("artists", [])
        if isinstance(artists, list) and artists and isinstance(artists[0], dict):
            return str(artists[0].get("name") or "").strip()
        return ""

    @staticmethod
    def _normalize_state(raw_state):
        state = str(raw_state or "").strip().lower()
        if state in {"playing", "buffering"}:
            return "playing"
        if state == "paused":
            return "paused"
        return "stopped"

    async def _build_media_data(self, payload):
        current_media = self._extract_current_media(payload)
        media_item = self._extract_media_item(payload)
        if not current_media and not media_item:
            return None

        title = ""
        for container in (current_media, media_item):
            for key in ("name", "title", "sort_name"):
                value = str(container.get(key) or "").strip()
                if value:
                    title = value
                    break
            if title:
                break

        artist = ""
        for container in (current_media, media_item):
            value = str(container.get("artist") or container.get("artist_str") or "").strip()
            if value:
                artist = value
                break
        if not artist and media_item:
            artist = self._extract_artist_name(media_item)

        album = ""
        for container in (current_media, media_item):
            value = container.get("album") or container.get("album_name")
            if isinstance(value, dict):
                value = value.get("name")
            value = str(value or "").strip()
            if value:
                album = value
                break

        uri = ""
        for container in (current_media, media_item):
            for key in ("uri", "media_item_uri", "url"):
                value = str(container.get(key) or "").strip()
                if value:
                    uri = value
                    break
            if uri:
                break

        image_url = ""
        for container in (current_media, media_item):
            value = str(container.get("image") or "").strip()
            if value:
                image_url = value
                break
            image_url = self._get_img(container)
            if image_url:
                break
        if not image_url:
            album_data = current_media.get("album") if isinstance(current_media.get("album"), dict) else {}
            if not album_data and isinstance(media_item.get("album"), dict):
                album_data = media_item.get("album")
            if isinstance(album_data, dict):
                image_url = str(album_data.get("image") or "").strip() or self._get_img(album_data)

        artwork = None
        if image_url:
            if image_url.startswith("/"):
                image_url = f"{self._mass_http_base}{image_url}"
            result = await self.fetch_artwork(image_url, session=self._http_session)
            artwork = f"data:image/jpeg;base64,{result['base64']}" if result else image_url

        return {
            "title": title or "—",
            "artist": artist or "—",
            "album": album or "—",
            "artwork": artwork,
            "state": self._normalize_state(payload.get("state") or self.state),
            "uri": uri,
        }

    async def _send_request(self, command: str, **kwargs):
        if not self.websocket:
            return None
        message_id = f"req-{os.urandom(2).hex()}"
        await self.websocket.send(
            json.dumps(
                {
                    "message_id": message_id,
                    "command": command,
                    "args": kwargs,
                }
            )
        )
        while True:
            payload = json.loads(await self.websocket.recv())
            if payload.get("message_id") == message_id:
                return payload.get("result")

    async def _discover_player_id(self) -> str:
        result = await self._send_request("players/all")
        player_items = result.get("items", []) if isinstance(result, dict) else (
            result if isinstance(result, list) else []
        )
        ranked = []
        for player in player_items:
            if not isinstance(player, dict):
                continue
            player_id = str(player.get("player_id") or player.get("id") or "").strip()
            if not player_id:
                continue
            state = str(player.get("state") or "").strip().lower()
            score = 0
            if state in {"playing", "paused", "buffering"}:
                score += 4
            if player.get("active_queue"):
                score += 2
            if player.get("active_source"):
                score += 1
            ranked.append((score, player_id))
        if not ranked:
            return ""
        ranked.sort(key=lambda item: (-item[0], item[1]))
        if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
            logger.warning(
                "MASS player auto-selected %s from %d candidates; set MASS_PLAYER_ID to pin a target",
                ranked[0][1],
                len(ranked),
            )
        return ranked[0][1]

    async def _maintain_connection(self):
        while True:
            if not self._mass_token:
                if not self._connected:
                    logger.warning(
                        "MASS player not fully configured; set MASS_TOKEN in secrets.env"
                    )
                await asyncio.sleep(15)
                continue
            if not self._connected:
                try:
                    self.websocket = await websockets.connect(self._mass_uri)
                    auth_result = await self._send_request(
                        "auth",
                        token=self._mass_token,
                    )
                    if not isinstance(auth_result, dict) or not auth_result.get("authenticated"):
                        raise RuntimeError("MASS authentication failed")
                    if not self._target_player_id:
                        self._target_player_id = await self._discover_player_id()
                    if not self._target_player_id:
                        logger.warning(
                            "MASS player could not discover a target player; set MASS_PLAYER_ID to enable transport/state sync"
                        )
                        await self.websocket.close()
                        self.websocket = None
                        await asyncio.sleep(15)
                        continue
                    await self.websocket.send(
                        json.dumps(
                            {
                                "message_id": "sub",
                                "command": "players/subscribe",
                                "args": {"player_id": self._target_player_id},
                            }
                        )
                    )
                    self._connected = True
                    logger.info("Connected to MASS player %s", self._target_player_id)
                    self._spawn(self._listen(), name="mass_player_listener")
                except Exception as exc:
                    logger.warning("MASS connection failed: %s", exc)
            await asyncio.sleep(5)

    async def _listen(self):
        try:
            async for message in self.websocket:
                data = json.loads(message)
                if data.get("event") == "player_updated":
                    player_data = data.get("data", {})
                    if str(player_data.get("player_id") or "") == self._target_player_id:
                        next_state = self._normalize_state(player_data.get("state") or "idle")
                        previous_state = self._current_playback_state
                        self.state = next_state
                        self._current_playback_state = next_state

                        media_data = await self._build_media_data(player_data)
                        if media_data:
                            self._current_track_uri = media_data.get("uri", "")
                            track_id = "|".join(
                                [
                                    self._current_track_uri,
                                    media_data.get("title", ""),
                                    media_data.get("artist", ""),
                                    media_data.get("album", ""),
                                ]
                            )
                            track_changed = track_id != self._current_track_id
                            state_changed = next_state != previous_state
                            if track_changed or state_changed:
                                self._current_track_id = track_id
                                self._cached_media_data = media_data
                                await self.broadcast_media_update(
                                    media_data,
                                    "track_change" if track_changed else "state_change",
                                )
                                if next_state == "playing" and previous_state != "playing":
                                    self._spawn(self.trigger_wake(), name="mass_player_wake")
                        elif next_state != previous_state and self._cached_media_data:
                            self._cached_media_data["state"] = next_state
                            await self.broadcast_media_update(self._cached_media_data, "state_change")
        except Exception as exc:
            logger.warning("MASS websocket listener stopped: %s", exc)
        finally:
            self._connected = False
            self.websocket = None

    async def _send_mass_cmd(self, command: str) -> bool:
        if not self._connected or not self.websocket or not self._target_player_id:
            return False
        try:
            await self.websocket.send(
                json.dumps(
                    {
                        "message_id": f"cmd-{os.urandom(2).hex()}",
                        "command": command,
                        "args": {"player_id": self._target_player_id},
                    }
                )
            )
            return True
        except Exception as exc:
            logger.warning("MASS command failed: %s", exc)
            self._connected = False
            self.websocket = None
            return False

    async def _pick_fallback_entity(self) -> str:
        if self._fallback_entity:
            return self._fallback_entity
        if not self._ha_token:
            return self._volume_priority[0] if self._volume_priority else ""

        headers = {
            "Authorization": f"Bearer {self._ha_token}",
            "Content-Type": "application/json",
        }
        async with aiohttp.ClientSession() as session:
            for entity in self._volume_priority:
                try:
                    async with session.get(
                        f"{self._ha_api_base}/states/{entity}",
                        headers=headers,
                    ) as resp:
                        if resp.status != 200:
                            continue
                        data = await resp.json()
                        if data.get("state") == "playing":
                            return entity
                except Exception as exc:
                    logger.debug("HA fallback lookup failed for %s: %s", entity, exc)
        return self._volume_priority[0] if self._volume_priority else ""

    async def _send_ha_fallback(self, service: str) -> bool:
        if not self._ha_token:
            logger.warning("HA_TOKEN missing; cannot send fallback %s", service)
            return False
        entity = await self._pick_fallback_entity()
        if not entity:
            logger.warning("No fallback HA entity configured for MASS transport")
            return False

        headers = {
            "Authorization": f"Bearer {self._ha_token}",
            "Content-Type": "application/json",
        }
        url = f"{self._ha_api_base}/services/media_player/{service}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    headers=headers,
                    json={"entity_id": entity},
                ) as resp:
                    ok = resp.status < 400
            if ok:
                logger.info("MASS fallback -> HA %s on %s", service, entity)
            else:
                logger.warning("HA fallback %s returned HTTP %d", service, resp.status)
            return ok
        except Exception as exc:
            logger.warning("Fallback transport failed: %s", exc)
            return False

    async def play(self, uri=None, url=None, track_uri=None, meta=None, enqueue=False, position=None) -> bool:
        if uri or url:
            logger.warning("MASS player does not support direct player.play payloads; playback is source-managed")
            return False
        return await self.resume()

    async def pause(self) -> bool:
        return await self._send_mass_cmd("players/cmd/pause") or await self._send_ha_fallback("media_pause")

    async def resume(self) -> bool:
        return await self._send_mass_cmd("players/cmd/play") or await self._send_ha_fallback("media_play")

    async def next_track(self) -> bool:
        return await self._send_mass_cmd("players/cmd/next") or await self._send_ha_fallback("media_next_track")

    async def prev_track(self) -> bool:
        return await self._send_mass_cmd("players/cmd/previous") or await self._send_ha_fallback("media_previous_track")

    async def stop(self) -> bool:
        return await self._send_mass_cmd("players/cmd/stop") or await self._send_ha_fallback("media_stop")

    async def get_state(self) -> str:
        return self.state

    async def get_track_uri(self) -> str:
        return self._current_track_uri or ""

    async def get_capabilities(self) -> list:
        # Playback is source-managed, so this backend exposes transport only.
        return []


if __name__ == "__main__":
    service = MassPlayer()
    asyncio.run(service.run())
