"""Tests for GET /router/media — HTTP resync endpoint for the UI.

The UI fetches this when entering the playing view if its in-memory
mediaInfo looks stale/empty (e.g. broadcast was missed because WS
reconnected after the last update, or because a Sonos external start
raced view entry). If this endpoint is ever removed or stops returning
the cached state, the original Kitchen bug (artwork missing in immersive
mode) comes back — this test is the guard.
"""

from __future__ import annotations

import asyncio
import json

import pytest

import router as router_module
from lib.media_state import MediaState


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _FakeRequest:
    """Stand-in — _handle_media_get doesn't read the request at all."""


class TestMediaGetEndpoint:
    def test_returns_empty_object_when_no_state(self, monkeypatch):
        """Before any broadcast has landed, the endpoint must return an
        empty JSON object (not 404, not null) — the UI's resync code
        treats a falsy title as "nothing to apply"."""
        fresh = router_module.EventRouter()
        resp = _run(fresh._handle_media_get(_FakeRequest()))
        assert resp.status == 200
        assert json.loads(resp.text) == {}

    def test_returns_cached_state(self, monkeypatch):
        fresh = router_module.EventRouter()
        fresh.media._state = {
            "title": "Cached Track",
            "artist": "Cached Artist",
            "artwork": "http://x/a.jpg",
            "state": "playing",
        }
        resp = _run(fresh._handle_media_get(_FakeRequest()))
        assert resp.status == 200
        body = json.loads(resp.text)
        assert body["title"] == "Cached Track"
        assert body["artwork"] == "http://x/a.jpg"

    def test_reflects_accept_and_push(self):
        """End-to-end: pushing media via MediaState.accept_and_push must
        make it visible to the GET endpoint, with no separate sync."""
        fresh = router_module.EventRouter()
        payload = {"title": "Live", "artist": "A", "state": "playing"}
        _run(fresh.media.accept_and_push(payload))
        resp = _run(fresh._handle_media_get(_FakeRequest()))
        body = json.loads(resp.text)
        assert body["title"] == "Live"

    def test_idle_clears_state(self):
        """push_idle resets ._state to None — the endpoint should return
        an empty object, not the last pre-idle payload."""
        fresh = router_module.EventRouter()
        _run(fresh.media.accept_and_push({"title": "Before Idle"}))
        _run(fresh.media.push_idle())
        resp = _run(fresh._handle_media_get(_FakeRequest()))
        assert json.loads(resp.text) == {}


class TestMediaStateCacheContract:
    """MediaState is the single cache — _handle_media_get is only a
    thin view over it. These tests lock the contract so nobody switches
    to a separate cache by accident."""

    def test_state_attribute_is_source_of_truth(self):
        ms = MediaState()
        assert ms.state is None
        ms._state = {"title": "x"}
        assert ms.state == {"title": "x"}
