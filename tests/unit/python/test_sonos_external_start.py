"""Regression tests for Sonos external playback start.

When the user starts playback from the Sonos app (no recent BS5c command),
the monitor must:

  1. Eagerly broadcast fresh media metadata BEFORE the UI navigates,
     so the playing view doesn't render stale/empty mediaInfo.
  2. Clear any leftover broadcast suppression flags that would otherwise
     swallow that broadcast (``_suppress_until_track`` left over from a
     prior pre-broadcast that never matched, or ``_last_play_was_radio``
     from a previous radio play).
  3. Trigger wake (which navigates the UI to the playing view) AFTER
     the media broadcast lands, so router cache and live update both
     reach the UI in the right order.
  4. Notify the router of the playback override so transport commands
     route directly to the player.

These assertions were the gap that let three Kitchen bugs through:
  - Sonos external start didn't auto-show artwork
  - Manual nav to playing view showed empty artwork
  - Selecting a (named) "Canvas" playlist left immersive mode blank
"""

from __future__ import annotations

import asyncio
import sys
import time
import types
from unittest.mock import AsyncMock, MagicMock

import pytest


def _install_fake_soco():
    """Install a stub ``soco`` package in sys.modules so ``players.sonos``
    can be imported without the real SoCo library.

    The real tests/requirements.txt installs soco in CI, but this makes
    the test usable in any venv (and avoids pulling a network lib for a
    unit test that never touches the network)."""
    if "soco" in sys.modules and getattr(sys.modules["soco"], "_beo_stub", False):
        return
    fake = types.ModuleType("soco")
    fake._beo_stub = True
    fake.SoCo = MagicMock(return_value=MagicMock())

    plugins = types.ModuleType("soco.plugins")
    sharelink = types.ModuleType("soco.plugins.sharelink")

    class _ShareLinkPlugin:  # pragma: no cover - never instantiated in these tests
        def __init__(self, *a, **k): pass

    class _AppleMusicShare:
        def canonical_uri(self, uri): return None

    sharelink.ShareLinkPlugin = _ShareLinkPlugin
    sharelink.AppleMusicShare = _AppleMusicShare
    plugins.sharelink = sharelink

    data_structures = types.ModuleType("soco.data_structures")
    data_structures.DidlMusicTrack = MagicMock()
    data_structures.to_didl_string = MagicMock(return_value="")

    sys.modules["soco"] = fake
    sys.modules["soco.plugins"] = plugins
    sys.modules["soco.plugins.sharelink"] = sharelink
    sys.modules["soco.data_structures"] = data_structures


@pytest.fixture
def sonos_player():
    """Construct a MediaServer with all SoCo network calls stubbed."""
    _install_fake_soco()
    from players.sonos import MediaServer
    p = MediaServer()

    # Replace methods that would hit the network or other services.
    p.fetch_media_data = AsyncMock(return_value={
        "title": "Eager Track",
        "artist": "Eager Artist",
        "album": "Eager Album",
        "artwork": "http://x/art.jpg",
        "state": "playing",
    })
    p.broadcast_media_update = AsyncMock()
    p.trigger_wake = AsyncMock()
    p.notify_router_playback_override = AsyncMock()
    return p


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class TestExternalStart:
    def test_call_order_override_broadcast_wake(self, sonos_player):
        """External path: clear_active_source → broadcast → wake.

        This exact ordering is the fix for the idle-push race where
        clear_active_source's push_idle was wiping MediaState._state
        right after the eager broadcast landed, leaving the UI with
        empty mediaInfo after auto-navigating to the playing view.

        The override must run first (with push_idle=False), then the
        broadcast overwrites router state with the real track, and
        only then does trigger_wake navigate the UI.
        """
        p = sonos_player
        p._last_internal_command = time.monotonic() - 10.0
        p._current_playback_state = "stopped"

        order = []
        p.notify_router_playback_override.side_effect = \
            lambda *a, **k: order.append("override")
        p.broadcast_media_update.side_effect = \
            lambda *a, **k: order.append("broadcast")
        p.trigger_wake.side_effect = lambda *a, **k: order.append("wake")

        _run(p._on_playback_started())

        assert p.fetch_media_data.await_count == 1
        assert p.broadcast_media_update.await_count == 1
        assert p.trigger_wake.await_count == 1
        assert p.notify_router_playback_override.await_count == 1
        assert order == ["override", "broadcast", "wake"], (
            f"expected override→broadcast→wake, got {order}")

    def test_override_called_with_push_idle_false(self, sonos_player):
        """The whole point of the idle-push plumbing — if this kwarg
        gets dropped, the router will broadcast an idle media_update
        that wipes _state, and the original Kitchen bug comes back."""
        p = sonos_player
        p._last_internal_command = time.monotonic() - 10.0
        p._current_playback_state = "stopped"

        _run(p._on_playback_started())

        _, kwargs = p.notify_router_playback_override.call_args
        assert kwargs.get("push_idle") is False, (
            f"override must be called with push_idle=False, got {kwargs}")
        assert kwargs.get("force") is True

    def test_clears_suppression_flags(self, sonos_player):
        """Stale suppression flags must not be allowed to swallow the
        eager broadcast — this is the exact path the original bug took
        when ``_last_play_was_radio`` was still True from a previous
        radio play."""
        p = sonos_player
        p._suppress_until_track = "stale123"
        p._suppress_set_time = time.monotonic()
        p._last_play_was_radio = True
        p._current_playback_state = "stopped"

        _run(p._on_playback_started())

        assert p._suppress_until_track is None
        assert p._last_play_was_radio is False

    def test_internal_start_skips_playback_override(self, sonos_player):
        """If a BS5c command just fired, this is not external — don't
        clear active source."""
        p = sonos_player
        p._last_internal_command = time.monotonic()  # just now
        p._current_playback_state = "stopped"

        _run(p._on_playback_started())

        assert p.notify_router_playback_override.await_count == 0
        # But the broadcast and wake still fire — the UI must still
        # render fresh metadata even for internal starts.
        assert p.broadcast_media_update.await_count == 1
        assert p.trigger_wake.await_count == 1

    def test_broadcast_uses_track_change_reason(self, sonos_player):
        """Reason matters — handleMediaUpdate in the UI uses
        ``track_change`` to reset canvas_url and other per-track state."""
        p = sonos_player
        p._current_playback_state = "stopped"

        _run(p._on_playback_started())

        args, kwargs = p.broadcast_media_update.call_args
        reason = kwargs.get("reason") or (args[1] if len(args) > 1 else None)
        assert reason == "track_change", (
            f"expected track_change, got {reason!r}")

    def test_external_track_change_uses_push_idle_false(self, sonos_player):
        """Regression: external track change while already playing.

        The Kitchen bug: user skips to the next song in the Sonos app.
        Sonos monitor detects track_change → broadcasts fresh media
        (title=Semester) → calls notify_router_playback_override. If
        push_idle defaults to True, the router clears active source AND
        broadcasts an idle media_update that wipes the fresh metadata
        we just pushed, leaving the UI with artwork but empty
        title/artist/album.
        """
        p = sonos_player
        _run(p._on_external_track_change())

        assert p.notify_router_playback_override.await_count == 1
        _, kwargs = p.notify_router_playback_override.call_args
        assert kwargs.get("push_idle") is False, (
            f"external track change must use push_idle=False, got {kwargs}")
        assert kwargs.get("force") is True

    def test_fetch_failure_does_not_block_wake(self, sonos_player):
        """If fetch_media_data raises (network blip), we still wake the
        UI — better to navigate to playing view with stale info than
        to leave the user staring at the menu."""
        p = sonos_player
        p._current_playback_state = "stopped"
        p.fetch_media_data.side_effect = RuntimeError("transient")

        _run(p._on_playback_started())

        assert p.broadcast_media_update.await_count == 0
        assert p.trigger_wake.await_count == 1
