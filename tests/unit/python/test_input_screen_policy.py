from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

SERVICES_DIR = Path(__file__).resolve().parents[3] / "services"
sys.path.insert(0, str(SERVICES_DIR))

pytestmark = pytest.mark.skipif(
    sys.version_info < (3, 10),
    reason="services/input.py uses Python 3.10+ typing syntax",
)


def _load_input_module(monkeypatch):
    sys.modules.pop("input", None)
    monkeypatch.setitem(sys.modules, "hid", types.ModuleType("hid"))
    return importlib.import_module("input")


def _presence_states(input_mod, state: str) -> dict[str, str]:
    return {
        input_mod.LOCAL_HLK_PRESENCE_ENTITY_ID: state,
    }


def _wake_states(input_mod, state: str) -> dict[str, str]:
    return {
        input_mod.LOCAL_HLK_WAKE_ENTITY_ID: state,
    }


def _hlk_state(*, enabled: bool = True, available: bool = True, wake: bool = False, presence_stable: bool = False) -> dict:
    return {
        "config": {"enabled": enabled},
        "runtime": {
            "available": available,
            "wake": wake,
            "presence_stable": presence_stable,
        },
    }


def test_router_status_url_uses_router_status_endpoint(monkeypatch):
    input_mod = _load_input_module(monkeypatch)

    assert input_mod.ROUTER_STATUS_URL.endswith("/router/status")


def test_screen_policy_presence_snapshot_marks_all_sensors_off(monkeypatch):
    input_mod = _load_input_module(monkeypatch)

    snapshot = input_mod._screen_policy_presence_snapshot(_presence_states(input_mod, "off"))

    assert snapshot["state"] == "off"
    assert snapshot["known"] is True
    assert snapshot["any_on"] is False
    assert snapshot["all_off"] is True


def test_screen_policy_presence_snapshot_treats_any_on_as_present(monkeypatch):
    input_mod = _load_input_module(monkeypatch)

    snapshot = input_mod._screen_policy_presence_snapshot({
        input_mod.LOCAL_HLK_PRESENCE_ENTITY_ID: "on",
        "local_hlk.secondary": "unknown",
    })

    assert snapshot["state"] == "on"
    assert snapshot["any_on"] is True
    assert snapshot["all_off"] is False


def test_screen_policy_local_hlk_states_uses_available_runtime(monkeypatch):
    input_mod = _load_input_module(monkeypatch)
    monkeypatch.setattr(input_mod, "_hlk_enabled", lambda: True)

    states = input_mod._screen_policy_local_hlk_states(
        _hlk_state(presence_stable=True),
        field="presence_stable",
        entity_id=input_mod.LOCAL_HLK_PRESENCE_ENTITY_ID,
    )

    assert states == {input_mod.LOCAL_HLK_PRESENCE_ENTITY_ID: "on"}


def test_screen_policy_local_hlk_states_marks_unavailable_when_service_missing(monkeypatch):
    input_mod = _load_input_module(monkeypatch)
    monkeypatch.setattr(input_mod, "_hlk_enabled", lambda: True)

    states = input_mod._screen_policy_local_hlk_states(
        None,
        field="wake",
        entity_id=input_mod.LOCAL_HLK_WAKE_ENTITY_ID,
    )

    assert input_mod.LOCAL_HLK_WAKE_ENTITY_ID in states
    assert states[input_mod.LOCAL_HLK_WAKE_ENTITY_ID] is None


def test_screen_policy_target_state_follows_playback_and_presence_rules(monkeypatch):
    input_mod = _load_input_module(monkeypatch)

    all_off = input_mod._screen_policy_presence_snapshot(_presence_states(input_mod, "off"))
    any_on = input_mod._screen_policy_presence_snapshot({
        input_mod.LOCAL_HLK_PRESENCE_ENTITY_ID: "on",
    })
    unconfigured = input_mod._screen_policy_presence_snapshot({})
    wake_on = input_mod._screen_policy_presence_snapshot(_wake_states(input_mod, "on"))
    wake_off = input_mod._screen_policy_presence_snapshot(_wake_states(input_mod, "off"))

    assert input_mod._screen_policy_target_state(
        manual_override_active=False,
        wake_snapshot=wake_off,
        wake_hold_active=False,
        local_input_recent=True,
        playing=False,
        paused=False,
        paused_on_playing_screen=False,
        currently_awake=False,
        presence_snapshot=all_off,
        all_off_since=None,
        now=100.0,
        presence_off_delay_seconds=180.0,
    ) == "on"
    assert input_mod._screen_policy_target_state(
        manual_override_active=False,
        wake_snapshot=wake_off,
        wake_hold_active=True,
        local_input_recent=False,
        playing=False,
        paused=False,
        paused_on_playing_screen=False,
        currently_awake=False,
        presence_snapshot=all_off,
        all_off_since=None,
        now=100.0,
        presence_off_delay_seconds=180.0,
    ) == "on"
    assert input_mod._screen_policy_target_state(
        manual_override_active=False,
        wake_snapshot=wake_on,
        wake_hold_active=False,
        local_input_recent=False,
        playing=False,
        paused=False,
        paused_on_playing_screen=False,
        currently_awake=False,
        presence_snapshot=all_off,
        all_off_since=None,
        now=100.0,
        presence_off_delay_seconds=180.0,
    ) == "on"
    assert input_mod._screen_policy_target_state(
        manual_override_active=False,
        wake_snapshot=wake_off,
        wake_hold_active=False,
        local_input_recent=False,
        playing=False,
        paused=False,
        paused_on_playing_screen=False,
        currently_awake=False,
        presence_snapshot=any_on,
        all_off_since=None,
        now=100.0,
        presence_off_delay_seconds=180.0,
    ) == "off"
    assert input_mod._screen_policy_target_state(
        manual_override_active=False,
        wake_snapshot=wake_off,
        wake_hold_active=False,
        local_input_recent=False,
        playing=True,
        paused=False,
        paused_on_playing_screen=False,
        currently_awake=True,
        presence_snapshot=unconfigured,
        all_off_since=None,
        now=100.0,
        presence_off_delay_seconds=180.0,
    ) == "on"
    assert input_mod._screen_policy_target_state(
        manual_override_active=False,
        wake_snapshot=wake_off,
        wake_hold_active=False,
        local_input_recent=False,
        playing=True,
        paused=False,
        paused_on_playing_screen=False,
        currently_awake=True,
        presence_snapshot=any_on,
        all_off_since=None,
        now=100.0,
        presence_off_delay_seconds=180.0,
    ) == "on"
    assert input_mod._screen_policy_target_state(
        manual_override_active=False,
        wake_snapshot=wake_off,
        wake_hold_active=False,
        local_input_recent=False,
        playing=True,
        paused=False,
        paused_on_playing_screen=False,
        currently_awake=True,
        presence_snapshot=all_off,
        all_off_since=100.0,
        now=250.0,
        presence_off_delay_seconds=180.0,
    ) == "on"
    assert input_mod._screen_policy_target_state(
        manual_override_active=False,
        wake_snapshot=wake_off,
        wake_hold_active=False,
        local_input_recent=False,
        playing=True,
        paused=False,
        paused_on_playing_screen=False,
        currently_awake=True,
        presence_snapshot=all_off,
        all_off_since=100.0,
        now=281.0,
        presence_off_delay_seconds=180.0,
    ) == "off"
    assert input_mod._screen_policy_target_state(
        manual_override_active=False,
        wake_snapshot=wake_on,
        wake_hold_active=True,
        local_input_recent=False,
        playing=False,
        paused=True,
        paused_on_playing_screen=False,
        currently_awake=False,
        presence_snapshot=all_off,
        all_off_since=None,
        now=100.0,
        presence_off_delay_seconds=180.0,
    ) == "off"
    assert input_mod._screen_policy_target_state(
        manual_override_active=False,
        wake_snapshot=wake_off,
        wake_hold_active=False,
        local_input_recent=False,
        playing=False,
        paused=True,
        paused_on_playing_screen=True,
        currently_awake=False,
        presence_snapshot=unconfigured,
        all_off_since=None,
        now=100.0,
        presence_off_delay_seconds=180.0,
    ) == "off"
    assert input_mod._screen_policy_target_state(
        manual_override_active=False,
        wake_snapshot=wake_off,
        wake_hold_active=False,
        local_input_recent=False,
        playing=False,
        paused=True,
        paused_on_playing_screen=True,
        currently_awake=True,
        presence_snapshot=unconfigured,
        all_off_since=None,
        now=100.0,
        presence_off_delay_seconds=180.0,
    ) == "on"


def test_screen_policy_target_state_honours_manual_override(monkeypatch):
    input_mod = _load_input_module(monkeypatch)

    all_off = input_mod._screen_policy_presence_snapshot(_presence_states(input_mod, "off"))
    wake_on = input_mod._screen_policy_presence_snapshot(_wake_states(input_mod, "on"))

    assert input_mod._screen_policy_target_state(
        manual_override_active=True,
        wake_snapshot=wake_on,
        wake_hold_active=True,
        local_input_recent=True,
        playing=True,
        paused=False,
        paused_on_playing_screen=False,
        currently_awake=True,
        presence_snapshot=all_off,
        all_off_since=None,
        now=100.0,
        presence_off_delay_seconds=180.0,
    ) == "off"


def test_screen_policy_router_is_playing_uses_media_then_active_source(monkeypatch):
    input_mod = _load_input_module(monkeypatch)

    assert input_mod._screen_policy_router_is_playing({
        "media": {"state": "buffering"},
    }) is True
    assert input_mod._screen_policy_router_is_playing({
        "media": {"state": "paused"},
        "active_source": "spotify",
        "sources": {"spotify": {"state": "playing"}},
    }) is False
    assert input_mod._screen_policy_router_is_playing({
        "active_source": "spotify",
        "sources": {"spotify": {"state": "playing"}},
    }) is True


@pytest.mark.asyncio
async def test_screen_policy_tick_turns_screen_off_when_idle_timeout_expires(monkeypatch):
    input_mod = _load_input_module(monkeypatch)
    state = input_mod._new_screen_policy_state()
    state["last_local_input_at"] = 0.0
    monkeypatch.setattr(input_mod, "_screen_policy_state", state)
    monkeypatch.setattr(input_mod.time, "monotonic", lambda: 400.0)
    monkeypatch.setattr(
        input_mod,
        "_fetch_router_status_snapshot",
        AsyncMock(return_value={"media": {"state": "idle"}}),
    )
    monkeypatch.setattr(input_mod, "_hlk_enabled", lambda: False)
    monkeypatch.setattr(input_mod, "_screen_idle_off_delay_seconds", lambda: 180.0)
    set_display_awake = AsyncMock()
    monkeypatch.setattr(input_mod, "_set_display_awake", set_display_awake)

    result = await input_mod._screen_policy_tick()

    assert result == "off:idle_timeout"
    set_display_awake.assert_awaited_once_with(False)


@pytest.mark.asyncio
async def test_screen_policy_tick_waits_for_presence_grace_before_screen_off(monkeypatch):
    input_mod = _load_input_module(monkeypatch)
    state = input_mod._new_screen_policy_state()
    state["applied_target"] = "on"
    state["last_local_input_at"] = 0.0
    monkeypatch.setattr(input_mod, "_screen_policy_state", state)
    monkeypatch.setattr(input_mod.time, "monotonic", lambda: 1000.0)
    monkeypatch.setattr(
        input_mod,
        "_fetch_router_status_snapshot",
        AsyncMock(return_value={"media": {"state": "playing"}}),
    )
    monkeypatch.setattr(input_mod, "_hlk_enabled", lambda: True)
    monkeypatch.setattr(
        input_mod,
        "_fetch_local_hlk_state",
        AsyncMock(return_value=_hlk_state(wake=False, presence_stable=False)),
    )
    monkeypatch.setattr(input_mod, "_screen_presence_off_delay_seconds", lambda: 180.0)
    set_display_awake = AsyncMock()
    monkeypatch.setattr(input_mod, "_set_display_awake", set_display_awake)

    result = await input_mod._screen_policy_tick()

    assert result == "unchanged:on:presence_off_grace"
    assert input_mod._screen_policy_state["all_off_since"] == 1000.0
    set_display_awake.assert_not_awaited()


@pytest.mark.asyncio
async def test_screen_policy_tick_turns_screen_on_when_presence_returns_during_playback(monkeypatch):
    input_mod = _load_input_module(monkeypatch)
    state = input_mod._new_screen_policy_state()
    state["applied_target"] = "off"
    state["last_local_input_at"] = 0.0
    monkeypatch.setattr(input_mod, "_screen_policy_state", state)
    monkeypatch.setattr(input_mod.time, "monotonic", lambda: 700.0)
    monkeypatch.setattr(
        input_mod,
        "_fetch_router_status_snapshot",
        AsyncMock(return_value={"media": {"state": "playing"}}),
    )
    monkeypatch.setattr(input_mod, "_hlk_enabled", lambda: True)
    monkeypatch.setattr(
        input_mod,
        "_fetch_local_hlk_state",
        AsyncMock(return_value=_hlk_state(wake=False, presence_stable=True)),
    )
    set_display_awake = AsyncMock()
    monkeypatch.setattr(input_mod, "_set_display_awake", set_display_awake)

    result = await input_mod._screen_policy_tick()

    assert result == "on:presence_on"
    set_display_awake.assert_awaited_once_with(True)


@pytest.mark.asyncio
async def test_screen_policy_tick_wakes_screen_when_macro_sensor_turns_on(monkeypatch):
    input_mod = _load_input_module(monkeypatch)
    state = input_mod._new_screen_policy_state()
    state["applied_target"] = "off"
    state["last_local_input_at"] = 0.0
    monkeypatch.setattr(input_mod, "_screen_policy_state", state)
    monkeypatch.setattr(input_mod.time, "monotonic", lambda: 900.0)
    monkeypatch.setattr(
        input_mod,
        "_fetch_router_status_snapshot",
        AsyncMock(return_value={"media": {"state": "idle"}}),
    )
    monkeypatch.setattr(input_mod, "_hlk_enabled", lambda: True)
    monkeypatch.setattr(
        input_mod,
        "_fetch_local_hlk_state",
        AsyncMock(return_value=_hlk_state(wake=True, presence_stable=False)),
    )
    set_display_awake = AsyncMock()
    monkeypatch.setattr(input_mod, "_set_display_awake", set_display_awake)

    result = await input_mod._screen_policy_tick()

    assert result == "on:wake_on"
    set_display_awake.assert_awaited_once_with(True)


@pytest.mark.asyncio
async def test_screen_policy_tick_keeps_screen_on_after_recent_local_input(monkeypatch):
    input_mod = _load_input_module(monkeypatch)
    state = input_mod._new_screen_policy_state()
    state["applied_target"] = "off"
    state["last_local_input_at"] = 90.0
    monkeypatch.setattr(input_mod, "_screen_policy_state", state)
    monkeypatch.setattr(input_mod.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(
        input_mod,
        "_fetch_router_status_snapshot",
        AsyncMock(return_value={"media": {"state": "idle"}}),
    )
    monkeypatch.setattr(input_mod, "_hlk_enabled", lambda: False)
    monkeypatch.setattr(input_mod, "_screen_idle_off_delay_seconds", lambda: 180.0)
    set_display_awake = AsyncMock()
    monkeypatch.setattr(input_mod, "_set_display_awake", set_display_awake)

    result = await input_mod._screen_policy_tick()

    assert result == "on:local_input_recent"
    set_display_awake.assert_awaited_once_with(True)


@pytest.mark.asyncio
async def test_screen_policy_tick_holds_screen_on_for_wake_grace_after_sensor_clears(monkeypatch):
    input_mod = _load_input_module(monkeypatch)
    state = input_mod._new_screen_policy_state()
    state["applied_target"] = "off"
    state["last_local_input_at"] = 0.0
    state["last_wake_sensor_state"] = "on"
    monkeypatch.setattr(input_mod, "_screen_policy_state", state)
    monkeypatch.setattr(input_mod.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(
        input_mod,
        "_fetch_router_status_snapshot",
        AsyncMock(return_value={"media": {"state": "idle"}}),
    )
    monkeypatch.setattr(input_mod, "_hlk_enabled", lambda: True)
    monkeypatch.setattr(
        input_mod,
        "_fetch_local_hlk_state",
        AsyncMock(return_value=_hlk_state(wake=False, presence_stable=False)),
    )
    monkeypatch.setattr(input_mod, "_screen_wake_hold_seconds", lambda: 30.0)
    set_display_awake = AsyncMock()
    monkeypatch.setattr(input_mod, "_set_display_awake", set_display_awake)

    result = await input_mod._screen_policy_tick()

    assert result == "on:wake_grace"
    assert input_mod._screen_policy_state["wake_hold_until"] == 130.0
    set_display_awake.assert_awaited_once_with(True)


def test_note_local_screen_activity_cancels_wake_hold(monkeypatch):
    input_mod = _load_input_module(monkeypatch)
    state = input_mod._new_screen_policy_state()
    state["manual_off_latched"] = True
    state["wake_hold_until"] = 130.0
    monkeypatch.setattr(input_mod, "_screen_policy_state", state)
    monkeypatch.setattr(input_mod.time, "monotonic", lambda: 100.0)

    input_mod._note_local_screen_activity("hid_rotary")

    assert input_mod._screen_policy_state["last_local_input_at"] == 100.0
    assert input_mod._screen_policy_state["manual_off_latched"] is False
    assert input_mod._screen_policy_state["wake_hold_until"] == 0.0


def test_set_screen_manual_override_can_latch_until_physical_input(monkeypatch):
    input_mod = _load_input_module(monkeypatch)
    state = input_mod._new_screen_policy_state()
    monkeypatch.setattr(input_mod, "_screen_policy_state", state)

    input_mod._set_screen_manual_override("command:screen_off", persistent=True)

    assert input_mod._screen_policy_state["manual_off_latched"] is True
    assert input_mod._screen_policy_manual_override_active(100.0) is True


@pytest.mark.asyncio
async def test_process_command_does_not_wake_screen_while_manual_override_latched(monkeypatch):
    input_mod = _load_input_module(monkeypatch)
    state = input_mod._new_screen_policy_state()
    state["manual_off_latched"] = True
    monkeypatch.setattr(input_mod, "_screen_policy_state", state)
    set_display_awake = AsyncMock()
    monkeypatch.setattr(input_mod, "_set_display_awake", set_display_awake)
    forward_to_router = AsyncMock()
    monkeypatch.setattr(input_mod, "_forward_to_router", forward_to_router)

    result = await input_mod.process_command({
        "command": "wake",
        "params": {"page": "now_playing"},
    })

    assert result == {"status": "ok", "screen": "off", "blocked": "manual_override"}
    set_display_awake.assert_not_awaited()
    forward_to_router.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_command_screen_toggle_does_not_wake_while_manual_override_latched(monkeypatch):
    input_mod = _load_input_module(monkeypatch)
    state = input_mod._new_screen_policy_state()
    state["manual_off_latched"] = True
    monkeypatch.setattr(input_mod, "_screen_policy_state", state)
    monkeypatch.setattr(input_mod, "is_backlight_on", lambda: False)
    toggle_backlight = MagicMock()
    monkeypatch.setattr(input_mod, "toggle_backlight", toggle_backlight)

    result = await input_mod.process_command({
        "command": "screen_toggle",
        "params": {},
    })

    assert result == {"status": "ok", "screen": "off", "blocked": "manual_override"}
    toggle_backlight.assert_not_called()


def test_parse_report_records_local_activity_for_rotary_not_power(monkeypatch):
    input_mod = _load_input_module(monkeypatch)
    recorded = []
    monkeypatch.setattr(input_mod, "_note_local_screen_activity", lambda source='hid': recorded.append(source))

    input_mod.parse_report([1, 0, 0, 0], loop=None)
    input_mod.parse_report([0, 0, 0, 0x80], loop=None)

    assert recorded == ["hid_rotary"]
