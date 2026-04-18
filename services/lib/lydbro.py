"""Lydbro One BeoRemote handler for BeoSound 5c router.

Handles MQTT events from the Lydbro bridge: mode switching, volume,
transport controls, source selection, playlist/radio preset playback,
and Sonos join/unjoin.
"""

import asyncio
import logging
import time

import aiohttp

from .config import cfg
from .endpoints import (
    PLAYER_JOIN,
    PLAYER_NEXT,
    PLAYER_PREV,
    PLAYER_TOGGLE,
    PLAYER_UNJOIN,
    RADIO_COMMAND,
    SPOTIFY_COMMAND,
)

logger = logging.getLogger("beo-router")


class LydbroHandler:
    """Handles Lydbro One MQTT events, delegating to the router for state changes."""

    def __init__(self, router):
        self.router = router
        self._playlists: dict = {}
        self._pre_mute_vol: float = 30.0
        self._volume_step: int = 1
        self._last_music_mode_ts: float = 0.0  # timestamp of last bare "Music" button press

    def setup(self):
        """Subscribe to Lydbro MQTT topic if configured."""
        topic = cfg("lydbro", "topic", default=None)
        if not topic:
            return
        self._playlists = cfg("lydbro", "playlists", default={})
        self._volume_step = int(cfg("lydbro", "volume_step", default=1))
        self.router.transport.add_subscription(topic, self.handle_event)
        logger.info("Lydbro One remote: subscribing to %s (%d playlists)",
                     topic, len(self._playlists))

    async def handle_event(self, data: dict):
        """Handle an MQTT event from the Lydbro One BeoRemote bridge."""
        event = data.get("event", "")
        mode = data.get("mode", "")
        source = data.get("source", "")
        event_id = data.get("id", -1)
        r = self.router

        # Mode buttons (no source field)
        if event == "Music" and not source:
            logger.info("Lydbro: Music mode — waking screen")
            self._last_music_mode_ts = time.monotonic()
            r._spawn(r._wake_screen(), name="lydbro_wake")
            return

        if event == "TV" and not source:
            # Only trigger standby if the player is not active AND the Music button
            # was pressed recently (≤5s ago) — the "MUSIC then TV" gesture signals
            # intentional hand-off from BS5c to TV.  Any other TV press (e.g. while
            # music is playing, or as a standalone TV control action) is ignored so
            # the BS5c keeps playing.
            is_playing = (r.media.state or {}).get("state") == "playing"
            music_recently = (time.monotonic() - self._last_music_mode_ts) <= 5.0
            if not is_playing and music_recently:
                logger.info("Lydbro: TV mode after Music — standby")
                r._spawn(r._player_stop(), name="lydbro_stop")
                if r._volume:
                    r._spawn(r._volume.power_off(), name="lydbro_power_off")
                r._spawn(r._screen_off(), name="lydbro_screen_off")
            else:
                logger.info("Lydbro: TV press ignored (playing=%s, music_recent=%s)",
                            is_playing, music_recently)
            return

        # Only MUSIC mode (scenes/TV stay in HA)
        if mode != "MUSIC" and source not in ("scene",):
            return
        if source == "scene":
            return

        r.touch_activity()
        logger.info("Lydbro: %s (source=%s, id=%s)", event, source or "-", event_id)

        # Volume
        if event == "Volume Up":
            await r.set_volume(min(100, r.volume + self._volume_step))
            return
        if event == "Volume Down":
            await r.set_volume(max(0, r.volume - self._volume_step))
            return
        if event == "Mute":
            if r.volume > 0:
                self._pre_mute_vol = r.volume
                await r.set_volume(0)
            else:
                await r.set_volume(self._pre_mute_vol)
            return

        # Transport
        if event in ("Play/Pause", "Play", "Pause"):
            r._spawn(r._wake_screen(), name="lydbro_wake")
            try:
                await r._session.post(
                    PLAYER_TOGGLE,
                    timeout=aiohttp.ClientTimeout(total=2))
            except Exception as e:
                logger.warning("Lydbro play/pause failed: %s", e)
            return

        if event == "Next":
            try:
                await r._session.post(
                    PLAYER_NEXT,
                    timeout=aiohttp.ClientTimeout(total=2))
            except Exception as e:
                logger.warning("Lydbro next failed: %s", e)
            return

        if event == "Previous":
            try:
                await r._session.post(
                    PLAYER_PREV,
                    timeout=aiohttp.ClientTimeout(total=2))
            except Exception as e:
                logger.warning("Lydbro prev failed: %s", e)
            return

        # Power off
        if event == "Power":
            r._spawn(r._player_stop(), name="lydbro_stop")
            if r._volume:
                r._spawn(r._volume.power_off(), name="lydbro_power_off")
            r._spawn(r._screen_off(), name="lydbro_screen_off")
            return

        # Source selection
        if source == "music":
            r._spawn(r._wake_screen(), name="lydbro_wake")
            if r._volume:
                r._spawn(r._volume.power_on(), name="lydbro_power_on")
            source_map = {"Spotify": "spotify", "Radio": "radio",
                          "TIDAL": "tidal", "CD": "cd", "USB": "usb",
                          "Plex": "plex", "Favorites": "radio"}
            source_id = source_map.get(event, event.lower())
            src = r.registry.get(source_id)
            if src and src.command_url:
                action_ts = time.monotonic()
                r._latest_action_ts = action_ts
                logger.info("Lydbro: activating source %s", source_id)
                await r._forward_to_source(src, {
                    "action": "go", "action_ts": action_ts})
            return

        # Spotify playlists
        if source in ("sub_1", "Spotify") and event_id >= 0:
            playlist_uri = self._playlists.get(str(event_id))
            if playlist_uri:
                await self._play_spotify(playlist_uri)
            else:
                logger.warning("Lydbro: no playlist mapped for id %d", event_id)
            return

        # Radio presets
        if source in ("sub_2", "Radio") and event_id >= 0:
            station_name = event.split("/", 1)[1] if "/" in event else event
            r._spawn(r._wake_screen(), name="lydbro_wake")
            if r._volume:
                r._spawn(r._volume.power_on(), name="lydbro_power_on")
            try:
                async with r._session.post(
                    RADIO_COMMAND,
                    json={"command": "play_by_name", "name": station_name},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    logger.info("Lydbro radio play '%s': HTTP %d",
                                station_name, resp.status)
            except Exception as e:
                logger.warning("Lydbro radio play failed: %s", e)
            return

        # Join/Unjoin
        if source == "join":
            try:
                if event == "UNJOIN":
                    await r._session.post(
                        PLAYER_UNJOIN,
                        timeout=aiohttp.ClientTimeout(total=5))
                else:
                    await r._session.post(
                        PLAYER_JOIN,
                        json={"name": event},
                        timeout=aiohttp.ClientTimeout(total=5))
            except Exception as e:
                logger.warning("Lydbro join/unjoin failed: %s", e)
            return

    async def _play_spotify(self, spotify_uri: str):
        """Play a Spotify URI via the spotify source service."""
        r = self.router
        r._spawn(r._wake_screen(), name="lydbro_wake")
        if r._volume:
            try:
                if not await r._volume.is_on():
                    await r._volume.power_on()
            except Exception:
                pass
        if spotify_uri == "spotify:collection:tracks":
            playlist_id = "liked-songs"
        else:
            playlist_id = spotify_uri.split(":")[-1]
        try:
            async with r._session.post(
                SPOTIFY_COMMAND,
                json={"command": "play_playlist", "playlist_id": playlist_id,
                      "shuffle": True},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                logger.info("Lydbro spotify play: HTTP %d (%s)",
                            resp.status, playlist_id)
        except Exception as e:
            logger.warning("Lydbro spotify play failed: %s", e)
