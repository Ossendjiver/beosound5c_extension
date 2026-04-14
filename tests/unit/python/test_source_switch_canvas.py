"""End-to-end-ish tests for the canvas pipeline across source switches.

Background: the original Kitchen canvas bug only covered the
player-originated path (external Sonos start). After fixing it, a
follow-up gap appeared on Office: switching away from Spotify and
back caused the rebroadcast from the Spotify SOURCE to land in
router state without a ``track_id``, because ``post_media_update``
didn't forward the source's ``_last_track_uri`` as ``_track_uri``.
The UI's canvas-vs-artwork cycle then fell through to "no opinion"
mode, which works visually but isn't robust — a stale canvas could
still flash up.

These tests lock the contract for the source-switch flow:
  * ``post_media_update(track_uri=...)`` forwards as ``_track_uri``
  * Spotify source's ``_resolve_and_broadcast`` always passes
    ``track_uri``, so router-side stamping never falls back to the
    no-opinion path on resync
  * Switching to a non-Spotify source clears canvas + track_id from
    router state via the source's broadcast
  * Switching back to Spotify restores both via the source rebroadcast

This is the seam the live test on Office exercised — encoding it as
a unit suite catches any regression in source_base or spotify
service that drops the track_uri plumbing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import router as router_module
from lib.background_tasks import BackgroundTaskSet


VALID_ID = "3GBnGi8VktRx2Dr3Abn6m6"  # "I Love It" — real id format
SECOND_ID = "01RdEXps15f3VmQMV6OuTM"
SPOTIFY_URI = f"spotify:track:{VALID_ID}"
SECOND_URI = f"spotify:track:{SECOND_ID}"
CANVAS_URL = "https://canvaz.scdn.co/upload/artist/abc/video/xyz.cnvs.mp4"


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _FakeRequest:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


# ── post_media_update plumbing ───────────────────────────────────────


class TestPostMediaUpdateTrackUri:
    """``SourceBase.post_media_update`` must forward ``track_uri`` as
    ``_track_uri`` in the outgoing payload. The router's media POST
    handler then extracts ``track_id`` from it."""

    def _build_source(self):
        from lib.source_base import SourceBase

        class _S(SourceBase):
            id = "spotify"
            name = "Spotify"
            handles = set()

            async def handle_command(self, cmd, data): return {}

        with patch("lib.source_base.SourceBase.__init__", return_value=None):
            s = _S.__new__(_S)
            s.id = "spotify"
            s._action_ts = 0
            s._last_media = None
            s._http_session = MagicMock()
            s.ROUTER_MEDIA_URL = "http://r/media"
            s._background_tasks = BackgroundTaskSet(
                logging.getLogger("test"), label="spotify")
            return s

    def _capture_post(self, src):
        captured = {}

        class _Resp:
            status = 200
            async def __aenter__(self_inner): return self_inner
            async def __aexit__(self_inner, *a): return False

        def _post(url, json=None, **kwargs):
            captured["url"] = url
            captured["payload"] = json
            return _Resp()

        src._http_session.post = _post
        return captured

    def test_track_uri_forwarded_as_underscore_track_uri(self):
        src = self._build_source()
        captured = self._capture_post(src)
        _run(src.post_media_update(
            title="T", artist="A", album="B",
            track_uri=SPOTIFY_URI,
        ))
        assert captured["payload"]["_track_uri"] == SPOTIFY_URI

    def test_no_track_uri_omits_field(self):
        """Sources that don't know their URI must not pollute the
        payload with empty hints."""
        src = self._build_source()
        captured = self._capture_post(src)
        _run(src.post_media_update(title="T", artist="A"))
        assert "_track_uri" not in captured["payload"]

    def test_empty_string_track_uri_omits_field(self):
        src = self._build_source()
        captured = self._capture_post(src)
        _run(src.post_media_update(title="T", artist="A", track_uri=""))
        assert "_track_uri" not in captured["payload"]


# ── Spotify _resolve_and_broadcast plumbing ──────────────────────────


class TestSpotifyResolveAndBroadcast:
    """``SpotifyService._resolve_and_broadcast`` must always pass
    ``track_uri`` to ``post_media_update``, regardless of which
    metadata-resolution branch it takes (cache / live / fallback).
    Without this, the post-source-switch resync path drops track_id."""

    def _build_service(self):
        with patch.dict(os.environ, {"BS5C_CONFIG_DIR": "/tmp"}):
            with patch("lib.config.cfg", return_value=None):
                with patch("lib.source_base.SourceBase.__init__", return_value=None):
                    from sources.spotify.service import SpotifyService
                    svc = SpotifyService.__new__(SpotifyService)
                    svc.id = "spotify"
                    svc.post_media_update = AsyncMock()
                    svc._player_get = AsyncMock(return_value=None)
                    svc._last_media = None
                    svc.playlists = []
                    svc._last_playlist_id = None
                    return svc

    @pytest.mark.asyncio
    async def test_playlist_cache_branch_passes_track_uri(self):
        svc = self._build_service()
        svc.playlists = [{"id": "pl1", "tracks": [
            {"uri": SPOTIFY_URI, "name": "T", "artist": "A",
             "album": "Alb", "image": "i.jpg"},
        ]}]
        svc._last_playlist_id = "pl1"
        await svc._resolve_and_broadcast(SPOTIFY_URI, "track_change")
        assert svc.post_media_update.call_args.kwargs["track_uri"] == SPOTIFY_URI

    @pytest.mark.asyncio
    async def test_live_player_branch_passes_track_uri(self):
        svc = self._build_service()
        svc._player_get = AsyncMock(return_value={
            "title": "T", "artist": "A", "album": "B", "artwork": "x",
        })
        await svc._resolve_and_broadcast(SPOTIFY_URI, "track_change")
        assert svc.post_media_update.call_args.kwargs["track_uri"] == SPOTIFY_URI

    @pytest.mark.asyncio
    async def test_last_media_fallback_branch_passes_track_uri(self):
        svc = self._build_service()
        svc._last_media = {
            "title": "T", "artist": "A", "album": "B",
            "artwork": "x", "back_artwork": "",
        }
        await svc._resolve_and_broadcast(SPOTIFY_URI, "update")
        assert svc.post_media_update.call_args.kwargs["track_uri"] == SPOTIFY_URI

    @pytest.mark.asyncio
    async def test_canvas_url_forwarded_alongside_track_uri(self):
        """Canvas + track_uri must travel together — the UI uses
        track_id (derived from track_uri) to verify the canvas
        belongs to the playing track at render time."""
        svc = self._build_service()
        svc.playlists = [{"id": "pl1", "tracks": [
            {"uri": SPOTIFY_URI, "name": "T", "artist": "A",
             "album": "Alb", "image": "i.jpg"},
        ]}]
        svc._last_playlist_id = "pl1"
        await svc._resolve_and_broadcast(SPOTIFY_URI, "track_change",
                                          canvas_url=CANVAS_URL)
        kwargs = svc.post_media_update.call_args.kwargs
        assert kwargs["track_uri"] == SPOTIFY_URI
        assert kwargs["canvas_url"] == CANVAS_URL


# ── End-to-end: source switch via router ─────────────────────────────


class TestSourceSwitchCanvasFlow:
    """Simulate the live test we ran on Office: Spotify→Radio→Spotify.

    Drives the real EventRouter._handle_media_post + media state
    cache to verify that:
      1. A Spotify SOURCE broadcast (with canvas + track_uri) lands
         with both ``canvas_url`` and ``track_id`` in router state.
      2. A subsequent Radio source broadcast (no canvas, no track_uri)
         clears the canvas state for the new source.
      3. A Spotify rebroadcast (resync on activate) restores both
         canvas_url and track_id — this is the path that was broken
         before the post_media_update(track_uri=…) plumbing fix.
    """

    def _make_router(self):
        r = router_module.EventRouter()
        r._spawn = lambda coro, name=None: None  # absorb canvas_inject
        return r

    def _spotify_payload(self, *, with_canvas=False):
        p = {
            "title": "I Love It (feat. Charli XCX)",
            "artist": "Icona Pop",
            "album": "Icona Pop",
            "artwork": "https://i.scdn.co/image/abc",
            "state": "playing",
            "duration": 0,
            "position": 0,
            "_reason": "track_change",
            "_source_id": "spotify",
            "_track_uri": SPOTIFY_URI,
        }
        if with_canvas:
            p["canvas_url"] = CANVAS_URL
        return p

    def _radio_payload(self):
        return {
            "title": "P2: Radio Romano",
            "artist": "Sveriges Radio",
            "album": "—",
            "artwork": "https://r/art.jpg",
            "state": "playing",
            "duration": 0,
            "position": 0,
            "_reason": "track_change",
            "_source_id": "radio",
        }

    def _activate(self, router, source_id):
        """Register and activate a source so validate_update accepts it.

        Bypasses the SourceRegistry state machine (which would emit
        broadcasts and run validation transitions) — we only need
        the source to exist in the registry and be flagged active.
        """
        from lib.source_registry import Source
        src = Source(id=source_id, handles={"play"})
        src.command_url = f"http://localhost/{source_id}"
        src.player = "remote"
        # Bypass the read-only state property — set the underlying
        # slot directly. This is intentional in tests: we're locking
        # behaviour at the media-post layer, not the state machine.
        object.__setattr__(src, "_state", "playing")
        router.registry._sources[source_id] = src
        router.registry._active_id = source_id

    def test_full_switch_cycle(self):
        r = self._make_router()
        # 1. Spotify active, broadcast with canvas + track_uri
        self._activate(r, "spotify")
        _run(r._handle_media_post(_FakeRequest(
            self._spotify_payload(with_canvas=True))))
        assert r.media.state["title"].startswith("I Love It")
        assert r.media.state["canvas_url"] == CANVAS_URL
        assert r.media.state["track_id"] == VALID_ID

        # 2. Switch to radio — radio source becomes active and
        # broadcasts without canvas. Router state must reflect the
        # new track and clear canvas.
        self._activate(r, "radio")
        _run(r._handle_media_post(_FakeRequest(self._radio_payload())))
        assert r.media.state["title"] == "P2: Radio Romano"
        # Canvas validator defaults canvas_url to "" — locks in the
        # cleared state so the UI knows the previous canvas is gone.
        assert r.media.state.get("canvas_url", "") == ""
        # No spotify track id (radio isn't a Spotify track)
        assert "track_id" not in r.media.state or r.media.state["track_id"] != VALID_ID

        # 3. Switch back to Spotify — source rebroadcasts the same
        # track_uri and the router stamps track_id again.
        self._activate(r, "spotify")
        _run(r._handle_media_post(_FakeRequest(
            self._spotify_payload(with_canvas=True))))
        assert r.media.state["title"].startswith("I Love It")
        assert r.media.state["canvas_url"] == CANVAS_URL
        assert r.media.state["track_id"] == VALID_ID

    def test_rebroadcast_without_canvas_keeps_track_id(self):
        """Pre-broadcast (cached canvas not ready yet) lands without
        canvas_url. The router must still stamp track_id from
        ``_track_uri`` so the UI knows which track it's for."""
        r = self._make_router()
        self._activate(r, "spotify")
        _run(r._handle_media_post(_FakeRequest(
            self._spotify_payload(with_canvas=False))))
        assert r.media.state["track_id"] == VALID_ID
        assert r.media.state.get("canvas_url", "") == ""

    def test_canvas_url_changes_with_track(self):
        """A new track that ships its own canvas_url must overwrite
        the previous one — no stale-canvas leak across track boundaries."""
        r = self._make_router()
        self._activate(r, "spotify")

        # Track 1
        _run(r._handle_media_post(_FakeRequest(
            self._spotify_payload(with_canvas=True))))
        assert r.media.state["track_id"] == VALID_ID
        assert r.media.state["canvas_url"] == CANVAS_URL

        # Track 2 — different track id, different canvas
        new_canvas = "https://canvaz.scdn.co/upload/artist/xyz.mp4"
        p2 = self._spotify_payload(with_canvas=True)
        p2["title"] = "Tusen spänn"
        p2["_track_uri"] = SECOND_URI
        p2["canvas_url"] = new_canvas
        _run(r._handle_media_post(_FakeRequest(p2)))
        assert r.media.state["track_id"] == SECOND_ID
        assert r.media.state["canvas_url"] == new_canvas
