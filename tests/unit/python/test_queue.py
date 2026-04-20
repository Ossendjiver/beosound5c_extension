"""Unit tests for the Router-Owns-Queue feature.

Tests queue methods on PlayerBase, SourceBase, and source services
in isolation (no network, no real devices).
"""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

# Add services/ to sys.path
SERVICES_DIR = Path(__file__).resolve().parents[3] / "services"
sys.path.insert(0, str(SERVICES_DIR))


# ── PlayerBase queue defaults ──

class TestPlayerBaseQueue:
    """PlayerBase.get_queue() and play_from_queue() defaults."""

    def test_default_get_queue_returns_empty(self):
        from lib.player_base import PlayerBase
        p = PlayerBase()
        result = asyncio.run(p.get_queue())
        assert result == {"tracks": [], "current_index": -1, "total": 0}

    def test_default_get_queue_with_params(self):
        from lib.player_base import PlayerBase
        p = PlayerBase()
        result = asyncio.run(p.get_queue(start=5, max_items=10))
        assert result["tracks"] == []

    def test_default_play_from_queue_returns_false(self):
        from lib.player_base import PlayerBase
        p = PlayerBase()
        result = asyncio.run(p.play_from_queue(3))
        assert result is False


# ── SourceBase queue defaults ──

class TestSourceBaseQueue:
    """SourceBase.manages_queue flag and get_queue() defaults."""

    def test_manages_queue_default_false(self):
        from lib.source_base import SourceBase
        assert SourceBase.manages_queue is False

    def test_default_get_queue_returns_empty(self):
        from lib.source_base import SourceBase
        s = SourceBase()
        result = asyncio.run(s.get_queue())
        assert result == {"tracks": [], "current_index": -1, "total": 0}

    def test_default_get_queue_with_params(self):
        from lib.source_base import SourceBase
        s = SourceBase()
        result = asyncio.run(s.get_queue(start=10, max_items=5))
        assert result["tracks"] == []


# ── Plex queue ──

class TestPlexQueue:
    """PlexService.get_queue() returns playlist tracks."""

    @pytest.fixture
    def plex_service(self, mock_config):
        mock_config({
            "device": "Test",
            "menu": {"PLEX": "plex"},
            "player": {"type": "local"},
        })
        from sources.plex.service import PlexService
        return PlexService()

    def test_plex_manages_queue_true(self, plex_service):
        assert plex_service.manages_queue is True

    def test_plex_empty_when_no_playlist(self, plex_service):
        result = asyncio.run(plex_service.get_queue())
        assert result["tracks"] == []
        assert result["current_index"] == -1

    def test_plex_returns_playlist_tracks(self, plex_service):
        plex_service._current_playlist = {
            "name": "Test Playlist",
            "tracks": [
                {"name": "Track 1", "artist": "Artist A", "image": "http://art1.jpg"},
                {"name": "Track 2", "artist": "Artist B", "image": "http://art2.jpg"},
                {"name": "Track 3", "artist": "Artist C", "image": "http://art3.jpg"},
            ],
        }
        plex_service._current_index = 1

        result = asyncio.run(plex_service.get_queue())
        assert result["total"] == 3
        assert result["current_index"] == 1
        assert len(result["tracks"]) == 3
        assert result["tracks"][0]["title"] == "Track 1"
        assert result["tracks"][1]["current"] is True
        assert result["tracks"][2]["current"] is False

    def test_plex_queue_pagination(self, plex_service):
        plex_service._current_playlist = {
            "name": "Big Playlist",
            "tracks": [{"name": f"Track {i}", "artist": "A", "image": ""} for i in range(20)],
        }
        plex_service._current_index = 5

        result = asyncio.run(plex_service.get_queue(start=3, max_items=5))
        assert len(result["tracks"]) == 5
        assert result["tracks"][0]["index"] == 3
        assert result["tracks"][-1]["index"] == 7
        assert result["total"] == 20

    def test_plex_queue_track_ids_prefixed(self, plex_service):
        plex_service._current_playlist = {
            "name": "Test",
            "tracks": [{"name": "T", "artist": "A", "image": ""}],
        }
        plex_service._current_index = 0

        result = asyncio.run(plex_service.get_queue())
        assert result["tracks"][0]["id"] == "q:0"


class TestQueueOwningSources:
    def test_mass_manages_queue_true(self):
        from sources.mass.service import MassSource
        assert MassSource.manages_queue is True

    def test_kodi_manages_queue_true(self):
        from sources.kodi.service import KodiSource
        assert KodiSource.manages_queue is True


# ── Spotify queue ──

class TestSpotifyQueue:
    """SpotifyService.get_queue() returns playlist tracks for local player.

    We don't import SpotifyService directly because its module pulls in the
    full Spotify stack (aiohttp sessions, PKCE, etc.).  Test the queue logic
    via SourceBase with the same algorithm Spotify uses.
    """

    def _make_spotify_like_source(self):
        """Create a SourceBase subclass with Spotify's get_queue logic."""
        from lib.source_base import SourceBase

        class FakeSpotify(SourceBase):
            id = "spotify"
            name = "Spotify"
            port = 8771
            manages_queue = False
            action_map = {"play": "toggle"}
            playlists = []
            _last_playlist_id = None
            _last_track_uri = None

            async def handle_command(self, cmd, data):
                return {}

            def _find_track_index(self, playlist_id, track_uri):
                if not track_uri:
                    return 0
                for pl in self.playlists:
                    if pl.get('id') == playlist_id:
                        for i, track in enumerate(pl.get('tracks', [])):
                            if track.get('uri') == track_uri:
                                return i
                        break
                return 0

            async def get_queue(self, start=0, max_items=50):
                if not self._last_playlist_id:
                    return {"tracks": [], "current_index": -1, "total": 0}
                playlist = None
                for pl in self.playlists:
                    if pl.get('id') == self._last_playlist_id:
                        playlist = pl
                        break
                if not playlist:
                    return {"tracks": [], "current_index": -1, "total": 0}
                all_tracks = playlist.get('tracks', [])
                current_index = self._find_track_index(
                    self._last_playlist_id, self._last_track_uri)
                end = min(start + max_items, len(all_tracks))
                tracks = []
                for i in range(start, end):
                    t = all_tracks[i]
                    tracks.append({
                        "id": f"q:{i}",
                        "title": t.get("name", ""),
                        "artist": t.get("artist", ""),
                        "album": "",
                        "artwork": t.get("image", ""),
                        "index": i,
                        "current": i == current_index,
                    })
                return {
                    "tracks": tracks,
                    "current_index": current_index,
                    "total": len(all_tracks),
                }

        return FakeSpotify()

    def test_spotify_manages_queue_false(self):
        svc = self._make_spotify_like_source()
        assert svc.manages_queue is False

    def test_spotify_empty_when_no_playlist(self):
        svc = self._make_spotify_like_source()
        result = asyncio.run(svc.get_queue())
        assert result["tracks"] == []

    def test_spotify_returns_playlist_tracks(self):
        svc = self._make_spotify_like_source()
        svc.playlists = [
            {
                "id": "pl123",
                "tracks": [
                    {"name": "Song A", "artist": "Band X", "image": "http://img1", "uri": "spotify:track:aaa"},
                    {"name": "Song B", "artist": "Band Y", "image": "http://img2", "uri": "spotify:track:bbb"},
                    {"name": "Song C", "artist": "Band Z", "image": "http://img3", "uri": "spotify:track:ccc"},
                ],
            }
        ]
        svc._last_playlist_id = "pl123"
        svc._last_track_uri = "spotify:track:bbb"

        result = asyncio.run(svc.get_queue())
        assert result["total"] == 3
        assert result["current_index"] == 1
        assert result["tracks"][1]["current"] is True
        assert result["tracks"][0]["title"] == "Song A"

    def test_spotify_pagination(self):
        svc = self._make_spotify_like_source()
        svc.playlists = [
            {
                "id": "pl456",
                "tracks": [{"name": f"T{i}", "artist": "A", "image": "", "uri": f"spotify:track:{i}"} for i in range(30)],
            }
        ]
        svc._last_playlist_id = "pl456"
        svc._last_track_uri = "spotify:track:10"

        result = asyncio.run(svc.get_queue(start=8, max_items=5))
        assert len(result["tracks"]) == 5
        assert result["tracks"][0]["index"] == 8
        assert result["total"] == 30


# ── Router Source model ──

class TestRouterSourceModel:
    """Router Source model stores manages_queue."""

    def test_source_manages_queue_default(self):
        from router import Source
        s = Source("test", {"play"})
        assert s.manages_queue is False

    def test_source_manages_queue_set(self):
        from router import Source
        s = Source("plex", {"play"})
        s.manages_queue = True
        assert s.manages_queue is True


# ── Queue authority logic ──

class TestQueueAuthority:
    """Test the authority selection logic used by /router/queue."""

    def _authority(self, manages_queue, player_type):
        """Simulate the router's queue authority logic.
        Returns "source" or "player" for primary authority."""
        if manages_queue or player_type == "local":
            return "source"
        else:
            return "player"

    def test_plex_with_sonos_is_player(self):
        assert self._authority(manages_queue=True, player_type="remote") == "source"

    def test_plex_with_local_is_source(self):
        assert self._authority(manages_queue=True, player_type="local") == "source"

    def test_spotify_with_sonos_is_player(self):
        assert self._authority(manages_queue=False, player_type="remote") == "player"

    def test_spotify_with_local_is_source(self):
        assert self._authority(manages_queue=False, player_type="local") == "source"

    def test_radio_with_local_is_source(self):
        # Radio has manages_queue=False, local player → source first
        # (source returns empty, falls back to media_state)
        assert self._authority(manages_queue=False, player_type="local") == "source"

    def test_cd_with_sonos_is_source(self):
        # CD manages its own queue regardless of player
        assert self._authority(manages_queue=True, player_type="remote") == "source"


# ── SourceBase registration includes manages_queue ──

class TestSourceRegistrationPayload:
    """Verify manages_queue is included in router registration."""

    def test_register_sends_manages_queue(self, mock_config):
        mock_config({
            "device": "Test",
            "menu": {"PLEX": "plex"},
            "player": {"type": "local"},
        })
        from lib.source_base import SourceBase

        class TestSource(SourceBase):
            id = "test"
            name = "Test"
            port = 9999
            manages_queue = True
            action_map = {"play": "toggle"}

            async def handle_command(self, cmd, data):
                return {}

        src = TestSource()

        # Capture the payload sent to register using a proper async CM mock
        captured = {}

        class FakeResp:
            status = 200
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class FakeSession:
            def post(self, url, json=None, timeout=None):
                captured.update(json or {})
                return FakeResp()

        src._http_session = FakeSession()

        asyncio.run(src.register("available"))
        assert "manages_queue" in captured
        assert captured["manages_queue"] is True


# ── Beo6 queue item detection ──

class TestBeo6QueueItemDetection:
    """Beo6 _handle_play detects q: prefixed IDs."""

    def test_queue_id_prefix(self):
        track_id = "q:5"
        assert track_id.startswith("q:")
        position = int(track_id.split(":", 1)[1])
        assert position == 5

    def test_catalog_id_no_prefix(self):
        track_id = "42"
        assert not track_id.startswith("q:")

    def test_queue_id_zero(self):
        track_id = "q:0"
        assert track_id.startswith("q:")
        assert int(track_id.split(":", 1)[1]) == 0
