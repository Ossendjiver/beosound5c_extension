from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from lib.mass_playback import (
    get_configured_mass_playback_mode,
    mass_prefers_local_playback,
    mass_runtime_playback_path,
    normalize_mass_playback_mode,
)
from sources.mass.service import MassSource


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _cfg(values):
    def getter(section, key=None, *, default=None):
        current = values.get(section, default if key is None else {})
        if key is None:
            return current if current is not None else default
        if isinstance(current, dict):
            return current.get(key, default)
        return default
    return getter


def _make_mass_source():
    with patch.object(MassSource, "_load_local_cache", return_value=False):
        return MassSource()


class TestMassPlaybackModeHelpers:
    def test_normalize_invalid_mode_defaults_to_auto(self):
        assert normalize_mass_playback_mode("banana") == "auto"

    def test_auto_prefers_local_for_powerlink_outputs(self):
        config_get = _cfg({
            "mass": {"playback_mode": "auto"},
            "volume": {"type": "powerlink"},
            "player": {"type": "local"},
        })

        assert get_configured_mass_playback_mode(config_get) == "auto"
        assert mass_prefers_local_playback(config_get) is True
        assert mass_runtime_playback_path(config_get) == "local"

    def test_auto_prefers_remote_for_hass_outputs(self):
        config_get = _cfg({
            "mass": {"playback_mode": "auto"},
            "volume": {"type": "hass"},
            "player": {"type": "local"},
        })

        assert mass_prefers_local_playback(config_get) is False
        assert mass_runtime_playback_path(config_get) == "remote"

    def test_forced_local_needs_local_player_backend(self):
        config_get = _cfg({
            "mass": {"playback_mode": "local"},
            "volume": {"type": "powerlink"},
            "player": {"type": "mass"},
        })

        assert mass_prefers_local_playback(config_get) is True
        assert mass_runtime_playback_path(config_get) == "remote"


class TestMassLocalPlayback:
    def test_extract_local_stream_candidates_scans_nested_mass_details(self):
        source = _make_mass_source()

        item = {
            "provider_mappings": [
                {
                    "details": (
                        '{"stream":{"content_url":"https://streams.example/station.aac"},'
                        '"artwork":"https://images.example/station.jpg"}'
                    )
                }
            ]
        }

        candidates = source._extract_local_stream_candidates(item)

        assert candidates == ["https://streams.example/station.aac"]

    def test_play_now_routes_resolved_stream_to_local_player(self):
        source = _make_mass_source()
        source._should_try_local_playback = MagicMock(return_value=True)
        source._forced_local_playback = MagicMock(return_value=False)
        source._local_player_ready = MagicMock(return_value=True)
        source._build_local_entries_for_request = MagicMock(return_value=[{
            "id": "track-1",
            "source_uri": "track://library/1",
            "title": "Track One",
            "artist": "Artist One",
            "album": "Album One",
            "artwork": "",
            "radio": False,
        }])
        source._resolve_local_entry = AsyncMock(return_value={
            "id": "track-1",
            "source_uri": "track://library/1",
            "stream_url": "http://streams.example/track-one.flac",
            "title": "Track One",
            "artist": "Artist One",
            "album": "Album One",
            "artwork": "http://images.example/track-one.jpg",
            "radio": False,
        })
        source._ensure_local_queue_monitor = MagicMock()
        source.player_play = AsyncMock(return_value=True)
        source.register = AsyncMock()
        source.post_media_update = AsyncMock()

        result = _run(source.handle_command("play_now", {
            "id": "track-1",
            "url": "track://library/1",
        }))

        source.player_play.assert_awaited_once_with(
            url="http://streams.example/track-one.flac",
            radio=False,
        )
        assert result["state"] == "playing"
        assert result["player_id"] == "local"
        assert source._local_queue_index == 0

    def test_forced_local_returns_error_without_local_backend(self):
        source = _make_mass_source()
        source._forced_local_playback = MagicMock(return_value=True)
        source._local_player_ready = MagicMock(return_value=False)

        result = _run(source.handle_command("play_now", {
            "id": "track-1",
            "url": "track://library/1",
        }))

        assert result["state"] == "error"
        assert result["reason"] == "local_player_unavailable"

    def test_get_queue_prefers_local_queue_when_active(self):
        source = _make_mass_source()
        source._local_queue_entries = [
            {
                "source_uri": "track://library/1",
                "title": "Track One",
                "artist": "Artist One",
                "album": "Album One",
                "artwork": "art-one",
            },
            {
                "source_uri": "track://library/2",
                "title": "Track Two",
                "artist": "Artist Two",
                "album": "Album Two",
                "artwork": "art-two",
            },
        ]
        source._local_queue_index = 1
        source._local_queue_active = True

        queue = _run(source.get_queue())

        assert queue["queue_id"] == "local"
        assert queue["current_index"] == 1
        assert [track["title"] for track in queue["tracks"]] == ["Track One", "Track Two"]

    def test_track_end_advances_local_queue(self):
        source = _make_mass_source()
        source._local_queue_index = 0
        source._local_queue_active = True
        source._play_local_queue_from = AsyncMock(return_value={"state": "playing", "uri": "track://library/2"})
        source.register = AsyncMock()

        payload = _run(source._advance_local_queue_after_track_end())

        source._play_local_queue_from.assert_awaited_once_with(1, step=1, reason="track_change")
        source.register.assert_not_called()
        assert payload["uri"] == "track://library/2"
        assert source._local_queue_active is True
        assert source._local_queue_last_player_state == "playing"

    def test_track_end_marks_source_available_when_queue_exhausted(self):
        source = _make_mass_source()
        source._local_queue_index = 1
        source._local_queue_active = True
        source._play_local_queue_from = AsyncMock(return_value=None)
        source.register = AsyncMock()

        payload = _run(source._advance_local_queue_after_track_end())

        source._play_local_queue_from.assert_awaited_once_with(2, step=1, reason="track_change")
        source.register.assert_awaited_once_with("available")
        assert payload is None
        assert source._local_queue_active is False
        assert source._local_queue_last_player_state == "stopped"
