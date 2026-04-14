"""Tests for ``SpotifyService._handle_canvas`` (GET /canvas).

Background: this endpoint is what the router calls when a player
broadcasts media (e.g. external Sonos start). Before the fix, the
URL builder in ``lib.endpoints.spotify_canvas_url`` shipped
``?track_id=<id>`` while the handler read ``?uri=…``, so canvas
lookups for player-originated broadcasts always returned an empty
string and Spotify Canvas videos never appeared in immersive mode
on Sonos-backed devices.

These tests lock the contract:
  * the handler MUST accept the ``track_id`` query param
  * inputs in any URI shape (Sonos-wrapped, canonical, bare id)
    MUST be normalized to ``spotify:track:<id>`` before lookup
  * unrecognised inputs return an empty canvas_url (never crash)
  * 401/lookup errors return an empty canvas_url
"""

from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _build_service():
    """Build a SpotifyService with mocked deps — same pattern as
    test_canvas.py::TestSpotifyCanvasBroadcast._build_service."""
    with patch.dict(os.environ, {"BS5C_CONFIG_DIR": "/tmp"}):
        with patch("lib.config.cfg", return_value=None):
            with patch("lib.source_base.SourceBase.__init__", return_value=None):
                from sources.spotify.service import SpotifyService
                svc = SpotifyService.__new__(SpotifyService)
                svc.id = "spotify"
                svc._canvas = MagicMock()
                svc._canvas.configured = True
                svc._canvas.get_canvas_url = AsyncMock(return_value=None)
                svc._cors_headers = lambda: {}
                return svc


class _FakeRequest:
    def __init__(self, query):
        self.query = query


def _body(resp):
    return json.loads(resp.text)


VALID_ID = "01RdEXps15f3VmQMV6OuTM"
SONOS_URI = f"x-sonos-spotify:spotify%3atrack%3a{VALID_ID}?sid=9&flags=8232&sn=9"
CANONICAL = f"spotify:track:{VALID_ID}"
CANVAS_URL = "https://canvaz.scdn.co/upload/licensor/abc123"


class TestCanvasEndpoint:
    def test_track_id_query_param_with_bare_id(self):
        """The canonical ``?track_id=<bare id>`` shape — what
        ``lib.endpoints.spotify_canvas_url`` builds. This was the
        path that was silently broken before the fix."""
        svc = _build_service()
        svc._canvas.get_canvas_url = AsyncMock(return_value=CANVAS_URL)
        resp = _run(svc._handle_canvas(_FakeRequest({"track_id": VALID_ID})))
        assert resp.status == 200
        assert _body(resp)["canvas_url"] == CANVAS_URL
        # Lookup must be called with the canonical form
        svc._canvas.get_canvas_url.assert_awaited_once_with(CANONICAL)

    def test_uri_query_param_canonical(self):
        """Legacy ``?uri=spotify:track:<id>`` shape still works."""
        svc = _build_service()
        svc._canvas.get_canvas_url = AsyncMock(return_value=CANVAS_URL)
        resp = _run(svc._handle_canvas(_FakeRequest({"uri": CANONICAL})))
        assert _body(resp)["canvas_url"] == CANVAS_URL
        svc._canvas.get_canvas_url.assert_awaited_once_with(CANONICAL)

    def test_uri_query_param_sonos_wrapped(self):
        """A Sonos-wrapped, URL-encoded URI must normalize to canonical."""
        svc = _build_service()
        svc._canvas.get_canvas_url = AsyncMock(return_value=CANVAS_URL)
        resp = _run(svc._handle_canvas(_FakeRequest({"uri": SONOS_URI})))
        assert _body(resp)["canvas_url"] == CANVAS_URL
        svc._canvas.get_canvas_url.assert_awaited_once_with(CANONICAL)

    def test_track_id_with_sonos_wrapped(self):
        """If the router accidentally passes a wrapped URI under
        ``?track_id=`` (instead of the bare id), normalization must
        still salvage it. Defensive behaviour — no caller does this
        intentionally but the cost is one regex."""
        svc = _build_service()
        svc._canvas.get_canvas_url = AsyncMock(return_value=CANVAS_URL)
        resp = _run(svc._handle_canvas(_FakeRequest({"track_id": SONOS_URI})))
        assert _body(resp)["canvas_url"] == CANVAS_URL

    def test_empty_query_returns_empty(self):
        svc = _build_service()
        resp = _run(svc._handle_canvas(_FakeRequest({})))
        assert _body(resp)["canvas_url"] == ""
        svc._canvas.get_canvas_url.assert_not_called()

    def test_unrecognised_input_returns_empty_no_lookup(self):
        svc = _build_service()
        resp = _run(svc._handle_canvas(_FakeRequest({"track_id": "garbage"})))
        assert _body(resp)["canvas_url"] == ""
        # Must not waste a lookup on unparseable input
        svc._canvas.get_canvas_url.assert_not_called()

    def test_canvas_not_configured_returns_empty(self):
        """When the Spotify Canvas client has no sp_dc cookie, the
        handler must return an empty string without crashing."""
        svc = _build_service()
        svc._canvas.configured = False
        resp = _run(svc._handle_canvas(_FakeRequest({"track_id": VALID_ID})))
        assert _body(resp)["canvas_url"] == ""

    def test_no_canvas_for_track(self):
        """get_canvas_url returns None when the track has no canvas."""
        svc = _build_service()
        svc._canvas.get_canvas_url = AsyncMock(return_value=None)
        resp = _run(svc._handle_canvas(_FakeRequest({"track_id": VALID_ID})))
        assert _body(resp)["canvas_url"] == ""

    def test_lookup_exception_returns_empty(self):
        """Any exception from the lookup must be caught — the canvas
        endpoint is best-effort, never blocks the UI flow."""
        svc = _build_service()
        svc._canvas.get_canvas_url = AsyncMock(side_effect=RuntimeError("boom"))
        resp = _run(svc._handle_canvas(_FakeRequest({"track_id": VALID_ID})))
        assert _body(resp)["canvas_url"] == ""
