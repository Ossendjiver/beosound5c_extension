#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

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
        asyncio.create_task(self._maintain_connection())

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
                    asyncio.create_task(self._listen())
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
                        self.state = player_data.get("state", "idle") or "idle"
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
        return ""

    async def get_capabilities(self) -> list:
        # Playback is source-managed, so this backend exposes transport only.
        return []


if __name__ == "__main__":
    service = MassPlayer()
    asyncio.run(service.run())
