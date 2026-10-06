"""
PowerLink volume adapter — controls B&O speakers via masterlink.py HTTP API.

masterlink.py owns the PC2 USB device and exposes mixer control on a local
HTTP port (default 8768).  This adapter is a thin HTTP client.

Chain: router.py -> PowerLinkVolume -> HTTP -> masterlink.py -> PC2 USB -> speakers

Volume is an absolute value (0-max).  masterlink.py uses 0xE3 to set initial
volume at power-on and 0xEB steps for live changes.  The device echoes back
confirmed volume via USB feedback messages.

Tone (bass/treble/balance/loudness) goes through masterlink.py's /mixer/tone
endpoint, which applies it via ALSA (amixer) and also pokes the PC2 via 0xE3
as a best-effort (PC2 firmware may ignore 0xE3 after power-on).
"""

from __future__ import annotations

import logging

import aiohttp

from .base import VolumeAdapter

logger = logging.getLogger("beo-router.volume.powerlink")


class PowerLinkVolume(VolumeAdapter):
    """Volume control via masterlink.py mixer HTTP API."""

    def __init__(self, host: str, max_volume: int, default_volume: int,
                 session: aiohttp.ClientSession, port: int = 8768):
        super().__init__(max_volume, debounce_ms=200)
        self._host = host
        self._port = port
        self._default_volume = default_volume
        self._session = session
        self._base = f"http://{host}:{port}"
        self._cached_on: bool = False

    async def _apply_volume(self, volume: float) -> None:
        volume = min(int(volume), self._max_volume)
        try:
            async with self._session.post(
                f"{self._base}/mixer/volume",
                json={"volume": volume},
                timeout=aiohttp.ClientTimeout(total=2.0),
            ) as resp:
                data = await resp.json()
                confirmed = data.get("volume_confirmed", volume)
                logger.info("-> PowerLink volume: %d (confirmed %d, HTTP %d)",
                            volume, confirmed, resp.status)
        except Exception as e:
            logger.warning("PowerLink mixer unreachable: %s", e)

    async def get_volume(self) -> float:
        try:
            async with self._session.get(
                f"{self._base}/mixer/status",
                timeout=aiohttp.ClientTimeout(total=2.0),
            ) as resp:
                data = await resp.json()
                vol = float(data.get("volume_confirmed", data.get("volume", 0)))
                logger.info("PowerLink volume read: %d", vol)
                return vol
        except Exception as e:
            logger.warning("Could not read PowerLink volume: %s", e)
            return None

    def is_on_cached(self) -> bool | None:
        return self._cached_on

    async def power_on(self) -> None:
        try:
            async with self._session.post(
                f"{self._base}/mixer/power",
                json={"on": True, "volume": self._default_volume},
                timeout=aiohttp.ClientTimeout(total=3.0),
            ) as resp:
                self._cached_on = True
                logger.info("PowerLink power on (vol %d): HTTP %d",
                            self._default_volume, resp.status)
        except Exception as e:
            logger.warning("Could not power on PowerLink: %s", e)

    async def power_off(self) -> None:
        try:
            async with self._session.post(
                f"{self._base}/mixer/power",
                json={"on": False},
                timeout=aiohttp.ClientTimeout(total=2.0),
            ) as resp:
                self._cached_on = False
                logger.info("PowerLink power off: HTTP %d", resp.status)
        except Exception as e:
            logger.warning("Could not power off PowerLink: %s", e)

    async def is_on(self) -> bool:
        try:
            async with self._session.get(
                f"{self._base}/mixer/status",
                timeout=aiohttp.ClientTimeout(total=1.0),
            ) as resp:
                data = await resp.json()
                on = data.get("speakers_on", False) is True
                self._cached_on = on
                return on
        except Exception as e:
            logger.warning("Could not check PowerLink state: %s", e)
            return False

    # --- Tone (bass / treble / balance / loudness) ---

    async def get_tone(self) -> dict | None:
        try:
            async with self._session.get(
                f"{self._base}/mixer/tone",
                timeout=aiohttp.ClientTimeout(total=2.0),
            ) as resp:
                return await resp.json()
        except Exception as e:
            logger.warning("Could not read PowerLink tone: %s", e)
            return None

    async def set_tone(self, **kwargs) -> dict | None:
        """Set one or more of: bass, treble, balance (ints), loudness (bool).
        Anything omitted is left untouched."""
        body = {k: v for k, v in kwargs.items()
                if k in ("bass", "treble", "balance", "loudness") and v is not None}
        if not body:
            return None
        try:
            async with self._session.post(
                f"{self._base}/mixer/tone",
                json=body,
                timeout=aiohttp.ClientTimeout(total=3.0),
            ) as resp:
                data = await resp.json()
                logger.info("-> PowerLink tone: %s (HTTP %d)", body, resp.status)
                return data
        except Exception as e:
            logger.warning("PowerLink tone set failed: %s", e)
            return None
