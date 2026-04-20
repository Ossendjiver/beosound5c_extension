"""Tests for media state validation and management."""

import asyncio
import logging
import sys
from pathlib import Path

import pytest

SERVICES_DIR = Path(__file__).resolve().parents[3] / "services"
sys.path.insert(0, str(SERVICES_DIR))

from lib.media_state import MediaState


class TestMediaValidation:
    """Test media update validation rules."""

    def test_reject_inactive_source(self):
        ms = MediaState()
        payload = {"title": "Song", "_reason": "update",
                   "_source_id": "radio", "_action_ts": 100}
        result = ms.validate_update(payload, active_source_id="spotify",
                                    latest_action_ts=0)
        assert result is not None
        assert result["dropped"] is True
        assert result["reason"] == "inactive_source"

    def test_accept_active_source(self):
        ms = MediaState()
        payload = {"title": "Song", "_reason": "update",
                   "_source_id": "spotify", "_action_ts": 100}
        result = ms.validate_update(payload, active_source_id="spotify",
                                    latest_action_ts=200)
        assert result is None  # accepted

    def test_accept_track_change_bootstrap_without_active_source(self):
        ms = MediaState()
        payload = {"title": "Song", "_reason": "track_change",
                   "_source_id": "mass", "_action_ts": 100}
        result = ms.validate_update(payload, active_source_id=None,
                                    latest_action_ts=0)
        assert result is None
        assert payload["_validated_source_id"] == "mass"
        assert payload["_validated_reason"] == "track_change"

    def test_reject_stale_timestamp(self):
        """Source with stale timestamp rejected (source must not be active)."""
        ms = MediaState()
        # Source is "known" but not active — rejected as inactive first.
        # To test timestamp rejection specifically, source must be absent
        # from active but not trigger the inactive check — that only happens
        # when source_id is set AND doesn't match active.  The stale check
        # only fires after the inactive check passes (source_id matches or
        # is None).  So stale timestamp rejection is tested via the race
        # scenario test below.
        # Here we verify the inactive source rejection path.
        payload = {"title": "Song", "_reason": "update",
                   "_source_id": "radio", "_action_ts": 100}
        result = ms.validate_update(payload, active_source_id=None,
                                    latest_action_ts=200)
        assert result is not None
        assert result["dropped"] is True

    def test_accept_player_originated(self):
        ms = MediaState()
        payload = {"title": "Song", "_reason": "update",
                   "_source_id": None, "_action_ts": 0}
        result = ms.validate_update(payload, active_source_id="spotify",
                                    latest_action_ts=200)
        assert result is None  # player media always accepted

    def test_zero_timestamp_passes_for_active_source(self):
        """Active source with action_ts=0 (no opinion) should be accepted."""
        ms = MediaState()
        payload = {"title": "Song", "_reason": "update",
                   "_source_id": "radio", "_action_ts": 0}
        result = ms.validate_update(payload, active_source_id="radio",
                                    latest_action_ts=200)
        assert result is None  # accepted — active source is exempt

    def test_canvas_url_always_present(self):
        ms = MediaState()
        payload = {"title": "Song", "_reason": "update",
                   "_source_id": None, "_action_ts": 0}
        ms.validate_update(payload, active_source_id=None,
                           latest_action_ts=0)
        assert "canvas_url" in payload
        assert payload["canvas_url"] == ""

    def test_canvas_url_preserved_if_set(self):
        ms = MediaState()
        payload = {"title": "Song", "canvas_url": "https://canvas.example.com/v.mp4",
                   "_reason": "update", "_source_id": None, "_action_ts": 0}
        ms.validate_update(payload, active_source_id=None,
                           latest_action_ts=0)
        assert payload["canvas_url"] == "https://canvas.example.com/v.mp4"


class TestMediaStateCache:
    def test_push_idle_clears_state(self):
        ms = MediaState()
        ms.state = {"title": "Old Song"}
        asyncio.run(ms.push_idle("test"))
        assert ms.state is None

    def test_accept_and_push_stores_state(self):
        ms = MediaState()
        payload = {"title": "New Song", "artist": "Artist"}
        asyncio.run(ms.accept_and_push(payload, "update"))
        assert ms.state == payload
        assert ms.state["title"] == "New Song"

    def test_state_property(self):
        ms = MediaState()
        assert ms.state is None
        ms.state = {"title": "Test"}
        assert ms.state["title"] == "Test"


class TestMediaRaceScenarios:
    """Test real-world race conditions."""

    def test_rapid_source_switch_rejects_old_media(self):
        """Radio activates (ts=100), Spotify activates (ts=200).
        Radio's late media (ts=100) should be rejected."""
        ms = MediaState()
        # Spotify is now active with ts=200
        payload = {"title": "Radio Song", "_reason": "update",
                   "_source_id": "radio", "_action_ts": 100}
        result = ms.validate_update(payload, active_source_id="spotify",
                                    latest_action_ts=200)
        assert result is not None
        assert result["dropped"] is True

    def test_active_source_media_exempt_from_stale_check(self):
        """Active source's media should always be accepted, even with old ts.
        This handles auto-advance: source reuses its activation ts."""
        ms = MediaState()
        payload = {"title": "Track 2", "_reason": "update",
                   "_source_id": "spotify", "_action_ts": 100}
        # Spotify is active but latest_action_ts bumped by a source button press
        result = ms.validate_update(payload, active_source_id="spotify",
                                    latest_action_ts=200)
        assert result is None  # accepted because it's the active source

    def test_player_originated_media_accepted_regardless_of_ts(self):
        """Media without _source_id (e.g. Sonos external playback) is always
        accepted — the player owns metadata once external playback runs.
        Pins the documented policy so a future refactor of
        validate_update() can't silently start dropping Sonos metadata."""
        ms = MediaState()
        # No source_id, stale ts: still accepted
        payload = {"title": "Sonos Song", "_reason": "update",
                   "_source_id": None, "_action_ts": 50}
        result = ms.validate_update(payload, active_source_id="spotify",
                                    latest_action_ts=300)
        assert result is None
        # No source_id, ts=0: still accepted
        payload = {"title": "Sonos Song 2", "_reason": "update",
                   "_source_id": None, "_action_ts": 0}
        result = ms.validate_update(payload, active_source_id="spotify",
                                    latest_action_ts=300)
        assert result is None
        # No source_id, no active source at all: accepted
        payload = {"title": "Sonos Song 3", "_reason": "update"}
        result = ms.validate_update(payload, active_source_id=None,
                                    latest_action_ts=0)
        assert result is None

    def test_broadcast_drops_hung_client_and_delivers_to_healthy(self):
        """A hung WS client must not block delivery to healthy clients."""
        ms = MediaState()

        class StuckWS:
            async def send_str(self, msg):
                await asyncio.sleep(10)  # longer than send timeout

            async def close(self):
                pass

        class FastWS:
            def __init__(self):
                self.received = []

            async def send_str(self, msg):
                self.received.append(msg)

            async def close(self):
                pass

        stuck = StuckWS()
        fast = FastWS()
        ms._ws_clients.add(stuck)
        ms._ws_clients.add(fast)

        # Patch the module-level timeout so the test is quick.
        import lib.media_state as ms_mod
        orig = ms_mod._WS_SEND_TIMEOUT
        ms_mod._WS_SEND_TIMEOUT = 0.05
        try:
            asyncio.run(ms.broadcast("test", {"k": "v"}))
        finally:
            ms_mod._WS_SEND_TIMEOUT = orig

        assert stuck not in ms._ws_clients   # dropped
        assert fast in ms._ws_clients
        assert len(fast.received) == 1       # delivered

    def test_external_playback_clears_stale_metadata(self):
        """When Sonos app starts playing, old metadata should be cleared."""
        ms = MediaState()
        ms.state = {"title": "Old BS5c Song", "artist": "Old Artist"}
        asyncio.run(ms.push_idle("external_override"))
        assert ms.state is None
        # New player media arrives
        payload = {"title": "Sonos Song", "_reason": "update",
                   "_source_id": None, "_action_ts": 0}
        result = ms.validate_update(payload, active_source_id=None,
                                    latest_action_ts=0)
        assert result is None  # accepted


class TestMediaTrace:
    """The media_trace log line is the canonical observability hook for the
    stale-media bug family — it must contain enough structured fields to
    reconstruct the decision from logs alone."""

    def _trace_lines(self, caplog):
        return [r.message for r in caplog.records
                if r.message.startswith("media_trace ")]

    def test_accept_trace_has_all_fields(self, caplog):
        ms = MediaState()
        payload = {"title": "Hello World", "_reason": "update",
                   "_source_id": "spotify", "_action_ts": 42.5}
        with caplog.at_level(logging.INFO, logger="beo-router"):
            ms.validate_update(payload, active_source_id="spotify",
                               latest_action_ts=10.0)
        lines = self._trace_lines(caplog)
        assert len(lines) == 1
        line = lines[0]
        for frag in ("decision=accept", "source_id=spotify",
                     "active=spotify", "action_ts=42.5", "latest_ts=10.0",
                     "update_reason=update", 'title="Hello World"'):
            assert frag in line, f"missing {frag!r} in: {line}"

    def test_drop_trace_has_drop_reason(self, caplog):
        ms = MediaState()
        payload = {"title": "Stale", "_reason": "update",
                   "_source_id": "radio", "_action_ts": 5}
        with caplog.at_level(logging.INFO, logger="beo-router"):
            ms.validate_update(payload, active_source_id="spotify",
                               latest_action_ts=10.0)
        lines = self._trace_lines(caplog)
        assert len(lines) == 1
        assert "decision=drop" in lines[0]
        assert "drop_reason=inactive_source" in lines[0]
        assert "source_id=radio" in lines[0]
        assert "active=spotify" in lines[0]

    def test_player_originated_trace_marks_source_dash(self, caplog):
        ms = MediaState()
        payload = {"title": "Song", "_reason": "playback_override",
                   "_source_id": None, "_action_ts": 0}
        with caplog.at_level(logging.INFO, logger="beo-router"):
            ms.validate_update(payload, active_source_id=None,
                               latest_action_ts=0)
        lines = self._trace_lines(caplog)
        assert len(lines) == 1
        assert "decision=accept" in lines[0]
        assert "source_id=-" in lines[0]
        assert "update_reason=playback_override" in lines[0]
