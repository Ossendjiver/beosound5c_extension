"""Focused tests for lib.player_base: ArtworkCache, action_ts gating,
stale-rejection paths, and monitor suppression timing.

The big surface (play/pause/next/prev delegation) is already covered
indirectly via test_queue.py.  This file targets the bits that have
been historically buggy:

  * ArtworkCache LRU behaviour — previously had an interval leak
    (commit b9c8096) and a size-bound bug.
  * ``_update_action_ts`` — the staleness guard feeding every handler.
  * ``_handle_play`` / ``_handle_stop`` reject-stale paths — this is
    the exact chain the stale-media bug family was fixed in
    (4b34d3c, aac5b60, df5605e).
  * ``seconds_since_command`` — the "is a command recent?" clock used
    by Sonos monitor suppression.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest

from lib.player_base import ArtworkCache, PlayerBase


# ── ArtworkCache ─────────────────────────────────────────────────────


class TestArtworkCache:
    def test_get_miss_returns_none(self):
        c = ArtworkCache(max_size=3)
        assert c.get("https://example.com/a.jpg") is None

    def test_put_then_get_round_trip(self):
        c = ArtworkCache(max_size=3)
        data = {"base64": "AAAA", "size": (300, 300)}
        c.put("https://example.com/a.jpg", data)
        assert c.get("https://example.com/a.jpg") == data

    def test_contains(self):
        c = ArtworkCache(max_size=3)
        c.put("u", {"x": 1})
        assert "u" in c
        assert "missing" not in c

    def test_len(self):
        c = ArtworkCache(max_size=3)
        assert len(c) == 0
        c.put("a", {"x": 1})
        c.put("b", {"x": 2})
        assert len(c) == 2

    def test_eviction_drops_oldest(self):
        c = ArtworkCache(max_size=2)
        c.put("a", {"x": 1})
        c.put("b", {"x": 2})
        c.put("c", {"x": 3})  # evicts "a"
        assert "a" not in c
        assert "b" in c
        assert "c" in c
        assert len(c) == 2

    def test_get_moves_to_end_for_lru(self):
        """After get("a"), "a" should be the most-recent, so "b"
        becomes the LRU and is evicted next."""
        c = ArtworkCache(max_size=2)
        c.put("a", {"x": 1})
        c.put("b", {"x": 2})
        c.get("a")          # touch a — b is now LRU
        c.put("c", {"x": 3})  # evicts b
        assert "a" in c
        assert "b" not in c
        assert "c" in c

    def test_put_duplicate_updates_without_growing(self):
        c = ArtworkCache(max_size=2)
        c.put("a", {"x": 1})
        c.put("a", {"x": 2})
        assert len(c) == 1
        assert c.get("a") == {"x": 2}

    def test_put_duplicate_moves_to_end(self):
        """Re-putting an existing key refreshes its LRU position."""
        c = ArtworkCache(max_size=2)
        c.put("a", {"x": 1})
        c.put("b", {"x": 2})
        c.put("a", {"x": 11})  # touch a
        c.put("c", {"x": 3})   # should evict b, not a
        assert "a" in c
        assert "b" not in c
        assert "c" in c


# ── Minimal concrete PlayerBase subclass for tests ───────────────────


class _FakePlayer(PlayerBase):
    id = "fake"
    name = "Fake"
    port = 8766

    def __init__(self):
        super().__init__()
        self.play_calls: list[dict] = []
        self.pause_calls = 0
        self.stop_calls = 0

    async def play(self, uri=None, url=None, track_uri=None, meta=None,
                   radio=False, track_uris=None):
        self.play_calls.append({
            "uri": uri, "url": url, "track_uri": track_uri,
            "meta": meta, "radio": radio, "track_uris": track_uris,
        })
        return True

    async def pause(self):
        self.pause_calls += 1
        return True

    async def stop(self):
        self.stop_calls += 1
        return True

    async def resume(self):
        return True

    async def next_track(self):
        return True

    async def prev_track(self):
        return True

    async def get_state(self):
        return "playing"

    async def get_track_uri(self):
        return ""

    async def get_capabilities(self):
        return []


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _fake_request(body: dict):
    """Minimal stand-in for an aiohttp Request that carries a JSON body."""
    class _R:
        async def json(self):
            return body
    return _R()


# ── action_ts gating ─────────────────────────────────────────────────


class TestUpdateActionTs:
    def test_raises_watermark(self):
        p = _FakePlayer()
        p._update_action_ts({"action_ts": 100.0})
        assert p._latest_action_ts == 100.0

    def test_only_moves_forward(self):
        p = _FakePlayer()
        p._latest_action_ts = 200.0
        p._update_action_ts({"action_ts": 100.0})
        assert p._latest_action_ts == 200.0

    def test_equal_stays_equal(self):
        p = _FakePlayer()
        p._latest_action_ts = 200.0
        p._update_action_ts({"action_ts": 200.0})
        assert p._latest_action_ts == 200.0

    def test_zero_and_missing_are_noop(self):
        p = _FakePlayer()
        p._latest_action_ts = 200.0
        p._update_action_ts({"action_ts": 0})
        p._update_action_ts({})
        assert p._latest_action_ts == 200.0


# ── _handle_play / _handle_stop stale-rejection ──────────────────────


class TestStaleRejection:
    def test_play_rejected_when_action_ts_older(self):
        p = _FakePlayer()
        p._latest_action_ts = 500.0
        resp = _run(p._handle_play(_fake_request(
            {"action_ts": 100.0, "uri": "spotify:track:xyz"}
        )))
        body = _run(resp.json()) if hasattr(resp, "json") else resp.text
        # Response body is JSON encoded in the aiohttp Response object.
        text = resp.text
        assert "dropped" in text
        assert "stale" in text
        # And play() was NOT called
        assert p.play_calls == []

    def test_play_accepted_when_fresh(self):
        p = _FakePlayer()
        p._latest_action_ts = 100.0
        resp = _run(p._handle_play(_fake_request(
            {"action_ts": 500.0, "uri": "spotify:track:xyz"}
        )))
        assert "ok" in resp.text
        assert p.play_calls[0]["uri"] == "spotify:track:xyz"
        # Watermark advanced
        assert p._latest_action_ts == 500.0

    def test_play_accepted_with_no_action_ts(self):
        p = _FakePlayer()
        p._latest_action_ts = 100.0
        resp = _run(p._handle_play(_fake_request({"uri": "spotify:track:xyz"})))
        assert "ok" in resp.text
        assert len(p.play_calls) == 1
        # Watermark unchanged — no action_ts in request
        assert p._latest_action_ts == 100.0

    def test_stop_rejected_when_action_ts_older(self):
        """Stale stop rejection — prevents a deactivated source from
        killing playback that a newer source already started.  This is
        the exact bug fixed in df5605e."""
        p = _FakePlayer()
        p._latest_action_ts = 500.0
        resp = _run(p._handle_stop(_fake_request({"action_ts": 100.0})))
        assert "dropped" in resp.text
        assert p.stop_calls == 0

    def test_stop_accepted_when_fresh(self):
        p = _FakePlayer()
        p._latest_action_ts = 100.0
        resp = _run(p._handle_stop(_fake_request({"action_ts": 500.0})))
        assert "ok" in resp.text
        assert p.stop_calls == 1

    def test_stop_with_no_action_ts_accepted(self):
        p = _FakePlayer()
        p._latest_action_ts = 100.0
        resp = _run(p._handle_stop(_fake_request({})))
        assert "ok" in resp.text
        assert p.stop_calls == 1


# ── Monitor suppression clock ────────────────────────────────────────


class TestCommandTiming:
    def test_stamp_command_updates_clock(self):
        p = _FakePlayer()
        assert p._last_internal_command == 0.0
        p._stamp_command()
        assert p._last_internal_command > 0

    def test_seconds_since_command_infinity_when_never_stamped(self):
        p = _FakePlayer()
        assert p.seconds_since_command() == float("inf")

    def test_seconds_since_command_reflects_elapsed_time(self):
        """Test the clock's behaviour by patching time.monotonic so the
        elapsed window is deterministic and the test runs in <1ms."""
        p = _FakePlayer()
        fake_now = [1000.0]
        with patch("lib.player_base.time.monotonic",
                   side_effect=lambda: fake_now[0]):
            p._stamp_command()         # sets to 1000.0
            fake_now[0] = 1002.5
            assert p.seconds_since_command() == pytest.approx(2.5)

    def test_handle_play_stamps_command_twice(self):
        """_handle_play stamps before *and* after play() completes.
        The post-stamp is the monitor suppression window — a SoCo
        play() can take 5+ seconds and we don't want the window to
        start until the call returns."""
        p = _FakePlayer()
        stamp_log = []

        def _fake_time():
            stamp_log.append(len(stamp_log) + 1)
            return float(len(stamp_log))

        with patch("lib.player_base.time.monotonic", side_effect=_fake_time):
            _run(p._handle_play(_fake_request({"uri": "spotify:track:x"})))

        # At minimum two stamps recorded; the last one is the post-play stamp.
        assert p._last_internal_command >= 2.0
