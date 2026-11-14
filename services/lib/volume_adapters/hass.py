from __future__ import annotations

import asyncio
import json
import logging
import os

import aiohttp

from ..config import cfg
from .base import VolumeAdapter

logger = logging.getLogger("beo-router.volume.hass")
_MLGW_STEP_MULTIPLIER_DEFAULT = 2.0
_FRAME = "media_player.the_frame"


def _ha_api_base() -> str:
    base = (
        os.getenv("HA_URL")
        or cfg("home_assistant", "url", default="http://homeassistant.local:8123")
        or "http://homeassistant.local:8123"
    ).strip().rstrip("/")
    return base if base.endswith("/api") else f"{base}/api"


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return list(default)
    try:
        value = json.loads(raw)
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
    except json.JSONDecodeError:
        pass
    return [item.strip() for item in raw.split(",") if item.strip()]


def _env_entity_map(name: str) -> dict[str, int]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Invalid JSON in %s; disabling MLGW entity routing", name)
        return {}
    if not isinstance(value, dict):
        logger.warning("%s must be a JSON object; disabling MLGW entity routing", name)
        return {}
    resolved: dict[str, int] = {}
    for entity_id, mln in value.items():
        entity = str(entity_id).strip()
        if not entity:
            continue
        try:
            resolved[entity] = int(mln)
        except (TypeError, ValueError):
            logger.warning("Skipping invalid MLGW mapping %r=%r", entity_id, mln)
    return resolved


def _positive_float(name: str, raw: object, default: float, minimum: float = 1.0) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid %s=%r; using %.2f", name, raw, default)
        return default
    if value < minimum:
        logger.warning("%s=%r below minimum %.2f; using %.2f", name, raw, minimum, minimum)
        return minimum
    return value


class HassVolume(VolumeAdapter):
    """Bridge BS5c wheel updates to Home Assistant media-player commands."""

    def __init__(
        self,
        max_volume: int,
        session: aiohttp.ClientSession,
        default_vol: int = 30,
        *args,
        **kwargs,
    ):
        super().__init__(max_volume, debounce_ms=60)
        self._session = session
        self._last_reported_vol = float(default_vol)
        self._powered = True
        self._step_size = max(1, int(cfg("volume", "step", default=3)))
        self._mlgw_step_multiplier = _positive_float(
            "volume.mlgw_step_multiplier",
            cfg("volume", "mlgw_step_multiplier", default=_MLGW_STEP_MULTIPLIER_DEFAULT),
            _MLGW_STEP_MULTIPLIER_DEFAULT,
        )
        self._ha_api_base = _ha_api_base()
        self._ha_token = os.getenv("HA_TOKEN", "").strip()
        self._fallback_entity = os.getenv("HASS_FALLBACK_ENTITY", "").strip()
        self._volume_priority = _env_list("HASS_VOLUME_PRIORITY", [])
        self._showing_entity = cfg("showing", "entity_id", default="")
        targets = cfg("volume", "showing_child_targets", default={})
        self._showing_child_targets = targets if isinstance(targets, dict) else {}
        self._mlgw_host = os.getenv("MLGW_HOST", "").strip()
        self._mlgw_port = int(os.getenv("MLGW_PORT", "9000"))
        self._mlgw_user = os.getenv("MLGW_USER", "").strip()
        self._mlgw_pass = os.getenv("MLGW_PASSWORD", "").strip()
        self._entity_to_mln = _env_entity_map("MLGW_ENTITY_TO_MLN")
        self._mlgw_writer: asyncio.StreamWriter | None = None
        self._mlgw_task: asyncio.Task | None = None

        if self._entity_to_mln and self._mlgw_host and self._mlgw_user and self._mlgw_pass:
            self._mlgw_task = asyncio.create_task(self._connect_mlgw())
        else:
            logger.info("MLGW bridge disabled; HA media-player service mode only")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._ha_token}",
            "Content-Type": "application/json",
        }

    async def _connect_mlgw(self):
        while True:
            try:
                reader, writer = await asyncio.open_connection(self._mlgw_host, self._mlgw_port)
                creds = f"{self._mlgw_user}\x00{self._mlgw_pass}".encode("utf-8")
                header = bytes([0x01, 0x00, len(creds), 0x00])
                writer.write(header + creds)
                await writer.drain()

                self._mlgw_writer = writer
                logger.info("MLGW bridge connected to %s:%d", self._mlgw_host, self._mlgw_port)

                while True:
                    if not await reader.read(1024):
                        break
            except Exception as exc:
                logger.warning("MLGW bridge unavailable: %s", exc)
            finally:
                self._mlgw_writer = None
            await asyncio.sleep(5)

    async def _get_active_target(self) -> str | None:
        if not self._ha_token:
            return self._fallback_entity or (self._volume_priority[0] if self._volume_priority else None)
        # YouTube supplies playback state; the physical Frame owns volume.
        youtube_entity = "media_player.youtube_on_frame_65"
        try:
            async with self._session.get(
                f"{self._ha_api_base}/states/{youtube_entity}",
                headers=self._headers(),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("state") in {"playing", "paused", "buffering"}:
                        return _FRAME
        except Exception as exc:
            logger.debug("HA state lookup failed for %s: %s", youtube_entity, exc)

        if self._showing_entity and self._showing_child_targets:
            try:
                async with self._session.get(
                    f"{self._ha_api_base}/states/{self._showing_entity}",
                    headers=self._headers(), timeout=aiohttp.ClientTimeout(total=2),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        child = (data.get("attributes") or {}).get("active_child")
                        target = self._showing_child_targets.get(child) if isinstance(child, str) else None
                        if target and data.get("state") in {"playing", "buffering", "paused"} and (
                            target != _FRAME or await self._read_level(_FRAME) is not None
                        ):
                            return target
            except Exception as exc:
                logger.debug("HA Showing volume lookup failed: %s", exc)

        for entity in self._volume_priority:
            try:
                async with self._session.get(
                    f"{self._ha_api_base}/states/{entity}",
                    headers=self._headers(),
                ) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()
                    if data.get("state") == "playing":
                        return entity
            except Exception as exc:
                logger.debug("HA state lookup failed for %s: %s", entity, exc)
        # A configured physical output can still be playing when Showing is
        # temporarily reporting a different child. Preserve other active priorities.
        for target in dict.fromkeys(self._showing_child_targets.values()):
            if target in self._volume_priority or target == _FRAME:
                continue
            try:
                async with self._session.get(
                    f"{self._ha_api_base}/states/{target}", headers=self._headers(),
                    timeout=aiohttp.ClientTimeout(total=2),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("state") in {"playing", "buffering"}:
                            return target
            except Exception as exc:
                logger.debug("HA volume lookup failed for %s: %s", target, exc)
        if _FRAME in self._showing_child_targets.values() and await self._read_level(_FRAME) is not None:
            return _FRAME
        if self._fallback_entity:
            return self._fallback_entity
        return self._volume_priority[0] if self._volume_priority else None

    async def _send_mlgw_steps(self, target: str, steps: int, diff: float) -> bool:
        mln = self._entity_to_mln.get(target)
        if mln is None or self._mlgw_writer is None:
            return False
        cmd = 0x60 if diff > 0 else 0x64
        dest = 0x00 if "bv" in target or "bs" in target else 0x01
        packet = bytes([0x01, 0x01, 0x03, 0x00, mln, dest, cmd])
        try:
            for _ in range(steps):
                self._mlgw_writer.write(packet)
                await self._mlgw_writer.drain()
                await asyncio.sleep(0.1)
            logger.info("MLGW %s -> %s x%d", target, "UP" if diff > 0 else "DOWN", steps)
            return True
        except Exception as exc:
            logger.warning("MLGW volume send failed: %s", exc)
            self._mlgw_writer = None
            return False

    async def _send_ha_steps(self, target: str, steps: int, diff: float) -> bool:
        if not self._ha_token:
            logger.warning("HA_TOKEN missing; cannot control HASS volume")
            return False
        service = "volume_up" if diff > 0 else "volume_down"
        url = f"{self._ha_api_base}/services/media_player/{service}"
        try:
            for _ in range(steps):
                async with self._session.post(
                    url,
                    headers=self._headers(),
                    json={"entity_id": target},
                ) as resp:
                    if resp.status >= 400:
                        logger.warning("HA volume step returned %d for %s", resp.status, target)
                        return False
                await asyncio.sleep(0.05)
            logger.info("HA %s -> %s x%d", target, service, steps)
            return True
        except Exception as exc:
            logger.warning("HA volume send failed: %s", exc)
            return False

    async def _read_level(self, target: str) -> float | None:
        try:
            async with self._session.get(f"{self._ha_api_base}/states/{target}", headers=self._headers()) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                attrs = data.get("attributes") or {}
                level = attrs.get("volume_level")
                active = data.get("state") in {"playing", "paused", "buffering"}
                if target == _FRAME:
                    active = data.get("state") in {"on", "idle", "playing", "paused", "buffering"} and attrs.get("art_mode_status") != "on"
                if active and type(level) in {int, float} and 0 <= level <= 1:
                    return level
        except Exception as exc:
            logger.debug("HA volume feedback failed for %s: %s", target, exc)
        return None

    async def read_active_volume(self) -> float | None:
        if not self._ha_token or not self._showing_child_targets:
            return None
        target = await self._get_active_target()
        if target not in self._showing_child_targets.values():
            return None
        level = await self._read_level(target)
        return level * 100 if level is not None else None

    def sync_volume_baseline(self, volume: float) -> None:
        self._last_reported_vol = volume

    async def _set_measured_level(self, target: str, diff: float) -> bool:
        level = await self._read_level(target)
        if level is None:
            return False
        # Apply a wheel delta to the receiver's own level. External changes
        # and the router's UI scale must never become absolute volume jumps.
        step = max(0.01, abs(diff) / 100)
        limit = max(level, self._max_volume / 100)
        requested = max(0.0, min(limit, level + (step if diff > 0 else -step)))
        if requested == level:
            return True
        try:
            async with self._session.post(
                f"{self._ha_api_base}/services/media_player/volume_set", headers=self._headers(),
                json={"entity_id": target, "volume_level": round(requested, 4)},
            ) as resp:
                if resp.status >= 400:
                    logger.warning("HA volume_set returned %d for %s", resp.status, target)
                    return False
            logger.info("HA %s volume_set %.1f%% -> %.1f%%", target, level * 100, requested * 100)
            return True
        except Exception as exc:
            logger.warning("HA volume_set failed for %s: %s", target, exc)
            return False

    async def _apply_volume(self, volume: float) -> None:
        diff = volume - self._last_reported_vol
        if abs(diff) < 0.5:
            return

        target = await self._get_active_target()
        if not target:
            logger.warning("No HASS volume target configured")
            return

        steps = max(1, int(abs(diff) / self._step_size))
        mlgw_steps = max(steps, int(steps * self._mlgw_step_multiplier + 0.5))
        if target in self._showing_child_targets.values():
            if await self._set_measured_level(target, diff):
                self._last_reported_vol = volume
            return
        if await self._send_mlgw_steps(target, mlgw_steps, diff) or await self._send_ha_steps(target, steps, diff):
            self._last_reported_vol = volume

    async def get_volume(self) -> float:
        return self._last_reported_vol

    async def power_on(self) -> None:
        self._powered = True

    async def power_off(self) -> None:
        self._powered = False

    async def is_on(self) -> bool:
        return self._powered
