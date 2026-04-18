"""Tests for LydbroHandler — BeoRemote event routing.

LydbroHandler is a plain router-delegating class with ~8 distinct
event kinds (mode switch, volume up/down/mute, transport, source
select, playlist, radio preset, join/unjoin).  Every one of them
has been buggy at least once in the history — commits b39eec2,
a1cd753, fc5fe58, and others all touched lydbro.  The module had
zero direct tests before this file.

The strategy here is to use a faithful async mock of the router
so the tests exercise the real branching, HTTP URL strings, and
event-filtering logic — not the router internals.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from lib.lydbro import LydbroHandler


def _make_router():
    """A reasonable async mock of the EventRouter surface LydbroHandler touches."""
    r = MagicMock()
    r.volume = 30
    r._latest_action_ts = 0.0
    r._volume = AsyncMock()
    r._volume.is_on = AsyncMock(return_value=True)
    r._volume.power_on = AsyncMock()
    r._volume.power_off = AsyncMock()
    r._session = MagicMock()

    class _FakeResponse:
        status = 200
        async def __aenter__(self):
            return self
        async def __aexit__(self, *exc):
            return None

    # POST/GET return a context manager that yields a response.  We
    # track every call for assertions.
    r._session.post = MagicMock(return_value=_FakeResponse())
    r._session.get = MagicMock(return_value=_FakeResponse())

    r.registry = MagicMock()
    r.set_volume = AsyncMock()
    r.touch_activity = MagicMock()
    r._wake_screen = AsyncMock()
    r._screen_off = AsyncMock()
    r._player_stop = AsyncMock()
    r._forward_to_source = AsyncMock()

    # _spawn is called for fire-and-forget tasks.  Collect the (name,
    # coro) pair so tests can assert what was spawned.  We *close* the
    # coroutines so Python doesn't print "coroutine was never awaited"
    # warnings — tests inspect the names, not the runtime effects of
    # the spawned work.
    spawned: list = []
    def _spawn(coro, *, name=None):
        spawned.append((name, coro))
        coro.close()
        return None
    r._spawn = MagicMock(side_effect=_spawn)
    r._spawned = spawned
    return r


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def handler():
    router = _make_router()
    h = LydbroHandler(router)
    return h


# ── Mode switching ────────────────────────────────────────────────────


class TestModeSwitch:
    def test_music_mode_wakes_screen(self, handler):
        _run(handler.handle_event({"event": "Music"}))
        assert any(
            name == "lydbro_wake" for name, _ in handler.router._spawned
        )

    def test_tv_mode_goes_standby_after_music(self, handler):
        """TV triggers standby only when Music was pressed recently and nothing is playing."""
        _run(handler.handle_event({"event": "Music"}))
        handler.router._spawned.clear()
        _run(handler.handle_event({"event": "TV"}))
        names = {name for name, _ in handler.router._spawned}
        assert "lydbro_stop" in names
        assert "lydbro_power_off" in names
        assert "lydbro_screen_off" in names

    def test_tv_mode_ignored_without_music(self, handler):
        """TV without a recent Music press must not trigger standby."""
        _run(handler.handle_event({"event": "TV"}))
        names = {name for name, _ in handler.router._spawned}
        assert "lydbro_stop" not in names

    def test_tv_mode_ignored_while_playing(self, handler):
        """TV must not trigger standby when the player is active."""
        handler.router.media.state = {"state": "playing"}
        _run(handler.handle_event({"event": "Music"}))
        handler.router._spawned.clear()
        _run(handler.handle_event({"event": "TV"}))
        names = {name for name, _ in handler.router._spawned}
        assert "lydbro_stop" not in names

    def test_non_music_mode_ignored(self, handler):
        """Events with mode != 'MUSIC' and source != 'scene' must be ignored."""
        _run(handler.handle_event({
            "event": "Volume Up", "mode": "RADIO", "source": ""
        }))
        handler.router.set_volume.assert_not_called()

    def test_scene_source_ignored(self, handler):
        """Scene events stay in HA — handler should return without acting."""
        _run(handler.handle_event({
            "event": "Scene1", "mode": "MUSIC", "source": "scene"
        }))
        handler.router.set_volume.assert_not_called()
        handler.router._session.post.assert_not_called()


# ── Volume ────────────────────────────────────────────────────────────


class TestVolume:
    def test_volume_up_uses_step(self, handler):
        handler._volume_step = 3
        handler.router.volume = 30
        _run(handler.handle_event({
            "event": "Volume Up", "mode": "MUSIC", "source": ""
        }))
        handler.router.set_volume.assert_awaited_once_with(33)

    def test_volume_down_uses_step(self, handler):
        handler._volume_step = 2
        handler.router.volume = 30
        _run(handler.handle_event({
            "event": "Volume Down", "mode": "MUSIC", "source": ""
        }))
        handler.router.set_volume.assert_awaited_once_with(28)

    def test_volume_up_clamped_at_100(self, handler):
        handler._volume_step = 5
        handler.router.volume = 99
        _run(handler.handle_event({
            "event": "Volume Up", "mode": "MUSIC", "source": ""
        }))
        handler.router.set_volume.assert_awaited_once_with(100)

    def test_volume_down_clamped_at_0(self, handler):
        handler._volume_step = 5
        handler.router.volume = 2
        _run(handler.handle_event({
            "event": "Volume Down", "mode": "MUSIC", "source": ""
        }))
        handler.router.set_volume.assert_awaited_once_with(0)

    def test_mute_stores_and_zeros(self, handler):
        handler.router.volume = 42
        _run(handler.handle_event({
            "event": "Mute", "mode": "MUSIC", "source": ""
        }))
        handler.router.set_volume.assert_awaited_once_with(0)
        assert handler._pre_mute_vol == 42

    def test_unmute_restores_pre_mute(self, handler):
        """Second Mute press restores the saved volume."""
        handler._pre_mute_vol = 55
        handler.router.volume = 0
        _run(handler.handle_event({
            "event": "Mute", "mode": "MUSIC", "source": ""
        }))
        handler.router.set_volume.assert_awaited_once_with(55)


# ── Transport ─────────────────────────────────────────────────────────


class TestTransport:
    def test_play_pause_hits_player_toggle(self, handler):
        _run(handler.handle_event({
            "event": "Play/Pause", "mode": "MUSIC", "source": ""
        }))
        urls = [call.args[0] for call in handler.router._session.post.call_args_list]
        assert any("/player/toggle" in u for u in urls)

    def test_next_hits_player_next(self, handler):
        _run(handler.handle_event({
            "event": "Next", "mode": "MUSIC", "source": ""
        }))
        urls = [call.args[0] for call in handler.router._session.post.call_args_list]
        assert any(u.endswith("/player/next") for u in urls)

    def test_previous_hits_player_prev(self, handler):
        _run(handler.handle_event({
            "event": "Previous", "mode": "MUSIC", "source": ""
        }))
        urls = [call.args[0] for call in handler.router._session.post.call_args_list]
        assert any(u.endswith("/player/prev") for u in urls)


# ── Source selection ──────────────────────────────────────────────────


class TestSourceSelection:
    def test_spotify_activation_stamps_action_ts(self, handler):
        src = MagicMock()
        src.command_url = "http://localhost:8771/command"
        handler.router.registry.get = MagicMock(return_value=src)

        _run(handler.handle_event({
            "event": "Spotify", "mode": "MUSIC", "source": "music"
        }))
        # A fresh action_ts must be stamped so downstream sources can
        # reject stale media updates (see action-timestamp docs).
        assert handler.router._latest_action_ts > 0
        handler.router._forward_to_source.assert_awaited()
        fwd_args = handler.router._forward_to_source.call_args
        assert fwd_args.args[1]["action"] == "go"
        assert fwd_args.args[1]["action_ts"] == handler.router._latest_action_ts

    def test_unknown_source_is_noop(self, handler):
        handler.router.registry.get = MagicMock(return_value=None)
        _run(handler.handle_event({
            "event": "Nonsense", "mode": "MUSIC", "source": "music"
        }))
        handler.router._forward_to_source.assert_not_called()

    def test_favorites_maps_to_radio(self, handler):
        """The source_map aliases 'Favorites' -> 'radio'.  Regression
        guard: don't regress that alias."""
        src = MagicMock()
        src.command_url = "http://localhost:8779/command"
        def _get(name):
            assert name == "radio", f"Expected radio lookup, got {name!r}"
            return src
        handler.router.registry.get = MagicMock(side_effect=_get)

        _run(handler.handle_event({
            "event": "Favorites", "mode": "MUSIC", "source": "music"
        }))
        handler.router._forward_to_source.assert_awaited()


# ── Playlist + radio presets ──────────────────────────────────────────


class TestPresets:
    def test_spotify_playlist_hits_command_with_id(self, handler):
        handler._playlists = {"0": "spotify:playlist:abc123"}
        _run(handler.handle_event({
            "event": "preset", "mode": "MUSIC",
            "source": "sub_1", "id": 0,
        }))
        post_calls = handler.router._session.post.call_args_list
        assert post_calls, "expected a POST to the spotify command endpoint"
        url = post_calls[-1].args[0]
        body = post_calls[-1].kwargs["json"]
        assert url.endswith("/command")
        assert "8771" in url    # spotify source port
        assert body["command"] == "play_playlist"
        assert body["playlist_id"] == "abc123"

    def test_liked_songs_special_cased(self, handler):
        """``spotify:collection:tracks`` is not a playlist_id in the
        normal sense; it maps to ``liked-songs`` for the Spotify service."""
        handler._playlists = {"9": "spotify:collection:tracks"}
        _run(handler.handle_event({
            "event": "preset", "mode": "MUSIC",
            "source": "sub_1", "id": 9,
        }))
        body = handler.router._session.post.call_args_list[-1].kwargs["json"]
        assert body["playlist_id"] == "liked-songs"

    def test_unmapped_playlist_logs_and_returns(self, handler, caplog):
        import logging
        handler._playlists = {}
        with caplog.at_level(logging.WARNING, logger="beo-router"):
            _run(handler.handle_event({
                "event": "preset", "mode": "MUSIC",
                "source": "sub_1", "id": 7,
            }))
        assert any("no playlist mapped" in r.message for r in caplog.records)

    def test_radio_preset_extracts_station_name(self, handler):
        _run(handler.handle_event({
            "event": "preset/BBC Radio 3", "mode": "MUSIC",
            "source": "sub_2", "id": 1,
        }))
        body = handler.router._session.post.call_args_list[-1].kwargs["json"]
        assert body["command"] == "play_by_name"
        assert body["name"] == "BBC Radio 3"


# ── Join / unjoin ─────────────────────────────────────────────────────


class TestJoinUnjoin:
    def test_join_posts_name(self, handler):
        _run(handler.handle_event({
            "event": "kitchen", "mode": "MUSIC", "source": "join"
        }))
        call = handler.router._session.post.call_args_list[-1]
        assert call.args[0].endswith("/player/join")
        assert call.kwargs["json"] == {"name": "kitchen"}

    def test_unjoin_posts_to_unjoin_endpoint(self, handler):
        _run(handler.handle_event({
            "event": "UNJOIN", "mode": "MUSIC", "source": "join"
        }))
        call = handler.router._session.post.call_args_list[-1]
        assert call.args[0].endswith("/player/unjoin")
        # UNJOIN carries no body
        assert "json" not in call.kwargs or call.kwargs.get("json") is None
