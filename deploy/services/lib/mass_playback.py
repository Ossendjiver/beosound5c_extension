from __future__ import annotations

"""Helpers for choosing the MASS playback path.

MASS can either:
  * act like a remote control for a Music Assistant queue/player, or
  * hand directly playable streams to the BS5c local player.

The runtime choice depends on ``mass.playback_mode`` and whether the local
player backend is actually configured.
"""

from .config import cfg

VALID_MODES = {"auto", "remote", "local"}
LOCAL_VOLUME_TYPES = {"beolab5", "powerlink", "c4amp", "hdmi", "spdif", "rca"}


def normalize_mass_playback_mode(value) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in VALID_MODES else "auto"


def get_configured_mass_playback_mode(config_get=cfg) -> str:
    return normalize_mass_playback_mode(
        config_get("mass", "playback_mode", default="auto")
    )


def mass_prefers_local_playback(config_get=cfg) -> bool:
    mode = get_configured_mass_playback_mode(config_get)
    if mode == "local":
        return True
    if mode == "remote":
        return False
    volume_type = str(config_get("volume", "type", default="")).strip().lower()
    return volume_type in LOCAL_VOLUME_TYPES


def mass_local_backend_ready(config_get=cfg) -> bool:
    player_type = str(config_get("player", "type", default="")).strip().lower()
    return player_type == "local"


def mass_runtime_playback_path(config_get=cfg) -> str:
    if mass_prefers_local_playback(config_get) and mass_local_backend_ready(config_get):
        return "local"
    return "remote"
