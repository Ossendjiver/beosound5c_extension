from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

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


def _presence_states(state: str) -> dict[str, str]:
    return {
        "binary_sensor.lounge_hlk_presence_stable": state,
        "binary_sensor.desk_room_presence": state,
        "binary_sensor.dining_hlk_presence_stable": state,
    }


def _wake_states(state: str) -> dict[str, str]:
    return {
        "binary_sensor.bs5c_macro": state,
    }


def test_router_status_url_uses_router_status_endpoint(monkeypatch):
    input_mod = _load_input_module(monkeypatch)

    assert input_mod.ROUTER_STATUS_URL.endswith("/router/status")


def test_screen_policy_presence_snapshot_marks_all_sensors_off(monkeypatch):
    input_mod = _load_input_module(monkeypatch)

    snapshot = input_mod._screen_policy_presence_snapshot(_presence_states("off"))

    assert snapshot["state"] == "off"
    assert snapshot["known"] is True
    assert snapshot["any_on"] is False
    assert snapshot["all_off"] is True


def test_screen_policy_presence_snapshot_treats_any_on_as_present(monkeypatch):
    input_mod = _load_input_module(monkeypatch)

    snapshot = input_mod._screen_policy_presence_snapshot({
        "binary_sensor.lounge_hlk_presence_stable": "off",
        "binary_sensor.desk_room_presence": "on",
        "binary_sensor.dining_hlk_presence_stable": "unknown",
    })

    assert snapshot["state"] == "on"
    assert snapshot["any_on"] is True
    assert snapshot["all_off"] is False


def test_screen_policy_target_state_follows_playback_and_presence_rules(monkeypatch):
    input_mod = _load_input_module(monkeypatch)

    all_off = input_mod._screen_policy_presence_snapshot(_presence_states("off"))
    any_on = input_mod._screen_policy_presence_snapshot({
        "binary_sensor.lounge_hlk_presence_stable": "on",
    })
    unconfigured = input_mod._screen_policy_presence_snapshot({})
    wake_on = input_mod._screen_policy_presence_snapshot(_wake_states("on"))
    wake_off = input_mod._screen_policy_presence_snapshot(_wake_states("off"))

    assert input_mod._screen_policy_target_state(
        local_input_active=True,
        wake_snapshot=wake_on,
        playing=False,
        presence_snapshot=all_off,
        all_off_since=None,
        now=100.0,
        off_delay_seconds=600.0,
    ) == "on"
    assert input_mod._screen_policy_target_state(
        local_input_active=False,
        wake_snapshot=wake_off,
        playing=False,
        presence_snapshot=any_on,
        all_off_since=None,
        now=100.0,
        off_delay_seconds=600.0,
    ) == "off"
    assert input_mod._screen_policy_target_state(
        local_input_active=False,
        wake_snapshot=wake_off,
        playing=True,
        presence_snapshot=unconfigured,
        all_off_since=None,
        now=100.0,
        off_delay_seconds=600.0,
    ) == "on"
    assert input_mod._screen_policy_target_state(
        local_input_active=False,
        wake_snapshot=wake_off,
        playing=True,
        presence_snapshot=all_off,
        all_off_since=100.0,
        now=650.0,
        off_delay_seconds=600.0,
    ) is None
    assert input_mod._screen_policy_target_state(
        local_input_active=False,
        wake_snapshot=wake_off,
        playing=True,
        presence_snapshot=all_off,
        all_off_since=100.0,
        now=701.0,
        off_delay_seconds=600.0,
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
async def test_screen_policy_tick_turns_screen_off_when_nothing_is_playing(monkeypatch):
    input_mod = _load_input_module(monkeypatch)
    monkeypatch.setattr(input_mod, "_screen_policy_state", input_mod._new_screen_policy_state())
    monkeypatch.setattr(input_mod.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(
        input_mod,
        "_fetch_router_status_snapshot",
        AsyncMock(return_value={"media": {"state": "idle"}}),
    )
    monkeypatch.setattr(
        input_mod,
        "_fetch_screen_presence_states",
        AsyncMock(side_effect=[_wake_states("off"), _presence_states("on")]),
    )
    set_display_awake = AsyncMock()
    monkeypatch.setattr(input_mod, "_set_display_awake", set_display_awake)

    result = await input_mod._screen_policy_tick()

    assert result == "off:not_playing"
    set_display_awake.assert_awaited_once_with(False)


@pytest.mark.asyncio
async def test_screen_policy_tick_waits_for_presence_grace_before_screen_off(monkeypatch):
    input_mod = _load_input_module(monkeypatch)
    monkeypatch.setattr(input_mod, "_screen_policy_state", input_mod._new_screen_policy_state())
    monkeypatch.setattr(input_mod.time, "monotonic", lambda: 1000.0)
    monkeypatch.setattr(
        input_mod,
        "_fetch_router_status_snapshot",
        AsyncMock(return_value={"media": {"state": "playing"}}),
    )
    monkeypatch.setattr(
        input_mod,
        "_fetch_screen_presence_states",
        AsyncMock(side_effect=[_wake_states("off"), _presence_states("off")]),
    )
    monkeypatch.setattr(input_mod, "_screen_presence_off_delay_seconds", lambda: 600.0)
    set_display_awake = AsyncMock()
    monkeypatch.setattr(input_mod, "_set_display_awake", set_display_awake)

    result = await input_mod._screen_policy_tick()

    assert result == "hold:presence_off_grace"
    assert input_mod._screen_policy_state["all_off_since"] == 1000.0
    set_display_awake.assert_not_awaited()


@pytest.mark.asyncio
async def test_screen_policy_tick_turns_screen_on_when_presence_returns_during_playback(monkeypatch):
    input_mod = _load_input_module(monkeypatch)
    state = input_mod._new_screen_policy_state()
    state["applied_target"] = "off"
    monkeypatch.setattr(input_mod, "_screen_policy_state", state)
    monkeypatch.setattr(input_mod.time, "monotonic", lambda: 700.0)
    monkeypatch.setattr(
        input_mod,
        "_fetch_router_status_snapshot",
        AsyncMock(return_value={"media": {"state": "playing"}}),
    )
    monkeypatch.setattr(
        input_mod,
        "_fetch_screen_presence_states",
        AsyncMock(side_effect=[
            _wake_states("off"),
            {
                "binary_sensor.lounge_hlk_presence_stable": "off",
                "binary_sensor.desk_room_presence": "on",
                "binary_sensor.dining_hlk_presence_stable": "off",
            },
        ]),
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
    monkeypatch.setattr(input_mod, "_screen_policy_state", state)
    monkeypatch.setattr(input_mod.time, "monotonic", lambda: 900.0)
    monkeypatch.setattr(
        input_mod,
        "_fetch_router_status_snapshot",
        AsyncMock(return_value={"media": {"state": "idle"}}),
    )
    monkeypatch.setattr(
        input_mod,
        "_fetch_screen_presence_states",
        AsyncMock(side_effect=[_wake_states("on"), _presence_states("off")]),
    )
    set_display_awake = AsyncMock()
    monkeypatch.setattr(input_mod, "_set_display_awake", set_display_awake)

    result = await input_mod._screen_policy_tick()

    assert result == "on:wake_on"
    set_display_awake.assert_awaited_once_with(True)


@pytest.mark.asyncio
async def test_screen_policy_tick_holds_screen_on_after_local_input(monkeypatch):
    input_mod = _load_input_module(monkeypatch)
    state = input_mod._new_screen_policy_state()
    state["applied_target"] = "off"
    state["local_wake_until"] = 130.0
    monkeypatch.setattr(input_mod, "_screen_policy_state", state)
    monkeypatch.setattr(input_mod.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(
        input_mod,
        "_fetch_router_status_snapshot",
        AsyncMock(return_value={"media": {"state": "idle"}}),
    )
    monkeypatch.setattr(
        input_mod,
        "_fetch_screen_presence_states",
        AsyncMock(side_effect=[_wake_states("off"), _presence_states("off")]),
    )
    set_display_awake = AsyncMock()
    monkeypatch.setattr(input_mod, "_set_display_awake", set_display_awake)

    result = await input_mod._screen_policy_tick()

    assert result == "on:local_input"
    set_display_awake.assert_awaited_once_with(True)


def test_parse_report_records_local_activity_for_rotary_not_power(monkeypatch):
    input_mod = _load_input_module(monkeypatch)
    recorded = []
    monkeypatch.setattr(input_mod, "_note_local_screen_activity", lambda source='hid': recorded.append(source))

    input_mod.parse_report([1, 0, 0, 0], loop=None)
    input_mod.parse_report([0, 0, 0, 0x80], loop=None)

    assert recorded == ["hid_rotary"]
