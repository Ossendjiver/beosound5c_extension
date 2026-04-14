"""Tests for SourceBase.register() retry + payload shape.

The register call is the handshake every source service performs with
the router on startup, on resync, and on every state change.  It has
a retry loop with exponential back-off and a branch that omits
non-``gone`` fields — both are easy to break silently.

Also covers _resync_media: the "re-post fresh player media, fall back
to cached _last_media" decision that fixed the stale-resync bug.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from lib.source_base import SourceBase, _action_ts_ctx


class _FakeResponse:
    def __init__(self, status=200, json_body=None):
        self.status = status
        self._body = json_body or {}
    async def __aenter__(self):
        return self
    async def __aexit__(self, *exc):
        return None
    async def json(self):
        return self._body


class _RecordingSession:
    """HTTP session mock that records POSTs and can be configured to
    fail the first N calls to exercise retry logic."""

    def __init__(self, fail_first: int = 0, fail_always: bool = False):
        self.post_calls: list = []
        self.get_calls: list = []
        self._fails_remaining = fail_first
        self._fail_always = fail_always
        self._get_response: dict | None = None

    def post(self, url, json=None, timeout=None):
        self.post_calls.append({"url": url, "json": json})
        if self._fail_always:
            raise RuntimeError("simulated router unreachable")
        if self._fails_remaining > 0:
            self._fails_remaining -= 1
            raise RuntimeError("simulated router unreachable (retry)")
        return _FakeResponse(200)

    def get(self, url, timeout=None):
        self.get_calls.append({"url": url})
        return _FakeResponse(200, self._get_response or {})


class _FakeSource(SourceBase):
    id = "spotify"
    name = "Spotify"
    port = 8771
    action_map = {"play": "toggle", "next": "next_track"}


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── register() payload shape ─────────────────────────────────────────


class TestRegisterPayload:
    def test_available_payload_has_all_fields(self):
        src = _FakeSource()
        sess = _RecordingSession()
        src._http_session = sess

        _run(src.register("available"))

        assert len(sess.post_calls) == 1
        payload = sess.post_calls[0]["json"]
        assert payload["id"] == "spotify"
        assert payload["state"] == "available"
        assert payload["name"] == "Spotify"
        assert payload["menu_preset"] == "spotify"
        # Port must match self.port via source_url helper
        assert "8771" in payload["command_url"]
        assert payload["command_url"].endswith("/command")
        assert set(payload["handles"]) == {"play", "next"}
        assert payload["player"] == "local"
        assert payload["manages_queue"] is False

    def test_gone_payload_omits_metadata(self):
        """When a source tells the router it's gone, we don't need to
        re-send name/handles/command_url — the router already has them."""
        src = _FakeSource()
        sess = _RecordingSession()
        src._http_session = sess

        _run(src.register("gone"))

        payload = sess.post_calls[0]["json"]
        assert payload["state"] == "gone"
        assert payload["id"] == "spotify"
        # Only id + state (+ maybe action_ts) — no metadata
        for field in ("name", "command_url", "handles", "player",
                      "menu_preset", "manages_queue"):
            assert field not in payload, f"{field} leaked into gone payload"

    def test_navigate_flag_propagates(self):
        src = _FakeSource()
        sess = _RecordingSession()
        src._http_session = sess
        _run(src.register("playing", navigate=True))
        assert sess.post_calls[0]["json"]["navigate"] is True

    def test_auto_power_flag_propagates(self):
        src = _FakeSource()
        sess = _RecordingSession()
        src._http_session = sess
        _run(src.register("playing", auto_power=True))
        assert sess.post_calls[0]["json"]["auto_power"] is True

    def test_action_ts_from_contextvar(self):
        src = _FakeSource()
        sess = _RecordingSession()
        src._http_session = sess

        async def _with_ctx():
            _action_ts_ctx.set(42.0)
            await src.register("playing")
        _run(_with_ctx())

        assert sess.post_calls[0]["json"]["action_ts"] == 42.0

    def test_action_ts_falls_back_to_instance(self):
        src = _FakeSource()
        sess = _RecordingSession()
        src._http_session = sess
        src._action_ts = 100.0

        async def _no_ctx():
            _action_ts_ctx.set(0.0)  # context var is zero → instance field wins
            await src.register("playing")
        _run(_no_ctx())

        assert sess.post_calls[0]["json"]["action_ts"] == 100.0

    def test_zero_action_ts_omitted(self):
        """If neither contextvar nor instance is set, action_ts must
        not appear in the payload — the router treats missing as
        ``no opinion`` (backwards compatible).

        The ``_reset_action_ts_ctx`` autouse fixture in conftest.py
        ensures the ContextVar starts at 0 for each test.
        """
        src = _FakeSource()
        sess = _RecordingSession()
        src._http_session = sess

        _run(src.register("available"))
        assert "action_ts" not in sess.post_calls[0]["json"]


# ── register() retry loop ────────────────────────────────────────────


class TestRegisterRetry:
    def test_succeeds_on_first_try_no_retry(self):
        src = _FakeSource()
        sess = _RecordingSession()
        src._http_session = sess
        _run(src.register("available", _retries=5))
        assert len(sess.post_calls) == 1

    def test_retries_up_to_limit_on_persistent_failure(self, monkeypatch):
        src = _FakeSource()
        sess = _RecordingSession(fail_always=True)
        src._http_session = sess

        # Short-circuit the exponential backoff sleeps so the test is fast.
        async def _no_sleep(_):
            return None
        monkeypatch.setattr("lib.source_base.asyncio.sleep", _no_sleep)

        _run(src.register("available", _retries=3))
        assert len(sess.post_calls) == 3  # all 3 attempts fired

    def test_recovers_after_transient_failure(self, monkeypatch):
        src = _FakeSource()
        sess = _RecordingSession(fail_first=2)  # 2 fails, 3rd succeeds
        src._http_session = sess

        async def _no_sleep(_):
            return None
        monkeypatch.setattr("lib.source_base.asyncio.sleep", _no_sleep)

        _run(src.register("available", _retries=5))
        assert len(sess.post_calls) == 3  # stopped retrying after success


# ── _resync_media decision tree ─────────────────────────────────────


class _FakeSourceWithMedia(_FakeSource):
    def __init__(self):
        super().__init__()
        self._resync_posts: list = []

    async def post_media_update(self, **kwargs):
        self._resync_posts.append(kwargs)


class TestResyncMedia:
    def test_noop_when_not_playing(self):
        src = _FakeSourceWithMedia()
        src._http_session = _RecordingSession()
        src._registered_state = "available"
        _run(src._resync_media())
        assert src._resync_posts == []

    def test_prefers_fresh_player_media(self):
        """If the player has live media, use that instead of the
        cached _last_media (which may be stale after auto-advance)."""
        src = _FakeSourceWithMedia()
        sess = _RecordingSession()
        sess._get_response = {
            "title": "Fresh Song",
            "artist": "Fresh Artist",
            "album": "Fresh Album",
            "artwork": "fresh-art",
        }
        src._http_session = sess
        src._registered_state = "playing"
        src._last_media = {
            "title": "Stale Song", "artist": "Stale", "album": "",
            "artwork": "",
        }
        _run(src._resync_media())

        assert len(src._resync_posts) == 1
        posted = src._resync_posts[0]
        assert posted["title"] == "Fresh Song"
        assert posted["artist"] == "Fresh Artist"
        assert posted["reason"] == "resync"

    def test_falls_back_to_cached_when_player_empty(self):
        """Player returns empty (source manages its own queue, not
        remote player) → use cached _last_media."""
        src = _FakeSourceWithMedia()
        sess = _RecordingSession()
        sess._get_response = {}   # player has nothing
        src._http_session = sess
        src._registered_state = "playing"
        src._last_media = {
            "title": "Cached Song", "artist": "Cached", "album": "",
            "artwork": "",
        }
        _run(src._resync_media())

        assert len(src._resync_posts) == 1
        assert src._resync_posts[0]["title"] == "Cached Song"

    def test_noop_when_both_empty(self):
        src = _FakeSourceWithMedia()
        sess = _RecordingSession()
        sess._get_response = {}
        src._http_session = sess
        src._registered_state = "playing"
        src._last_media = None
        _run(src._resync_media())
        assert src._resync_posts == []

    def test_preserves_canvas_url_when_track_id_matches(self):
        """Fresh player media doesn't include canvas_url (it's a
        Spotify-specific extension); we must carry the cached one
        across or the Spotify Canvas video disappears on every resync.
        Match is by Spotify track id — only reuse if the player is
        still on the same track we cached for."""
        track_id = "5GHY1DFWKz3Prg2V0Iodqo"
        src = _FakeSourceWithMedia()
        sess = _RecordingSession()
        sess._get_response = {
            "title": "Song", "artist": "", "album": "", "artwork": "",
            "uri": f"spotify:track:{track_id}",
        }
        src._http_session = sess
        src._registered_state = "playing"
        src._last_media = {
            "title": "Song", "artist": "", "album": "", "artwork": "",
            "track_uri": f"spotify:track:{track_id}",
            "canvas_url": "https://canvas.example/abc.mp4",
        }
        _run(src._resync_media())
        assert src._resync_posts[0]["canvas_url"] == "https://canvas.example/abc.mp4"
