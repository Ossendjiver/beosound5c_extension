"""Tests for source registry state machine transitions."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

SERVICES_DIR = Path(__file__).resolve().parents[3] / "services"
sys.path.insert(0, str(SERVICES_DIR))

from lib.source_registry import Source, SourceRegistry, VALID_TRANSITIONS


def make_router_mock():
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


class TestTransitionTable:
    """VALID_TRANSITIONS defines exactly which state moves are legal."""

    def test_gone_transitions(self):
        # gone→playing and gone→paused are allowed for router-restart
        # resync: a source mid-playback when the router restarts must
        # be able to re-register directly into its current state.
        assert VALID_TRANSITIONS["gone"] == {"available", "playing", "paused"}

    def test_available_transitions(self):
        assert VALID_TRANSITIONS["available"] == {"available", "playing", "paused", "gone"}

    def test_playing_transitions(self):
        assert VALID_TRANSITIONS["playing"] == {"playing", "paused", "available", "gone"}

    def test_paused_transitions(self):
        assert VALID_TRANSITIONS["paused"] == {"playing", "paused", "available", "gone"}


class TestValidateTransition:

    def test_valid_gone_to_available(self):
        assert SourceRegistry._validate_transition("gone", "available") is True

    def test_valid_available_to_playing(self):
        assert SourceRegistry._validate_transition("available", "playing") is True

    def test_valid_playing_to_paused(self):
        assert SourceRegistry._validate_transition("playing", "paused") is True

    def test_valid_self_transition_playing(self):
        assert SourceRegistry._validate_transition("playing", "playing") is True

    def test_valid_self_transition_available(self):
        assert SourceRegistry._validate_transition("available", "available") is True

    def test_valid_gone_to_playing_resync(self):
        # Router restart: source was mid-playback, re-registers directly.
        assert SourceRegistry._validate_transition("gone", "playing") is True

    def test_valid_gone_to_paused_resync(self):
        assert SourceRegistry._validate_transition("gone", "paused") is True

    def test_invalid_gone_to_gone(self):
        assert SourceRegistry._validate_transition("gone", "gone") is False

    def test_invalid_unknown_state(self):
        assert SourceRegistry._validate_transition("unknown", "available") is False


class TestInvalidTransitionRejection:
    """update() must reject invalid transitions and return a rejection dict."""

    def test_gone_to_playing_accepted_as_resync(self):
        """Router-restart resync: a source that was mid-playback when
        the router restarted re-registers directly into ``playing``.
        The registry must accept it (previously this was silently
        rejected, leaving the source invisible to the router until it
        paused back to available — which never happens on its own)."""
        reg = SourceRegistry()
        router = make_router_mock()
        result = asyncio.run(reg.update("radio", "playing", router,
                                        name="Radio", command_url="http://localhost:8779/command",
                                        action_ts=100))
        assert "rejected" not in result
        assert reg.active_id == "radio"
        assert "radio" in reg._sources
        assert reg._sources["radio"].state == "playing"
        # The menu_item broadcast must still fire for the new source,
        # even though the first state is not "available".
        assert "add_menu_item" in result.get("actions", [])

    def test_gone_to_paused_accepted_as_resync(self):
        reg = SourceRegistry()
        router = make_router_mock()
        result = asyncio.run(reg.update("cd", "paused", router,
                                        name="CD", command_url="http://localhost:8769/command"))
        assert "rejected" not in result
        assert reg._sources["cd"].state == "paused"

    def test_gone_to_gone_rejected(self):
        """Re-unregistering an already-gone source is a no-op rejection."""
        reg = SourceRegistry()
        router = make_router_mock()
        result = asyncio.run(reg.update("cd", "gone", router))
        assert result.get("rejected") == "invalid_transition"

    def test_valid_sequence_not_rejected(self):
        """Full lifecycle: gone -> available -> playing -> available -> gone."""
        reg = SourceRegistry()
        router = make_router_mock()
        r1 = asyncio.run(reg.update("cd", "available", router,
                                     name="CD", command_url="http://localhost:8769/command"))
        assert "rejected" not in r1

        r2 = asyncio.run(reg.update("cd", "playing", router, action_ts=100))
        assert "rejected" not in r2
        assert reg.active_id == "cd"

        r3 = asyncio.run(reg.update("cd", "available", router))
        assert "rejected" not in r3
        assert reg.active_id is None

        r4 = asyncio.run(reg.update("cd", "gone", router))
        assert "rejected" not in r4


class TestAtomicSourceSwitch:
    """Source switching must await old source stop."""

    def test_switch_awaits_old_source_stop(self):
        """When activating a new source, the old source's stop must be awaited."""
        reg = SourceRegistry()
        router = make_router_mock()
        asyncio.run(reg.update("cd", "available", router,
                                name="CD", command_url="http://localhost:8769/command"))
        asyncio.run(reg.update("spotify", "available", router,
                                name="Spotify", command_url="http://localhost:8771/command"))
        asyncio.run(reg.update("cd", "playing", router, action_ts=100))
        asyncio.run(reg.update("spotify", "playing", router, action_ts=200))

        assert reg.active_id == "spotify"
        # _forward_to_source was called (to stop cd) and awaited (not just fired)
        router._forward_to_source.assert_called_once()
        # Verify it was the CD source that was stopped
        call_args = router._forward_to_source.call_args
        assert call_args[0][0].id == "cd"
        assert call_args[0][1]["action"] == "stop"

    def test_switch_handles_timeout_gracefully(self):
        """If old source doesn't respond within timeout, activation proceeds."""
        reg = SourceRegistry()
        router = make_router_mock()

        async def slow_forward(*args, **kwargs):
            await asyncio.sleep(10)  # much longer than 3s timeout

        router._forward_to_source = slow_forward

        asyncio.run(reg.update("cd", "available", router,
                                name="CD", command_url="http://localhost:8769/command"))
        asyncio.run(reg.update("spotify", "available", router,
                                name="Spotify", command_url="http://localhost:8771/command"))
        asyncio.run(reg.update("cd", "playing", router, action_ts=100))
        # Should not hang — proceeds after timeout
        asyncio.run(reg.update("spotify", "playing", router, action_ts=200))
        assert reg.active_id == "spotify"


class TestSourceStateReadOnly:
    """The ``Source.state`` property must be read-only.

    Historical bugs (84f9bb3, aac5b60, df5605e, 9ef9492) were caused by
    direct ``source.state = 'x'`` writes that bypassed VALID_TRANSITIONS.
    The property exposes only a getter — any external mutation now
    raises AttributeError at runtime.
    """

    def test_direct_state_assignment_raises(self):
        source = Source("spotify", handles=set())
        with pytest.raises(AttributeError):
            source.state = "playing"

    def test_state_reads_return_current(self):
        source = Source("spotify", handles=set())
        assert source.state == "gone"
        # Internal mutation path still works (SourceRegistry uses this).
        source._state = "available"
        assert source.state == "available"

    def test_slots_block_ad_hoc_attribute(self):
        """__slots__ prevents accidental typo-attributes like
        ``source.stste = "playing"`` from silently doing nothing."""
        source = Source("spotify", handles=set())
        with pytest.raises(AttributeError):
            source.stste = "playing"  # intentional typo


class TestRestorePersistedActive:
    """Startup-resync promotion path — replaces the old inline
    ``source.state = 'available'`` write in router._probe_running_sources."""

    def test_demotes_others_and_promotes_persisted(self):
        reg = SourceRegistry()
        router = make_router_mock()

        # Two sources both resynced as playing.
        spotify = Source("spotify", handles=set())
        plex = Source("plex", handles=set())
        spotify._state = "playing"
        plex._state = "playing"
        spotify.name = "Spotify"
        plex.name = "Plex"
        spotify.command_url = "http://localhost:8771/command"
        plex.command_url = "http://localhost:8778/command"
        reg._sources["spotify"] = spotify
        reg._sources["plex"] = plex

        restored = asyncio.run(reg.restore_persisted_active(
            "spotify", ["spotify", "plex"], router,
        ))
        assert restored is True
        assert reg.active_id == "spotify"
        assert spotify.state == "playing"
        assert plex.state == "available"
        # UI received a source_change broadcast
        router.media.broadcast.assert_called()

    def test_noop_when_persisted_not_in_resynced(self):
        reg = SourceRegistry()
        router = make_router_mock()
        restored = asyncio.run(reg.restore_persisted_active(
            "spotify", ["plex"], router,
        ))
        assert restored is False
        assert reg.active_id is None

    def test_skips_demotion_for_invalid_transition(self):
        reg = SourceRegistry()
        router = make_router_mock()
        spotify = Source("spotify", handles=set())
        plex = Source("plex", handles=set())
        spotify._state = "playing"
        plex._state = "gone"   # gone -> available is valid, not this case
        spotify.name = "Spotify"
        reg._sources["spotify"] = spotify
        reg._sources["plex"] = plex
        # plex is 'gone', restore skips it (only playing/paused get demoted)
        restored = asyncio.run(reg.restore_persisted_active(
            "spotify", ["spotify", "plex"], router,
        ))
        assert restored is True
        assert plex.state == "gone"
