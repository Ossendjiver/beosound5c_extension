"""End-to-end format test: Lydbro One BeoRemote → RadioService.

The Lydbro bridge publishes an MQTT event when the user picks a radio
preset. LydbroHandler turns that into an HTTP POST to
http://radio:8779/command. RadioService.handle_command then resolves
the station name. If either side gets the format wrong, the user
presses RADIO 1 on their BeoRemote and nothing plays.

This test wires both together with no live network: it intercepts the
HTTP call Lydbro makes, replays the JSON body into RadioService's
command handler, and asserts the right station starts.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from lib.lydbro import LydbroHandler
from sources.radio.service import RadioService


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _station(uuid, name, **extra):
    s = {
        "stationuuid": uuid, "name": name,
        "url_resolved": f"http://example.com/{uuid}.mp3",
        "favicon": "", "country": "", "tags": "", "codec": "MP3",
        "bitrate": 128, "votes": 0,
    }
    s.update(extra)
    return s


@pytest.fixture
def radio(mock_config, monkeypatch):
    """A RadioService stub mirroring the production handler surface,
    but with persistence and network calls neutered."""
    mock_config({})
    monkeypatch.setattr(RadioService, "_save_favourites", lambda self: None)
    monkeypatch.setattr(RadioService, "_save_last_station", lambda self: None)
    monkeypatch.setattr(RadioService, "_load_favourites", lambda self: None)
    monkeypatch.setattr(RadioService, "_load_last_station", lambda self: None)
    s = RadioService()
    s._favourites = [
        _station("uuid-p1", "Sveriges Radio - P1", short_name="SR P1"),
        _station("uuid-p3", "Sveriges Radio - P3", short_name="SR P3"),
        _station("uuid-bbc", "BBC Radio 3"),
    ]
    s.post_media_update = AsyncMock()
    s.register = AsyncMock()
    s.player_play = AsyncMock()
    s.player_pause = AsyncMock()
    s.player_resume = AsyncMock()
    s.player_stop = AsyncMock()
    return s


@pytest.fixture
def lydbro_handler(radio):
    """LydbroHandler whose _session.post is wired through to the
    RadioService command handler — round-trips JSON like the real HTTP."""
    router = MagicMock()
    router.volume = 30
    router._latest_action_ts = 0.0
    router._volume = AsyncMock()
    router._volume.is_on = AsyncMock(return_value=True)
    router._volume.power_on = AsyncMock()
    router._volume.power_off = AsyncMock()
    router.set_volume = AsyncMock()
    router.touch_activity = MagicMock()
    router._wake_screen = AsyncMock()
    router._screen_off = AsyncMock()
    router._player_stop = AsyncMock()
    router._forward_to_source = AsyncMock()

    captured: list = []
    import json as json_lib

    class _Resp:
        """Async-context-manager response that, on __aenter__, dispatches
        the captured POST body to RadioService.handle_command — runs in
        the SAME event loop as the test's outer _run, so we don't crash
        with a nested-loop error."""
        def __init__(self, url, body):
            self._url = url
            self._body = body
            self._result = {"status": "ok"}
            self.status = 200
        async def __aenter__(self):
            if "8779" in self._url and "/command" in self._url:
                cmd = self._body.get("command", "")
                data = {k: v for k, v in self._body.items() if k != "command"}
                self._result = await radio.handle_command(cmd, data)
            return self
        async def __aexit__(self, *exc):
            return None
        async def json(self):
            return self._result

    def _post(url, json=None, timeout=None):
        # Re-encode → decode like a real HTTP boundary would, so we
        # catch JSON-incompatible types early.
        body = json_lib.loads(json_lib.dumps(json or {}))
        captured.append({"url": url, "json": body})
        return _Resp(url, body)
    router._session = MagicMock()
    router._session.post = MagicMock(side_effect=_post)
    router._spawn = MagicMock(side_effect=lambda coro, *, name=None: coro.close())

    h = LydbroHandler(router)
    h._captured = captured
    h._radio = radio
    return h


# ── Format compatibility: Lydbro's body shape matches RadioService's parser ──


class TestLydbroToRadioFormat:
    def test_radio_preset_starts_correct_station_via_short_name(self, lydbro_handler):
        """Lydbro publishes RADIO/preset 1 with event='preset/SR P1'.
        The handler must POST {command: 'play_by_name', name: 'SR P1'} to
        the radio service. RadioService must then match short_name to
        favourites[0] and start it."""
        _run(lydbro_handler.handle_event({
            "event": "preset/SR P1",
            "mode": "MUSIC",
            "source": "sub_2",
            "id": 1,
        }))

        # Inspect what Lydbro sent
        radio_calls = [c for c in lydbro_handler._captured if "8779" in c["url"]]
        assert len(radio_calls) == 1, f"Expected one radio POST, got {radio_calls}"
        body = radio_calls[0]["json"]
        assert body["command"] == "play_by_name"
        assert body["name"] == "SR P1"

        # And confirm it actually played the right station end-to-end
        lydbro_handler._radio.player_play.assert_awaited_once()
        url = lydbro_handler._radio.player_play.await_args.kwargs["url"]
        assert "uuid-p1" in url

    def test_radio_preset_with_full_name_works_via_substring(self, lydbro_handler):
        """If the BeoRemote menu sends the full station name (no short
        alias), substring matching should still find it. Regression
        guard: the handler strips event prefix before sending."""
        _run(lydbro_handler.handle_event({
            "event": "preset/BBC Radio 3",
            "mode": "MUSIC",
            "source": "sub_2",
            "id": 2,
        }))
        body = lydbro_handler._captured[-1]["json"]
        assert body == {"command": "play_by_name", "name": "BBC Radio 3"}
        lydbro_handler._radio.player_play.assert_awaited_once()

    def test_radio_preset_unknown_returns_error_but_doesnt_crash(
            self, lydbro_handler, monkeypatch):
        """Lydbro should log and move on when the station isn't found —
        no exception bubbling up that would unsubscribe MQTT."""
        monkeypatch.setattr(
            lydbro_handler._radio, "_fetch_curated", AsyncMock(return_value=[]))
        monkeypatch.setattr(
            lydbro_handler._radio, "_api_get", AsyncMock(return_value=[]))
        _run(lydbro_handler.handle_event({
            "event": "preset/DoesNotExist",
            "mode": "MUSIC",
            "source": "sub_2",
            "id": 99,
        }))
        body = lydbro_handler._captured[-1]["json"]
        assert body["command"] == "play_by_name"
        assert body["name"] == "DoesNotExist"
        lydbro_handler._radio.player_play.assert_not_called()


# ── source map: Lydbro's "Favorites" menu must reach the radio service ──


class TestLydbroSourceMap:
    def test_favorites_button_activates_radio_source(self, lydbro_handler):
        """The Favorites button on the BeoRemote routes to the radio
        source. Without this alias, pressing FAVORITES does nothing."""
        # Set up the registry to return a mock radio source when looked up
        radio_src = MagicMock()
        radio_src.command_url = "http://localhost:8779/command"
        lydbro_handler.router.registry = MagicMock()
        looked_up = []

        def _get(name):
            looked_up.append(name)
            return radio_src
        lydbro_handler.router.registry.get = MagicMock(side_effect=_get)

        _run(lydbro_handler.handle_event({
            "event": "Favorites",
            "mode": "MUSIC",
            "source": "music",
        }))
        assert "radio" in looked_up, \
            f"Favorites must look up the radio source; saw {looked_up}"
        lydbro_handler.router._forward_to_source.assert_awaited()
