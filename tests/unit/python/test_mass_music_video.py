"""Regression coverage for MASS metadata and music-video routing.

These tests pin the contract needed for the immersive video layer:
1. MASS must produce now-playing payloads with title/artist/uri.
2. The router must accept a MASS media payload and surface a
   ``music_video_url`` to the UI when a lookup is available.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import router as router_module
from sources.mass.service import MassSource


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _FakeRequest:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def _make_mass_source():
    with patch.object(MassSource, "_load_local_cache", return_value=False):
        return MassSource()


class TestMassNowPlayingPayload:
    def test_prefers_player_state_metadata(self):
        source = _make_mass_source()
        source._get_queue_snapshot = AsyncMock(return_value={
            "resolved_queue_id": "queue-main",
            "state": "playing",
            "current_item": {
                "name": "Queue Track",
                "artist": "Queue Artist",
                "album": "Queue Album",
                "uri": "mass://queue-track",
            },
        })
        source._resolve_player_candidates = AsyncMock(return_value=["player-main"])
        source._get_player_state = AsyncMock(return_value={
            "active_queue": "queue-main",
            "state": "playing",
            "current_media": {
                "name": "Player Track",
                "artist_str": "Player Artist",
                "album_name": "Player Album",
                "uri": "mass://player-track",
            },
        })
        source._extract_player_artwork = AsyncMock(return_value="")
        source._fetch_item_artwork_by_uri = AsyncMock(return_value="")
        source._cache_image_locally = AsyncMock(side_effect=lambda image: image)

        payload = _run(source._build_now_playing_payload("queue-main"))

        assert payload["state"] == "playing"
        assert payload["title"] == "Player Track"
        assert payload["artist"] == "Player Artist"
        assert payload["album"] == "Player Album"
        assert payload["uri"] == "mass://player-track"

    def test_falls_back_to_queue_metadata(self):
        source = _make_mass_source()
        source._get_queue_snapshot = AsyncMock(return_value={
            "resolved_queue_id": "queue-main",
            "state": "playing",
            "current_item": {
                "name": "Queue Track",
                "artist": "Queue Artist",
                "album": "Queue Album",
                "uri": "mass://queue-track",
            },
        })
        source._resolve_player_candidates = AsyncMock(return_value=[])
        source._cache_image_locally = AsyncMock(side_effect=lambda image: image)

        payload = _run(source._build_now_playing_payload("queue-main"))

        assert payload["state"] == "playing"
        assert payload["title"] == "Queue Track"
        assert payload["artist"] == "Queue Artist"
        assert payload["album"] == "Queue Album"
        assert payload["uri"] == "mass://queue-track"


class TestMassMusicVideoRouting:
    def test_router_surfaces_cached_music_video_for_mass_payload(self):
        router = router_module.EventRouter()
        mv_client = MagicMock()
        mv_client.get_cached.return_value = "https://video.example/mass.mp4"
        router._music_video_client = mv_client

        payload = {
            "title": "Player Track",
            "artist": "Player Artist",
            "state": "playing",
            "_reason": "track_change",
            "_source_id": "mass",
            "uri": "mass://player-track",
        }

        response = _run(router._handle_media_post(_FakeRequest(payload)))

        assert response.status == 200
        assert router.media.state is not None
        assert router.media.state["music_video_url"] == "https://video.example/mass.mp4"
        mv_client.get_cached.assert_called_once_with("Player Artist", "Player Track")
