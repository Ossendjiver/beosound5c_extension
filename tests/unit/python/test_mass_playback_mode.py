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

    def test_item_info_falls_back_to_album_notes_when_track_has_no_description(self):
        source = _make_mass_source()
        source._http_session = None
        track_item = {
            "uri": "track://library/1",
            "name": "Track One",
            "artist_str": "Artist One",
            "album": {
                "name": "Album One",
                "uri": "album://library/1",
            },
            "provider": "library",
            "duration": 182,
            "track_number": 4,
            "media_type": "track",
        }
        album_item = {
            "uri": "album://library/1",
            "name": "Album One",
            "metadata": {
                "description": "<p>A focused album note.</p>",
            },
        }
        source.send_command = AsyncMock(side_effect=[track_item, album_item])

        payload = _run(source._build_item_info_payload("track://library/1"))

        assert payload["state"] == "available"
        assert payload["title"] == "Track One"
        assert payload["artist"] == "Artist One"
        assert payload["album"] == "Album One"
        assert payload["description"] == "A focused album note."
        assert payload["description_label"] == "About the album"
        assert {"label": "Type", "value": "Track"} in payload["facts"]
        assert {"label": "Duration", "value": "3:02"} in payload["facts"]

    def test_favorite_add_uses_music_assistant_favorites_endpoint(self):
        source = _make_mass_source()
        source._send_command_response = AsyncMock(return_value={"ok": True})

        result = _run(source.handle_command("favorite_add", {
            "url": "track://library/42",
        }))

        source._send_command_response.assert_awaited_once_with(
            "music/favorites/add_item",
            item="track://library/42",
        )
        assert result["state"] == "favorited"

    def test_playlist_add_extracts_library_playlist_id_from_uri(self):
        source = _make_mass_source()
        source._send_command_response = AsyncMock(return_value={"ok": True})

        result = _run(source.handle_command("playlist_add", {
            "url": "track://library/42",
            "target_playlist_uri": "library://playlist/26",
        }))

        source._send_command_response.assert_awaited_once_with(
            "music/playlists/add_playlist_tracks",
            db_playlist_id="26",
            uris=["track://library/42"],
        )
        assert result["state"] == "playlist_added"
        assert result["playlist_id"] == "26"

    def test_local_play_next_inserts_after_current_queue_item(self):
        source = _make_mass_source()
        source._should_try_local_playback = MagicMock(return_value=True)
        source._forced_local_playback = MagicMock(return_value=False)
        source._local_player_ready = MagicMock(return_value=True)
        source._local_queue_entries = [
            {"id": "track-1", "source_uri": "track://library/1", "title": "One"},
            {"id": "track-2", "source_uri": "track://library/2", "title": "Two"},
        ]
        source._local_queue_index = 0
        source._local_queue_active = True
        source._build_local_entries_for_request = MagicMock(return_value=[
            {"id": "track-3", "source_uri": "track://library/3", "title": "Three"},
        ])

        result = _run(source.handle_command("play_next", {
            "url": "track://library/3",
        }))

        assert result["state"] == "queued"
        assert [entry["source_uri"] for entry in source._local_queue_entries] == [
            "track://library/1",
            "track://library/3",
            "track://library/2",
        ]
