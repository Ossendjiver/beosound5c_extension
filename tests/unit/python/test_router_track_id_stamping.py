"""Tests for ``EventRouter._handle_media_post`` track_id stamping.

When a player broadcasts media with a ``_track_uri`` hint, the router
must extract the canonical Spotify track id from it and stamp it on
the outgoing payload as ``track_id``. The UI's canvas-panel uses
this id at render time to refuse showing a canvas that was fetched
for a different track than the one currently playing — without it,
a stale canvas can flash up briefly between a track change and the
next media_update arriving with a fresh canvas_url.

These tests lock the contract:
  * Sonos-wrapped URIs are normalized
  * Canonical URIs pass through
  * Non-Spotify URIs (radio streams, etc.) leave track_id absent
  * track_id is preserved through accept_and_push to MediaState._state
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import router as router_module


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _FakeRequest:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


VALID_ID = "01RdEXps15f3VmQMV6OuTM"
SONOS_URI = f"x-sonos-spotify:spotify%3atrack%3a{VALID_ID}?sid=9&flags=8232&sn=9"
CANONICAL = f"spotify:track:{VALID_ID}"


def _make_router():
    """A real EventRouter — we want the real _handle_media_post path,
    but with the canvas-injection branch stubbed so it doesn't try to
    spawn HTTP fetches in the test."""
    r = router_module.EventRouter()
    r._spawn = lambda coro, name=None: None  # absorb canvas_inject spawns
    return r


class TestTrackIdStamping:
    def test_sonos_uri_hint_normalized(self):
        r = _make_router()
        payload = {
            "title": "Tusen spänn",
            "artist": "Tjuvjakt",
            "state": "playing",
            "_reason": "track_change",
            "_track_uri": SONOS_URI,
        }
        _run(r._handle_media_post(_FakeRequest(payload)))
        assert r.media.state is not None
        assert r.media.state["track_id"] == VALID_ID

    def test_canonical_uri_hint_passthrough(self):
        r = _make_router()
        payload = {
            "title": "Foo", "artist": "Bar", "state": "playing",
            "_reason": "update",
            "_track_uri": CANONICAL,
        }
        _run(r._handle_media_post(_FakeRequest(payload)))
        assert r.media.state["track_id"] == VALID_ID

    def test_non_spotify_uri_no_track_id(self):
        """A radio stream URI — no spotify track id, so track_id
        must NOT be stamped (UI's canvas check then has no opinion)."""
        r = _make_router()
        payload = {
            "title": "Some Radio", "artist": "Live", "state": "playing",
            "_reason": "track_change",
            "_track_uri": "x-rincon-mp3radio://example.com/stream.mp3",
        }
        _run(r._handle_media_post(_FakeRequest(payload)))
        assert "track_id" not in r.media.state

    def test_no_track_uri_hint_no_track_id(self):
        r = _make_router()
        payload = {
            "title": "Foo", "artist": "Bar", "state": "playing",
            "_reason": "update",
        }
        _run(r._handle_media_post(_FakeRequest(payload)))
        assert "track_id" not in r.media.state

    def test_falls_back_to_uri_field_when_hint_missing(self):
        """First eager broadcast after external Sonos start: the
        monitor loop hasn't committed ``_current_track_id`` yet, so
        ``get_track_uri()`` returns empty and the ``_track_uri`` hint
        is absent from the payload. But ``fetch_media_data`` always
        populates the ``uri`` field directly from track_info — fall
        back to that so the very first broadcast still carries a
        track_id. Without this fallback, the UI's canvas-vs-artwork
        cycle has nothing to match against on the first frame and
        falls through to "no opinion" mode."""
        r = _make_router()
        payload = {
            "title": "Tusen spänn", "artist": "Tjuvjakt",
            "state": "playing",
            "uri": SONOS_URI,
            "_reason": "track_change",
            # No _track_uri — simulating the eager-broadcast race
        }
        _run(r._handle_media_post(_FakeRequest(payload)))
        assert r.media.state["track_id"] == VALID_ID

    def test_hint_takes_precedence_over_uri_field(self):
        """When both are present, the hint wins — it's stamped from
        the player's most recent get_track_uri() call, while the
        ``uri`` field is whatever fetch_media_data captured at the
        time of the broadcast (could be slightly stale)."""
        r = _make_router()
        # Use two different valid ids to detect which one wins
        hint_id = "01RdEXps15f3VmQMV6OuTM"
        uri_field_id = "2epbL7s3RFV81K5UhTgZje"
        payload = {
            "title": "T", "artist": "A", "state": "playing",
            "_reason": "track_change",
            "_track_uri": f"spotify:track:{hint_id}",
            "uri": f"x-sonos-spotify:spotify%3atrack%3a{uri_field_id}?sid=9",
        }
        _run(r._handle_media_post(_FakeRequest(payload)))
        assert r.media.state["track_id"] == hint_id

    def test_track_id_persists_to_get_endpoint(self):
        """End-to-end: GET /router/media must echo the stamped track_id
        so a UI client that resyncs on view-entry sees it without
        needing a fresh broadcast."""
        r = _make_router()
        payload = {
            "title": "T", "artist": "A", "state": "playing",
            "_reason": "track_change",
            "_track_uri": SONOS_URI,
        }
        _run(r._handle_media_post(_FakeRequest(payload)))
        resp = _run(r._handle_media_get(_FakeRequest({})))
        body = json.loads(resp.text)
        assert body["track_id"] == VALID_ID

    def test_internal_fields_stripped(self):
        """The internal ``_track_uri`` hint must NOT leak into the
        cached/broadcast payload — only the normalized ``track_id``
        should reach the UI."""
        r = _make_router()
        payload = {
            "title": "T", "artist": "A", "state": "playing",
            "_reason": "track_change",
            "_track_uri": SONOS_URI,
        }
        _run(r._handle_media_post(_FakeRequest(payload)))
        assert "_track_uri" not in r.media.state
        assert "_validated_source_id" not in r.media.state
        assert "_validated_reason" not in r.media.state
