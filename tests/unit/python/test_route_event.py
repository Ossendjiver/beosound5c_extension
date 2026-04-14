"""Tests for EventRouter.route_event — the central event dispatcher.

route_event has ~10 branches that decide where every button press,
wheel event, and volume change goes.  Every one has been the site of
a real bug at least once in the history:

  * 9ef9492 — source button on already-active source must wake the screen
  * ff33b84 — speakers powering on during playlist auto-advance
  * b39eec2 — Lydbro mode buttons handled correctly
  * 4ea1e18 / aab93cb — source button → activate forwarding

Strategy: real EventRouter instance, mock the transport layer and HTTP
session, fire events at route_event and assert the outcome.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.source_registry import Source
from router import EventRouter


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _make_router():
    """EventRouter with just enough external dependencies stubbed out
    to exercise route_event branches."""
    r = EventRouter()

    # aiohttp session — async context manager with a dummy response
    class _Resp:
        status = 200
        async def __aenter__(self): return self
        async def __aexit__(self, *e): return None
        async def json(self): return {}
    r._session = MagicMock()
    r._session.post = MagicMock(return_value=_Resp())
    r._session.get = MagicMock(return_value=_Resp())

    # transport
    r.transport = MagicMock()
    r.transport.send_event = AsyncMock()

    # media broadcast surface
    r.media = MagicMock()
    r.media.state = None
    r.media.broadcast = AsyncMock()
    r.media.push_idle = AsyncMock()

    # volume adapter — None for most tests (no hardware)
    r._volume = None
    r._volume_step = 5
    r._balance_step = 1
    r.volume = 30
    r.balance = 0
    r._handle_audio = True
    r._handle_video = False
    r._latest_action_ts = 0.0

    # capture spawned tasks (name → coro) and close them so no warnings
    r._spawned_names: list = []
    orig_spawn = r._spawn

    def _spawn(coro, *, name=None):
        r._spawned_names.append(name)
        coro.close()
        return None
    r._spawn = _spawn

    # helpers
    r._forward_to_source = AsyncMock()
    r._wake_screen = AsyncMock()
    r._screen_off = AsyncMock()
    r._player_stop = AsyncMock()
    r._player_announce = AsyncMock()
    r.set_volume = AsyncMock()
    r._source_buttons = {}

    return r


def _make_source(registry, id_, *, handles, state, player="local",
                 command_url="http://localhost:8771/command"):
    src = registry.create_from_config(id_, set(handles))
    src._state = state
    src.name = id_.title()
    src.command_url = command_url
    src.player = player
    return src


# ── Active source forwarding ────────────────────────────────────────


class TestActiveSourceForwarding:
    def test_active_source_action_forwarded(self):
        r = _make_router()
        spotify = _make_source(r.registry, "spotify",
                                handles={"play", "pause", "next"},
                                state="playing")
        r.registry._active_id = "spotify"
        _run(r.route_event({"action": "next", "device_type": "Audio"}))
        r._forward_to_source.assert_awaited_once()
        forwarded = r._forward_to_source.call_args.args
        assert forwarded[0].id == "spotify"
        assert forwarded[1]["action"] == "next"

    def test_action_not_in_active_handles_falls_through(self):
        """If the active source doesn't claim the action in its handles,
        route_event must NOT forward it — it should fall through to the
        later branches (HA / default / etc)."""
        r = _make_router()
        spotify = _make_source(r.registry, "spotify",
                                handles={"play", "pause"}, state="playing")
        r.registry._active_id = "spotify"
        _run(r.route_event({"action": "unknown_button", "device_type": "Audio"}))
        r._forward_to_source.assert_not_called()
        # Should reach the HA fallthrough
        r.transport.send_event.assert_awaited_once()

    def test_video_event_not_local_when_handle_video_false(self):
        r = _make_router()
        r._handle_video = False
        spotify = _make_source(r.registry, "spotify",
                                handles={"play"}, state="playing")
        r.registry._active_id = "spotify"
        _run(r.route_event({"action": "play", "device_type": "Video"}))
        r._forward_to_source.assert_not_called()
        # Non-local → HA
        r.transport.send_event.assert_awaited_once()


# ── Source button press ──────────────────────────────────────────────


class TestSourceButtonPress:
    def test_button_activates_source_and_stamps_ts(self):
        r = _make_router()
        _make_source(r.registry, "spotify", handles={"play"},
                     state="available")
        _run(r.route_event({"action": "spotify", "device_type": "Audio"}))
        r._forward_to_source.assert_awaited_once()
        args, _ = r._forward_to_source.call_args
        assert args[0].id == "spotify"
        assert args[1]["action"] == "activate"
        assert args[1]["action_ts"] > 0
        assert r._latest_action_ts == args[1]["action_ts"]
        assert "wake_screen" in r._spawned_names

    def test_already_active_button_just_wakes_screen(self):
        """Regression guard for 9ef9492: pressing the source button for
        a source that's already playing should wake the screen but NOT
        re-activate (which would restart playback)."""
        r = _make_router()
        spotify = _make_source(r.registry, "spotify",
                                handles={"play"}, state="playing")
        r.registry._active_id = "spotify"
        _run(r.route_event({"action": "spotify", "device_type": "Audio"}))
        r._forward_to_source.assert_not_called()
        assert "wake_screen" in r._spawned_names

    def test_source_button_alias_mapping(self):
        """self._source_buttons is the IR→source-id alias table.  A
        raw action like 'amem' should land on the mapped source."""
        r = _make_router()
        _make_source(r.registry, "spotify", handles={"play"},
                     state="available")
        r._source_buttons = {"amem": "spotify"}
        _run(r.route_event({"action": "amem", "device_type": "Audio"}))
        r._forward_to_source.assert_awaited_once()
        assert r._forward_to_source.call_args.args[0].id == "spotify"

    def test_gone_source_button_does_not_activate(self):
        r = _make_router()
        _make_source(r.registry, "spotify", handles={"play"},
                     state="gone")
        _run(r.route_event({"action": "spotify", "device_type": "Audio"}))
        r._forward_to_source.assert_not_called()


# ── Volume / balance / standby ──────────────────────────────────────


class TestVolumeBalance:
    def test_volup_increments_by_step(self):
        r = _make_router()
        r._volume_step = 5
        r.volume = 40
        _run(r.route_event({"action": "volup", "device_type": "Audio"}))
        # set_volume called with 45 (then spawned and awaited via _spawn).
        r.set_volume.assert_called_once_with(45)
        assert "set_volume" in r._spawned_names

    def test_voldown_increments_by_step(self):
        r = _make_router()
        r._volume_step = 3
        r.volume = 40
        _run(r.route_event({"action": "voldown", "device_type": "Audio"}))
        r.set_volume.assert_called_once_with(37)
        assert "set_volume" in r._spawned_names

    def test_volup_clamps_at_100(self):
        r = _make_router()
        r._volume_step = 10
        r.volume = 95
        _run(r.route_event({"action": "volup", "device_type": "Audio"}))
        r.set_volume.assert_called_once_with(100)

    def test_voldown_clamps_at_0(self):
        r = _make_router()
        r._volume_step = 10
        r.volume = 3
        _run(r.route_event({"action": "voldown", "device_type": "Audio"}))
        r.set_volume.assert_called_once_with(0)

    def test_balance_up_increments(self):
        r = _make_router()
        r.balance = 0
        r._balance_step = 1
        _run(r.route_event({"action": "chup", "device_type": "Audio"}))
        assert r.balance == 1

    def test_balance_clamped(self):
        r = _make_router()
        r.balance = 20
        _run(r.route_event({"action": "chup", "device_type": "Audio"}))
        assert r.balance == 20  # clamp

    def test_off_spawns_stop_and_screen_off(self):
        r = _make_router()
        _run(r.route_event({"action": "off", "device_type": "Audio"}))
        assert "off_stop" in r._spawned_names
        assert "off_screen" in r._spawned_names

    def test_off_with_volume_adapter_powers_down(self):
        r = _make_router()
        r._volume = MagicMock()
        r._volume.power_off = AsyncMock()
        _run(r.route_event({"action": "off", "device_type": "Audio"}))
        assert "off_power" in r._spawned_names


# ── Fallthrough to HA ────────────────────────────────────────────────


class TestHAFallthrough:
    def test_unknown_action_routes_to_ha(self):
        r = _make_router()
        _run(r.route_event({"action": "total_nonsense",
                             "device_type": "Audio"}))
        r.transport.send_event.assert_awaited_once()

    def test_scene_action_routes_to_ha(self):
        """Scenes are HA-owned — the router just forwards."""
        r = _make_router()
        _run(r.route_event({"action": "scene_1", "device_type": "Scene"}))
        r.transport.send_event.assert_awaited_once()


# ── Local button views suppression ──────────────────────────────────


class TestLocalViewSuppression:
    def test_go_on_local_view_suppressed(self):
        """When on a local-UI view like menu/system, the UI eats the
        ``go`` button — the router must NOT forward it anywhere."""
        r = _make_router()
        r.active_view = "menu/system"
        r._local_button_views = {"menu/system"}
        _run(r.route_event({"action": "go", "device_type": "Audio"}))
        r._forward_to_source.assert_not_called()
        r.transport.send_event.assert_not_called()

    def test_non_nav_action_on_local_view_not_suppressed(self):
        """Only nav actions (go/left/right/up/down) get suppressed —
        other actions should still flow through."""
        r = _make_router()
        r.active_view = "menu/system"
        r._local_button_views = {"menu/system"}
        _run(r.route_event({"action": "some_action", "device_type": "Audio"}))
        # Should fall through to HA (no active source, no match)
        r.transport.send_event.assert_awaited_once()


# ── Announce (menu button while playing) ────────────────────────────


class TestAnnounce:
    def test_menu_while_playing_spawns_announce(self):
        r = _make_router()
        r.media.state = {"state": "playing", "title": "Test Song"}
        _run(r.route_event({"action": "menu", "device_type": "Audio"}))
        assert "player_announce" in r._spawned_names

    def test_menu_while_idle_does_not_announce(self):
        r = _make_router()
        r.media.state = {"state": "idle"}
        _run(r.route_event({"action": "menu", "device_type": "Audio"}))
        assert "player_announce" not in r._spawned_names
        # Falls through to HA instead
        r.transport.send_event.assert_awaited_once()

    def test_info_button_same_as_menu(self):
        r = _make_router()
        r.media.state = {"state": "playing", "title": "T"}
        _run(r.route_event({"action": "info", "device_type": "Audio"}))
        assert "player_announce" in r._spawned_names
