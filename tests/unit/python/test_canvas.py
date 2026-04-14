"""Tests for Spotify Canvas integration.

Tests the canvas client caching, source_base canvas_url passthrough,
and Spotify service canvas broadcast logic.
"""

import asyncio
import collections
import json
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add services/ to path
SERVICES_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "services")
sys.path.insert(0, SERVICES_DIR)


# ── SpotifyCanvasClient tests ──


class TestCanvasClientCache:
    """Test the SpotifyCanvasClient cache behavior."""

    def _make_client(self):
        from lib.spotify_canvas import SpotifyCanvasClient
        client = SpotifyCanvasClient(sp_dc="fake_token")
        return client

    def test_get_cached_miss(self):
        client = self._make_client()
        assert client.get_cached("spotify:track:abc123") is None

    def test_get_cached_hit(self):
        client = self._make_client()
        client._cache["spotify:track:abc123"] = "https://canvas.example.com/v.mp4"
        assert client.get_cached("spotify:track:abc123") == "https://canvas.example.com/v.mp4"

    def test_get_cached_empty_string_returns_none(self):
        """Empty string in cache means 'no canvas' — get_cached returns None."""
        client = self._make_client()
        client._cache["spotify:track:nocanvas"] = ""
        assert client.get_cached("spotify:track:nocanvas") is None

    def test_configured_with_sp_dc(self):
        client = self._make_client()
        assert client.configured is True

    def test_not_configured_without_sp_dc(self):
        from lib.spotify_canvas import SpotifyCanvasClient
        client = SpotifyCanvasClient(sp_dc="")
        assert client.configured is False

    def test_cache_is_ordered_dict(self):
        client = self._make_client()
        assert isinstance(client._cache, collections.OrderedDict)

    @pytest.mark.asyncio
    async def test_cache_eviction(self):
        """Cache should evict oldest entries when exceeding max size."""
        client = self._make_client()
        client._cache_max = 3
        client._web_token = "fake"
        client._token_expiry = 99999999999999

        # Fill cache via get_canvas_url (which triggers eviction)
        with patch("lib.spotify_canvas._fetch_canvas", return_value=None):
            for i in range(4):
                await client.get_canvas_url(f"spotify:track:t{i}")

        assert "spotify:track:t0" not in client._cache
        assert "spotify:track:t3" in client._cache
        assert len(client._cache) == 3

    @pytest.mark.asyncio
    async def test_get_canvas_url_skips_non_track_uris(self):
        client = self._make_client()
        assert await client.get_canvas_url("spotify:playlist:abc") is None
        assert await client.get_canvas_url("") is None
        assert await client.get_canvas_url(None) is None

    @pytest.mark.asyncio
    async def test_get_canvas_url_returns_cached(self):
        """get_canvas_url should return cached result without network call."""
        client = self._make_client()
        client._cache["spotify:track:cached"] = "https://cached.mp4"
        url = await client.get_canvas_url("spotify:track:cached")
        assert url == "https://cached.mp4"

    @pytest.mark.asyncio
    async def test_get_canvas_url_caches_none_as_empty(self):
        """Tracks without canvas should be cached as empty string to avoid re-fetch."""
        client = self._make_client()
        # Mock the network calls
        client._ensure_token = MagicMock()
        client._web_token = "fake_token"
        client._token_expiry = 99999999999999

        with patch("lib.spotify_canvas._fetch_canvas", return_value=None):
            url = await client.get_canvas_url("spotify:track:nocanvas")

        assert url is None
        assert client._cache["spotify:track:nocanvas"] == ""


# ── source_base canvas_url passthrough tests ──


class TestSourceBaseCanvasUrl:
    """Test that canvas_url flows correctly through post_media_update."""

    @pytest.fixture
    def source(self):
        """Create a minimal SourceBase-like mock for testing post_media_update."""
        from lib.source_base import SourceBase

        class TestSource(SourceBase):
            id = "test"
            name = "Test"
            port = 9999
            action_map = {}
            async def handle_command(self, cmd, data):
                return {}

        src = TestSource.__new__(TestSource)
        src.id = "test"
        src._action_ts = 0
        src._last_media = None
        src._registered_state = "playing"
        src._http_session = MagicMock()
        return src

    @pytest.mark.asyncio
    async def test_canvas_url_included_when_truthy(self, source):
        """canvas_url should be in the payload when non-empty."""
        posted_payload = {}

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        source._http_session.post = MagicMock(return_value=mock_resp)

        await source.post_media_update(
            title="Test", artist="Artist", canvas_url="https://canvas.mp4")

        call_args = source._http_session.post.call_args
        payload = call_args.kwargs.get("json") or call_args[1].get("json")
        assert payload["canvas_url"] == "https://canvas.mp4"

    @pytest.mark.asyncio
    async def test_canvas_url_excluded_when_empty(self, source):
        """canvas_url should NOT be in the payload when empty."""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        source._http_session.post = MagicMock(return_value=mock_resp)

        await source.post_media_update(
            title="Test", artist="Artist")

        call_args = source._http_session.post.call_args
        payload = call_args.kwargs.get("json") or call_args[1].get("json")
        assert "canvas_url" not in payload

    @pytest.mark.asyncio
    async def test_last_media_includes_canvas_when_set(self, source):
        """_last_media cache should include canvas_url when provided."""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        source._http_session.post = MagicMock(return_value=mock_resp)

        await source.post_media_update(
            title="Test", artist="Artist", canvas_url="https://canvas.mp4")

        assert source._last_media["canvas_url"] == "https://canvas.mp4"

    @pytest.mark.asyncio
    async def test_last_media_excludes_canvas_when_empty(self, source):
        """_last_media cache should NOT include canvas_url when empty."""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        source._http_session.post = MagicMock(return_value=mock_resp)

        await source.post_media_update(
            title="Test", artist="Artist")

        assert "canvas_url" not in source._last_media


# ── Spotify service canvas broadcast tests ──


class TestSpotifyCanvasBroadcast:
    """Test canvas broadcast logic in SpotifyService."""

    def _build_service(self):
        """Build a SpotifyService with mocked dependencies."""
        # Need to mock enough to instantiate without real config/auth
        with patch.dict(os.environ, {"BS5C_CONFIG_DIR": "/tmp"}):
            with patch("lib.config.cfg", return_value=None):
                with patch("lib.source_base.SourceBase.__init__", return_value=None):
                    from sources.spotify.service import SpotifyService
                    svc = SpotifyService.__new__(SpotifyService)
                    svc.id = "spotify"
                    svc._last_track_uri = None
                    svc._track_advanced_at = -10.0
                    svc._track_gen = 0
                    svc._last_playlist_id = None
                    svc._last_media = None
                    svc._action_ts = 0
                    svc._registered_state = "playing"
                    import logging
                    from lib.background_tasks import BackgroundTaskSet
                    svc._background_tasks = BackgroundTaskSet(
                        logging.getLogger("test"), label="spotify")
                    svc._canvas = MagicMock()
                    svc._canvas.configured = True
                    svc._canvas.get_cached = MagicMock(return_value=None)
                    svc._canvas.get_canvas_url = AsyncMock(return_value=None)
                    svc.post_media_update = AsyncMock()
                    svc._player_get = AsyncMock(return_value=None)
                    svc.playlists = []
                    return svc

    @pytest.mark.asyncio
    async def test_broadcast_current_track_uses_cached_canvas(self):
        svc = self._build_service()
        svc._last_track_uri = "spotify:track:abc"
        svc._canvas.get_cached.return_value = "https://cached-canvas.mp4"
        svc.playlists = [{"id": "pl1", "tracks": [
            {"uri": "spotify:track:abc", "name": "Song", "artist": "Art", "album": "Alb", "image": "img.jpg"}
        ]}]
        svc._last_playlist_id = "pl1"

        await svc._broadcast_current_track()

        svc.post_media_update.assert_called_once()
        call_kwargs = svc.post_media_update.call_args.kwargs
        assert call_kwargs["canvas_url"] == "https://cached-canvas.mp4"
        assert call_kwargs["reason"] == "track_change"
        assert call_kwargs["title"] == "Song"

    @pytest.mark.asyncio
    async def test_broadcast_current_track_empty_canvas_when_not_cached(self):
        svc = self._build_service()
        svc._last_track_uri = "spotify:track:abc"
        svc._canvas.get_cached.return_value = None
        svc.playlists = [{"id": "pl1", "tracks": [
            {"uri": "spotify:track:abc", "name": "Song", "artist": "Art", "album": "Alb", "image": "img.jpg"}
        ]}]
        svc._last_playlist_id = "pl1"

        await svc._broadcast_current_track()

        call_kwargs = svc.post_media_update.call_args.kwargs
        assert call_kwargs["canvas_url"] == ""

    @pytest.mark.asyncio
    async def test_fetch_and_broadcast_canvas_rebroadcasts(self):
        svc = self._build_service()
        svc._last_track_uri = "spotify:track:abc"
        svc._canvas.get_canvas_url = AsyncMock(return_value="https://new-canvas.mp4")
        svc.playlists = [{"id": "pl1", "tracks": [
            {"uri": "spotify:track:abc", "name": "Song", "artist": "Art", "album": "Alb", "image": "img.jpg"}
        ]}]
        svc._last_playlist_id = "pl1"

        await svc._fetch_and_broadcast_canvas("spotify:track:abc", svc._track_gen)

        svc.post_media_update.assert_called_once()
        call_kwargs = svc.post_media_update.call_args.kwargs
        assert call_kwargs["canvas_url"] == "https://new-canvas.mp4"
        assert call_kwargs["reason"] == "update"

    @pytest.mark.asyncio
    async def test_fetch_and_broadcast_canvas_skips_if_track_changed(self):
        """If user skipped to another track, don't broadcast stale canvas."""
        svc = self._build_service()
        svc._last_track_uri = "spotify:track:NEW"  # user already moved on
        svc._canvas.get_canvas_url = AsyncMock(return_value="https://old-canvas.mp4")
        gen = svc._track_gen
        svc._track_gen += 1  # simulate track change bumping generation

        await svc._fetch_and_broadcast_canvas("spotify:track:OLD", gen)

        svc.post_media_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_and_broadcast_canvas_no_canvas_no_broadcast(self):
        """If track has no canvas, don't re-broadcast."""
        svc = self._build_service()
        svc._last_track_uri = "spotify:track:abc"
        svc._canvas.get_canvas_url = AsyncMock(return_value=None)

        await svc._fetch_and_broadcast_canvas("spotify:track:abc", svc._track_gen)

        svc.post_media_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_resolve_and_broadcast_falls_back_to_player(self):
        """When track not in playlist cache, should fetch from player."""
        svc = self._build_service()
        svc._last_track_uri = "spotify:track:unknown"
        svc._last_playlist_id = "pl1"
        svc.playlists = [{"id": "pl1", "tracks": []}]  # track not in cache
        svc._player_get = AsyncMock(return_value={
            "title": "Live Title", "artist": "Live Artist",
            "album": "Live Album", "artwork": "live.jpg",
        })

        await svc._resolve_and_broadcast("spotify:track:unknown", "track_change", "")

        call_kwargs = svc.post_media_update.call_args.kwargs
        assert call_kwargs["title"] == "Live Title"

    @pytest.mark.asyncio
    async def test_next_then_poll_does_not_revert_to_old_track(self):
        """When user hits next, _poll_now_playing must not revert _last_track_uri
        if the player still reports the old track due to transition latency."""
        svc = self._build_service()
        svc.state = "playing"
        svc._last_track_uri = "spotify:track:zombie"
        svc._last_playlist_id = "pl1"
        svc.playlists = [{"id": "pl1", "tracks": [
            {"uri": "spotify:track:zombie", "name": "Zombie", "artist": "Art",
             "album": "Alb", "image": "z.jpg"},
            {"uri": "spotify:track:semester", "name": "Semester", "artist": "Art",
             "album": "Alb", "image": "s.jpg"},
        ]}]
        svc._canvas.get_cached.return_value = None
        svc._canvas.configured = False  # disable background canvas fetch

        # Simulate: _next advances locally, then poll sees old track from player
        svc.player_next = AsyncMock(return_value=True)
        svc.player_state = AsyncMock(return_value="playing")
        # Player still reports zombie (transition lag)
        svc.player_track_uri = AsyncMock(return_value="spotify:track:zombie")
        svc.register = AsyncMock()

        await svc._next()

        # _last_track_uri must be semester, NOT reverted to zombie
        assert svc._last_track_uri == "spotify:track:semester"
        # The last broadcast must be for Semester, not Zombie
        last_call = svc.post_media_update.call_args
        assert last_call.kwargs["title"] == "Semester"

    @pytest.mark.asyncio
    async def test_next_then_poll_accepts_confirmed_new_track(self):
        """When poll returns the new track (player caught up), accept it normally."""
        svc = self._build_service()
        svc.state = "playing"
        svc._last_track_uri = "spotify:track:zombie"
        svc._last_playlist_id = "pl1"
        svc.playlists = [{"id": "pl1", "tracks": [
            {"uri": "spotify:track:zombie", "name": "Zombie", "artist": "Art",
             "album": "Alb", "image": "z.jpg"},
            {"uri": "spotify:track:semester", "name": "Semester", "artist": "Art",
             "album": "Alb", "image": "s.jpg"},
        ]}]
        svc._canvas.get_cached.return_value = None
        svc._canvas.configured = False

        svc.player_next = AsyncMock(return_value=True)
        svc.player_state = AsyncMock(return_value="playing")
        # Player reports semester (caught up)
        svc.player_track_uri = AsyncMock(return_value="spotify:track:semester")
        svc.register = AsyncMock()

        await svc._next()

        assert svc._last_track_uri == "spotify:track:semester"

    @pytest.mark.asyncio
    async def test_poll_still_detects_auto_advance(self):
        """Regular polling (not after next/prev) should still detect track changes
        from auto-advance at end of song."""
        svc = self._build_service()
        svc.state = "playing"
        svc._last_track_uri = "spotify:track:zombie"
        svc._last_playlist_id = "pl1"
        svc.playlists = [{"id": "pl1", "tracks": [
            {"uri": "spotify:track:zombie", "name": "Zombie", "artist": "Art",
             "album": "Alb", "image": "z.jpg"},
            {"uri": "spotify:track:semester", "name": "Semester", "artist": "Art",
             "album": "Alb", "image": "s.jpg"},
        ]}]
        svc._canvas.get_cached.return_value = None
        svc._canvas.configured = False

        svc.player_state = AsyncMock(return_value="playing")
        svc.player_track_uri = AsyncMock(return_value="spotify:track:semester")
        svc.register = AsyncMock()

        await svc._poll_now_playing()

        # Poll should detect auto-advance and update
        assert svc._last_track_uri == "spotify:track:semester"
        svc.post_media_update.assert_called_once()
        assert svc.post_media_update.call_args.kwargs["title"] == "Semester"

    @pytest.mark.asyncio
    async def test_resolve_and_broadcast_falls_back_to_last_media(self):
        """When both playlist cache and player fail, use _last_media."""
        svc = self._build_service()
        svc._last_track_uri = "spotify:track:unknown"
        svc._last_playlist_id = "pl1"
        svc.playlists = [{"id": "pl1", "tracks": []}]
        svc._player_get = AsyncMock(return_value=None)
        svc._last_media = {
            "title": "Cached", "artist": "Cached Artist",
            "album": "Cached Album", "artwork": "cached.jpg",
            "back_artwork": "",
        }

        await svc._resolve_and_broadcast("spotify:track:unknown", "track_change", "https://canvas.mp4")

        call_kwargs = svc.post_media_update.call_args.kwargs
        assert call_kwargs["title"] == "Cached"
        assert call_kwargs["canvas_url"] == "https://canvas.mp4"
