"""Tests for source registry: activation, deactivation, timestamp rejection."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

SERVICES_DIR = Path(__file__).resolve().parents[3] / "services"
sys.path.insert(0, str(SERVICES_DIR))

from lib.source_registry import Source, SourceRegistry


def make_router_mock():
    """Create a mock router with the interface SourceRegistry needs."""
    router = MagicMock()
    router.media = MagicMock()
    router.media.broadcast = AsyncMock()
    router.media.push_idle = AsyncMock()
    router._latest_action_ts = 0.0
    router._forward_to_source = AsyncMock()
    router._wake_screen = AsyncMock()
    router._get_config_title = MagicMock(return_value=None)
    router._get_after = MagicMock(return_value=None)
    router._volume = None
    return router


class TestSourceLifecycle:
    def test_new_source_starts_gone(self):
        s = Source("test", {"play", "stop"})
        assert s.state == "gone"

    def test_create_from_config(self):
        reg = SourceRegistry()
        s = reg.create_from_config("spotify", {"play", "pause"})
        assert s.id == "spotify"
        assert s.state == "gone"
        assert reg.get("spotify") is s

    def test_register_available(self):
        reg = SourceRegistry()
        router = make_router_mock()
        result = asyncio.run(reg.update("cd", "available", router,
                                        name="CD", command_url="http://localhost:8769/command",
                                        handles=["play", "stop"]))
        assert "add_menu_item" in result["actions"]
        src = reg.get("cd")
        assert src.state == "available"

    def test_activate_source(self):
        reg = SourceRegistry()
        router = make_router_mock()
        asyncio.run(reg.update("spotify", "available", router,
                                name="Spotify", command_url="http://localhost:8771/command"))
        asyncio.run(reg.update("spotify", "playing", router, action_ts=100))
        assert reg.active_id == "spotify"
        router.media.broadcast.assert_any_call("source_change", {
            "active_source": "spotify", "source_name": "Spotify", "player": "local",
        })

    def test_deactivate_clears_media(self):
        reg = SourceRegistry()
        router = make_router_mock()
        asyncio.run(reg.update("cd", "available", router,
                                name="CD", command_url="http://localhost:8769/command"))
        asyncio.run(reg.update("cd", "playing", router, action_ts=100))
        assert reg.active_id == "cd"
        asyncio.run(reg.update("cd", "available", router))
        assert reg.active_id is None
        router.media.push_idle.assert_called()

    def test_gone_clears_active(self):
        reg = SourceRegistry()
        router = make_router_mock()
        asyncio.run(reg.update("cd", "available", router,
                                name="CD", command_url="http://localhost:8769/command"))
        asyncio.run(reg.update("cd", "playing", router, action_ts=100))
        asyncio.run(reg.update("cd", "gone", router))
        assert reg.active_id is None


class TestActivationExclusivity:
    """Only one source can be active at a time."""

    def test_second_source_stops_first(self):
        reg = SourceRegistry()
        router = make_router_mock()
        asyncio.run(reg.update("cd", "available", router,
                                name="CD", command_url="http://localhost:8769/command"))
        asyncio.run(reg.update("spotify", "available", router,
                                name="Spotify", command_url="http://localhost:8771/command"))
        asyncio.run(reg.update("cd", "playing", router, action_ts=100))
        assert reg.active_id == "cd"
        asyncio.run(reg.update("spotify", "playing", router, action_ts=200))
        assert reg.active_id == "spotify"
        # Old source should have been stopped
        router._forward_to_source.assert_called()

    def test_paused_doesnt_steal_from_playing(self):
        reg = SourceRegistry()
        router = make_router_mock()
        asyncio.run(reg.update("cd", "available", router,
                                name="CD", command_url="http://localhost:8769/command"))
        asyncio.run(reg.update("spotify", "available", router,
                                name="Spotify", command_url="http://localhost:8771/command"))
        asyncio.run(reg.update("spotify", "playing", router, action_ts=100))
        asyncio.run(reg.update("cd", "paused", router))
        # Spotify should still be active
        assert reg.active_id == "spotify"


class TestActionTimestampRejection:
    """Stale timestamps should be rejected to prevent race conditions."""

    def test_reject_stale_activation(self):
        reg = SourceRegistry()
        router = make_router_mock()
        router._latest_action_ts = 200.0  # Global timestamp from a newer activation
        asyncio.run(reg.update("radio", "available", router,
                                name="Radio", command_url="http://localhost:8779/command"))
        # Radio tries to activate with old timestamp
        result = asyncio.run(reg.update("radio", "playing", router, action_ts=100))
        assert reg.active_id is None  # Should be rejected

    def test_accept_fresh_activation(self):
        reg = SourceRegistry()
        router = make_router_mock()
        router._latest_action_ts = 100.0
        asyncio.run(reg.update("radio", "available", router,
                                name="Radio", command_url="http://localhost:8779/command"))
        asyncio.run(reg.update("radio", "playing", router, action_ts=200))
        assert reg.active_id == "radio"

    def test_zero_timestamp_always_passes(self):
        reg = SourceRegistry()
        router = make_router_mock()
        router._latest_action_ts = 100.0
        asyncio.run(reg.update("cd", "available", router,
                                name="CD", command_url="http://localhost:8769/command"))
        asyncio.run(reg.update("cd", "playing", router, action_ts=0))
        assert reg.active_id == "cd"


class TestClearActiveSource:
    def test_clears_and_pushes_idle(self):
        reg = SourceRegistry()
        router = make_router_mock()
        asyncio.run(reg.update("spotify", "available", router,
                                name="Spotify", command_url="http://localhost:8771/command"))
        asyncio.run(reg.update("spotify", "playing", router, action_ts=100))
        assert reg.active_id == "spotify"
        asyncio.run(reg.clear_active_source(router))
        assert reg.active_id is None
        # Must push idle media to clear stale metadata
        router.media.push_idle.assert_called_with("external_override")

    def test_noop_when_no_active(self):
        reg = SourceRegistry()
        router = make_router_mock()
        result = asyncio.run(reg.clear_active_source(router))
        assert result is False


class TestInvalidTransitions:
    """State machine enforcement in update()."""

    def test_gone_to_playing_accepted_as_resync(self):
        """Router-restart resync: source re-registers directly into
        playing. Must succeed so the router sees the source again."""
        reg = SourceRegistry()
        router = make_router_mock()
        result = asyncio.run(reg.update("radio", "playing", router,
                                        name="Radio", command_url="http://localhost:8779/command",
                                        action_ts=100))
        assert "rejected" not in result
        assert reg.active_id == "radio"

    def test_available_to_available_accepted(self):
        """Re-registration (resync) must work."""
        reg = SourceRegistry()
        router = make_router_mock()
        asyncio.run(reg.update("cd", "available", router,
                                name="CD", command_url="http://localhost:8769/command"))
        result = asyncio.run(reg.update("cd", "available", router,
                                        name="CD", command_url="http://localhost:8769/command"))
        assert "rejected" not in result


class TestMenuVisibility:
    def test_never_visible_not_added_to_menu(self):
        reg = SourceRegistry()
        router = make_router_mock()
        s = reg.create_from_config("cd", {"play"})
        s.visible = "never"
        asyncio.run(reg.update("cd", "available", router,
                                name="CD", command_url="http://localhost:8769/command"))
        # Should not broadcast menu_item add
        calls = [c for c in router.media.broadcast.call_args_list
                 if c[0][0] == "menu_item"]
        assert len(calls) == 0

    def test_always_visible_not_removed_on_gone(self):
        reg = SourceRegistry()
        router = make_router_mock()
        s = reg.create_from_config("spotify", {"play"})
        s.visible = "always"
        asyncio.run(reg.update("spotify", "available", router,
                                name="Spotify", command_url="http://localhost:8771/command"))
        router.media.broadcast.reset_mock()
        asyncio.run(reg.update("spotify", "gone", router))
        calls = [c for c in router.media.broadcast.call_args_list
                 if c[0][0] == "menu_item" and c[0][1].get("action") == "remove"]
        assert len(calls) == 0


class TestPersistence:
    """Round-trip persistence of the active source across router restarts.

    The persisted-active plumbing is what makes startup resync work:
    after a router restart, we re-register whichever source was
    playing before the restart, rather than coming back idle.  Each
    step of the round-trip (save, load, consume) has historically
    been fiddly — this class pins the contract.
    """

    def _swap_state_file(self, monkeypatch, tmp_path):
        import lib.source_registry as sr
        state_path = tmp_path / "beo-router-state.json"
        monkeypatch.setattr(sr, "STATE_FILE", str(state_path))
        return state_path

    def test_new_instance_with_no_file_has_no_persisted(
        self, monkeypatch, tmp_path
    ):
        self._swap_state_file(monkeypatch, tmp_path)
        reg = SourceRegistry()
        assert reg._persisted_active_id is None
        assert reg.consume_persisted_active() is None

    def test_persist_then_load_round_trip(self, monkeypatch, tmp_path):
        state_path = self._swap_state_file(monkeypatch, tmp_path)

        reg1 = SourceRegistry()
        reg1._active_id = "spotify"
        reg1._persist_active()
        assert state_path.exists()

        # A fresh registry instance reads the file at construction time.
        reg2 = SourceRegistry()
        assert reg2._persisted_active_id == "spotify"

    def test_consume_returns_value_then_clears(
        self, monkeypatch, tmp_path
    ):
        """consume_persisted_active returns the value once, then None —
        that's what prevents startup resync from re-firing on
        every router restart within the same process."""
        self._swap_state_file(monkeypatch, tmp_path)
        reg = SourceRegistry()
        reg._persisted_active_id = "plex"
        assert reg.consume_persisted_active() == "plex"
        assert reg.consume_persisted_active() is None

    def test_persist_handles_no_active(self, monkeypatch, tmp_path):
        """Writing None is fine — it just overwrites the file with
        active_source_id=None, so next startup sees no persisted source."""
        state_path = self._swap_state_file(monkeypatch, tmp_path)
        reg = SourceRegistry()
        reg._active_id = None
        reg._persist_active()

        import json
        data = json.loads(state_path.read_text())
        assert data["active_source_id"] is None

    def test_corrupt_state_file_falls_back_to_none(
        self, monkeypatch, tmp_path
    ):
        """A malformed state file must not crash the registry —
        we just return None and move on."""
        state_path = self._swap_state_file(monkeypatch, tmp_path)
        state_path.write_text("{not json")
        reg = SourceRegistry()
        assert reg._persisted_active_id is None

    def test_unwritable_state_file_is_soft_failure(
        self, monkeypatch, tmp_path, caplog
    ):
        """If the state file location is unwritable, persist_active
        logs a warning but doesn't raise — we don't want a transient
        FS issue to crash the router."""
        import logging
        import lib.source_registry as sr
        # Point STATE_FILE at a path where the parent dir doesn't
        # exist and can't be created by a simple open().
        monkeypatch.setattr(sr, "STATE_FILE", str(tmp_path / "nope" / "nope" / "state.json"))
        reg = SourceRegistry()
        reg._active_id = "spotify"
        with caplog.at_level(logging.WARNING, logger="beo-router"):
            reg._persist_active()  # must not raise
        assert any("persist" in r.message.lower() for r in caplog.records)


class TestAllAvailable:
    """``all_available()`` feeds the UI menu; it must filter by state."""

    def test_returns_non_gone_sources(self):
        reg = SourceRegistry()
        a = reg.create_from_config("spotify", set())
        b = reg.create_from_config("plex", set())
        c = reg.create_from_config("cd", set())
        a._state = "available"
        b._state = "playing"
        c._state = "gone"
        result = reg.all_available()
        ids = {s.id for s in result}
        assert ids == {"spotify", "plex"}

    def test_empty_when_all_gone(self):
        reg = SourceRegistry()
        s = reg.create_from_config("spotify", set())
        s._state = "gone"
        assert reg.all_available() == []
