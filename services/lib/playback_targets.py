from __future__ import annotations

from .config import cfg


DEFAULT_AUDIO_TARGETS = [
    {"id": "08a2eca2-247c-96fe-7998-7baddf01b2b1", "name": "Cuisine"},
    {"id": "64ad9554-d5e6-116c-8b0b-069c1f0b7885", "name": "Bedroom Mini"},
    {"id": "up50411c87e1c0", "name": "Link"},
]


def _as_list(value) -> list:
    return value if isinstance(value, list) else []


_PRIVATE_TARGET_KEYS = {
    "url",
    "rpc_url",
    "jsonrpc_url",
    "base_url",
    "host",
    "hostname",
    "ip",
    "port",
    "scheme",
    "user",
    "username",
    "login",
    "password",
    "pass",
    "token",
    "headers",
    "tls_verify",
    "verify_ssl",
    "ssl",
    "player_id",
    "playerid",
    "local",
}


def _normalize_targets(value, *, include_private: bool = False) -> list[dict]:
    targets = []
    for item in _as_list(value):
        if not isinstance(item, dict):
            continue
        target_id = str(item.get("id") or item.get("player_id") or item.get("target") or "").strip()
        if not target_id:
            continue
        name = str(item.get("name") or item.get("label") or target_id).strip()
        target = {"id": target_id, "name": name}
        if include_private:
            for key in _PRIVATE_TARGET_KEYS:
                if key in item:
                    target[key] = item[key]
        targets.append(target)
    return targets


def get_audio_targets() -> list[dict]:
    playback_cfg = cfg("playback", default={}) or {}
    mass_cfg = cfg("mass", default={}) or {}
    return (
        _normalize_targets(mass_cfg.get("transfer_targets"))
        or _normalize_targets(playback_cfg.get("audio_targets"))
        or list(DEFAULT_AUDIO_TARGETS)
    )


def get_video_targets(*, include_private: bool = False) -> list[dict]:
    playback_cfg = cfg("playback", default={}) or {}
    kodi_cfg = cfg("kodi", default={}) or {}
    return (
        _normalize_targets(kodi_cfg.get("transfer_targets"), include_private=include_private)
        or _normalize_targets(playback_cfg.get("video_targets"), include_private=include_private)
        or []
    )


def _selected_target_id(targets: list[dict], configured: str = "") -> str:
    configured = str(configured or "").strip()
    if configured and any(target["id"] == configured for target in targets):
        return configured
    return targets[0]["id"] if targets else ""


def default_playback_state() -> dict:
    playback_cfg = cfg("playback", default={}) or {}
    audio_targets = get_audio_targets()
    video_targets = get_video_targets()
    return {
        "audio_targets": audio_targets,
        "video_targets": video_targets,
        "audio_target_id": _selected_target_id(
            audio_targets,
            playback_cfg.get("audio_target_id")
            or playback_cfg.get("audio_target")
            or cfg("mass", "player_id", default="")
            or cfg("mass", "target_player_id", default=""),
        ),
        "video_target_id": _selected_target_id(
            video_targets,
            playback_cfg.get("video_target_id") or playback_cfg.get("video_target"),
        ),
        "music_video_enabled": bool(playback_cfg.get("music_video_enabled", True)),
    }
