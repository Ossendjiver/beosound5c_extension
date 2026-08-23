from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sources.mass.service import MassSource, VoiceSearchError


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _make_mass_source():
    with patch.object(MassSource, "_load_local_cache", return_value=False):
        source = MassSource()
    source._connected = True
    return source


class TestMassLiveSearch:
    def test_microphone_capture_only_accepts_the_b5c_ui_or_loopback(self):
        local_ui = SimpleNamespace(
            host="192.168.4.103:8783",
            remote="192.168.4.103",
            headers={"Origin": "http://192.168.4.103"},
        )
        hostile_page = SimpleNamespace(
            host="192.168.4.103:8783",
            remote="192.168.4.50",
            headers={"Origin": "https://example.net"},
        )
        loopback = SimpleNamespace(host="127.0.0.1:8783", remote="127.0.0.1", headers={})

        assert MassSource._voice_request_allowed(local_ui) is True
        assert MassSource._voice_request_allowed(loopback) is True
        assert MassSource._voice_request_allowed(hostile_page) is False

    def test_voice_command_cleanup_preserves_the_music_query(self):
        assert MassSource._clean_voice_query("Please play The National on Music Assistant") == "The National"
        assert MassSource._clean_voice_query("search for Blue Monday") == "Blue Monday"

    def test_search_calls_mass_globally_and_groups_playable_results(self):
        source = _make_mass_source()
        source.send_command = AsyncMock(return_value={
            "tracks": [
                {
                    "item_id": "11",
                    "provider": "spotify--account",
                    "name": "Blue Monday",
                    "uri": "track://spotify--account/11",
                    "artists": [{"name": "New Order"}],
                    "album": {"name": "Substance"},
                    "metadata": {"images": []},
                }
            ],
            "albums": [
                {
                    "item_id": "22",
                    "provider": "tidal--account",
                    "name": "Substance",
                    "uri": "album://tidal--account/22",
                    "artists": [{"name": "New Order"}],
                    "metadata": {"images": []},
                }
            ],
        })

        payload = _run(source._search_all_mass_providers("play Blue Monday", limit=6))

        source.send_command.assert_awaited_once_with(
            "music/search",
            search_query="Blue Monday",
            media_types=["artist", "album", "track", "playlist", "radio", "podcast", "audiobook"],
            limit=6,
            library_only=False,
        )
        assert payload["scope"] == "all_mass_providers"
        assert payload["total"] == 2
        assert [group["name"] for group in payload["groups"]] == ["Tracks", "Albums"]
        track = payload["groups"][0]["tracks"][0]
        assert track["url"] == "track://spotify--account/11"
        assert track["artist"] == "New Order"
        assert track["album"] == "Substance"
        assert track["live_search"] is True

    def test_search_rejects_when_mass_is_disconnected(self):
        source = _make_mass_source()
        source._connected = False

        try:
            _run(source._search_all_mass_providers("Blue Monday"))
        except VoiceSearchError as exc:
            assert exc.code == "mass_unavailable"
        else:
            raise AssertionError("expected disconnected MASS search to fail")

    def test_duplicate_provider_versions_are_collapsed_by_type_title_and_artist(self):
        source = _make_mass_source()
        source.send_command = AsyncMock(return_value={
            "tracks": [
                {
                    "item_id": "11",
                    "provider": "library",
                    "name": "Blue Monday",
                    "uri": "track://library/11",
                    "artists": [{"name": "New Order"}],
                    "metadata": {"images": []},
                },
                {
                    "item_id": "99",
                    "provider": "spotify--account",
                    "name": "Blue Monday",
                    "uri": "track://spotify--account/99",
                    "artists": [{"name": "New Order"}],
                    "metadata": {"images": []},
                },
            ]
        })

        payload = _run(source._search_all_mass_providers("Blue Monday"))

        assert payload["total"] == 1
        assert payload["groups"][0]["tracks"][0]["provider"] == "library"

    def test_active_playback_is_paused_for_capture_then_resumed(self):
        source = _make_mass_source()
        source._resolve_queue_candidates = AsyncMock(return_value=["queue-1"])
        source._resolve_player_candidates = AsyncMock(return_value=["player-1"])
        source._get_player_state = AsyncMock(return_value={"state": "playing"})
        source.send_command = AsyncMock(return_value=None)

        paused_player = _run(source._pause_for_voice_capture())
        _run(source._resume_after_voice_capture(paused_player))

        assert paused_player == "player-1"
        assert source.send_command.await_args_list[0].args == ("players/cmd/pause",)
        assert source.send_command.await_args_list[0].kwargs == {"player_id": "player-1"}
        assert source.send_command.await_args_list[1].args == ("players/cmd/play",)
        assert source.send_command.await_args_list[1].kwargs == {"player_id": "player-1"}

    def test_already_paused_playback_is_left_unchanged(self):
        source = _make_mass_source()
        source._resolve_queue_candidates = AsyncMock(return_value=["queue-1"])
        source._resolve_player_candidates = AsyncMock(return_value=["player-1"])
        source._get_player_state = AsyncMock(return_value={"state": "paused"})
        source.send_command = AsyncMock(return_value=None)

        paused_player = _run(source._pause_for_voice_capture())

        assert paused_player == ""
        source.send_command.assert_not_awaited()
