"""Tests for router.py HTTP handler input validation.

The handlers in router.py are thin wrappers around EventRouter and
SourceRegistry, but their *validation* (bad JSON, missing fields,
invalid enum values) is the frontline defence against bad payloads
reaching the internals.  None of it had direct tests — test_router.py
covered the state internals, not the HTTP validation layer.

Strategy: monkeypatch ``router.router_instance`` with a MagicMock for
the duration of each test.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import router as router_module
from router import (
    handle_broadcast,
    handle_event,
    handle_output_off,
    handle_output_on,
    handle_source,
    handle_view,
    handle_volume_report,
    handle_volume_set,
)


class _FakeRequest:
    def __init__(self, body=None, raise_on_json=False):
        self._body = body
        self._raise = raise_on_json

    async def json(self):
        if self._raise:
            raise ValueError("invalid json")
        return self._body


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _body(resp) -> dict:
    return json.loads(resp.text)


@pytest.fixture
def fake_router_instance(monkeypatch):
    fake = MagicMock()
    fake.route_event = AsyncMock()
    fake.registry = MagicMock()
    fake.registry.update = AsyncMock(return_value={
        "actions": [], "old_state": "available", "new_state": "playing",
    })
    fake.registry.active_id = "spotify"
    fake.media = MagicMock()
    fake.media.broadcast = AsyncMock()
    fake.set_volume = AsyncMock()
    fake.report_volume = AsyncMock()
    fake.volume = 30
    fake._volume = None
    fake.touch_activity = MagicMock()
    fake.active_view = None
    monkeypatch.setattr(router_module, "router_instance", fake)
    return fake


# ── handle_event ─────────────────────────────────────────────────────


class TestHandleEvent:
    def test_valid_json_delegated_to_route_event(self, fake_router_instance):
        resp = _run(handle_event(_FakeRequest({"type": "button", "event": "play"})))
        assert resp.status == 200
        fake_router_instance.route_event.assert_awaited_once()

    def test_invalid_json_returns_400(self, fake_router_instance):
        resp = _run(handle_event(_FakeRequest(raise_on_json=True)))
        assert resp.status == 400
        assert "invalid json" in _body(resp)["error"]
        fake_router_instance.route_event.assert_not_called()


# ── handle_source ────────────────────────────────────────────────────


class TestHandleSource:
    def test_missing_id_returns_400(self, fake_router_instance):
        resp = _run(handle_source(_FakeRequest({"state": "playing"})))
        assert resp.status == 400
        assert "required" in _body(resp)["error"]

    def test_missing_state_returns_400(self, fake_router_instance):
        resp = _run(handle_source(_FakeRequest({"id": "spotify"})))
        assert resp.status == 400

    def test_invalid_state_returns_400(self, fake_router_instance):
        resp = _run(handle_source(_FakeRequest({
            "id": "spotify", "state": "bogus",
        })))
        assert resp.status == 400
        assert "invalid state" in _body(resp)["error"]

    def test_valid_state_dispatches_to_registry(self, fake_router_instance):
        resp = _run(handle_source(_FakeRequest({
            "id": "spotify", "state": "playing",
            "name": "Spotify", "command_url": "http://localhost:8771/command",
        })))
        assert resp.status == 200
        fake_router_instance.registry.update.assert_awaited_once()
        # Response includes the active_source and the update result
        body = _body(resp)
        assert body["source"] == "spotify"
        assert body["active_source"] == "spotify"

    def test_only_whitelisted_fields_forwarded_to_update(self, fake_router_instance):
        """handle_source has a field whitelist — arbitrary extra keys
        must NOT reach registry.update() (they could cause TypeError
        and would otherwise let clients inject surprise kwargs)."""
        _run(handle_source(_FakeRequest({
            "id": "spotify", "state": "playing",
            "name": "Spotify",
            "rogue_field": "should_be_dropped",
            "__init__": "definitely_should_be_dropped",
        })))
        call = fake_router_instance.registry.update.call_args
        # Kwargs passed to update: only the whitelist
        kwargs = call.kwargs
        assert "rogue_field" not in kwargs
        assert "__init__" not in kwargs
        assert kwargs["name"] == "Spotify"

    def test_invalid_json_returns_400(self, fake_router_instance):
        resp = _run(handle_source(_FakeRequest(raise_on_json=True)))
        assert resp.status == 400
        fake_router_instance.registry.update.assert_not_called()


# ── handle_volume_set ────────────────────────────────────────────────


class TestHandleVolumeSet:
    def test_valid_numeric_volume_dispatched(self, fake_router_instance):
        resp = _run(handle_volume_set(_FakeRequest({"volume": 50})))
        assert resp.status == 200
        fake_router_instance.set_volume.assert_awaited_once()
        # touch_activity called — UI should wake
        fake_router_instance.touch_activity.assert_called_once()

    def test_float_volume_accepted(self, fake_router_instance):
        resp = _run(handle_volume_set(_FakeRequest({"volume": 42.5})))
        assert resp.status == 200
        fake_router_instance.set_volume.assert_awaited_once()

    def test_missing_volume_returns_400(self, fake_router_instance):
        resp = _run(handle_volume_set(_FakeRequest({})))
        assert resp.status == 400
        fake_router_instance.set_volume.assert_not_called()

    def test_non_numeric_volume_returns_400(self, fake_router_instance):
        resp = _run(handle_volume_set(_FakeRequest({"volume": "loud"})))
        assert resp.status == 400

    def test_invalid_json_returns_400(self, fake_router_instance):
        resp = _run(handle_volume_set(_FakeRequest(raise_on_json=True)))
        assert resp.status == 400


# ── handle_volume_report ─────────────────────────────────────────────


class TestHandleVolumeReport:
    def test_valid_numeric_dispatched(self, fake_router_instance):
        resp = _run(handle_volume_report(_FakeRequest({"volume": 42})))
        assert resp.status == 200
        fake_router_instance.report_volume.assert_awaited_once()

    def test_missing_returns_400(self, fake_router_instance):
        resp = _run(handle_volume_report(_FakeRequest({})))
        assert resp.status == 400

    def test_string_returns_400(self, fake_router_instance):
        resp = _run(handle_volume_report(_FakeRequest({"volume": "x"})))
        assert resp.status == 400


# ── handle_view ──────────────────────────────────────────────────────


class TestHandleView:
    def test_valid_view_updates_active(self, fake_router_instance):
        _run(handle_view(_FakeRequest({"view": "menu/playing"})))
        assert fake_router_instance.active_view == "menu/playing"

    def test_invalid_json_returns_400(self, fake_router_instance):
        resp = _run(handle_view(_FakeRequest(raise_on_json=True)))
        assert resp.status == 400


# ── handle_output_on / off ───────────────────────────────────────────


class TestOutputControl:
    def test_output_off_without_adapter_is_noop(self, fake_router_instance):
        fake_router_instance._volume = None
        resp = _run(handle_output_off(_FakeRequest({})))
        assert resp.status == 200
        assert _body(resp)["output"] == "no_adapter"

    def test_output_on_without_adapter_is_noop(self, fake_router_instance):
        fake_router_instance._volume = None
        resp = _run(handle_output_on(_FakeRequest({})))
        assert resp.status == 200
        assert _body(resp)["output"] == "no_adapter"

    def test_output_off_with_adapter_calls_power_off(self, fake_router_instance):
        adapter = MagicMock()
        adapter.power_off = AsyncMock()
        fake_router_instance._volume = adapter
        resp = _run(handle_output_off(_FakeRequest({})))
        assert resp.status == 200
        adapter.power_off.assert_awaited_once()

    def test_output_on_with_adapter_calls_power_on(self, fake_router_instance):
        adapter = MagicMock()
        adapter.power_on = AsyncMock()
        fake_router_instance._volume = adapter
        resp = _run(handle_output_on(_FakeRequest({})))
        assert resp.status == 200
        adapter.power_on.assert_awaited_once()


# ── handle_broadcast ─────────────────────────────────────────────────


class TestHandleBroadcast:
    def test_valid_broadcast_delegated(self, fake_router_instance):
        resp = _run(handle_broadcast(_FakeRequest({
            "type": "menu_item",
            "data": {"action": "add", "preset": "spotify"},
        })))
        assert resp.status == 200
        fake_router_instance.media.broadcast.assert_awaited_once()
        call = fake_router_instance.media.broadcast.call_args
        assert call.args[0] == "menu_item"
        assert call.args[1]["preset"] == "spotify"

    def test_invalid_json_returns_400(self, fake_router_instance):
        resp = _run(handle_broadcast(_FakeRequest(raise_on_json=True)))
        assert resp.status == 400
        fake_router_instance.media.broadcast.assert_not_called()

    def test_missing_type_defaults_to_unknown(self, fake_router_instance):
        """broadcast is permissive — missing type becomes ``unknown``
        rather than 400.  This pins that permissive behaviour."""
        resp = _run(handle_broadcast(_FakeRequest({"data": {"x": 1}})))
        assert resp.status == 200
        call = fake_router_instance.media.broadcast.call_args
        assert call.args[0] == "unknown"
