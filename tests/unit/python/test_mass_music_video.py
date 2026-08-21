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

    def test_ignores_stale_player_media_when_queue_is_idle(self):
        source = _make_mass_source()
        source._get_queue_snapshot = AsyncMock(return_value={
            "resolved_queue_id": "queue-main",
            "state": "idle",
            "items": [],
        })
        source._resolve_player_candidates = AsyncMock(return_value=["player-main"])
        source._get_player_state = AsyncMock(return_value={
            "active_queue": "queue-main",
            "state": "playing",
            "current_media": {
                "name": "Stale Track",
                "artist_str": "Stale Artist",
                "album_name": "Stale Album",
                "uri": "mass://stale-track",
            },
        })

        payload = _run(source._build_now_playing_payload("queue-main"))

        assert payload == {"state": "idle", "queue_id": "queue-main"}

    def test_normalizes_dict_queue_artwork_in_player_fallback(self):
        source = _make_mass_source()
        source._get_queue_snapshot = AsyncMock(return_value={
            "resolved_queue_id": "queue-main",
            "state": "playing",
            "current_item": {
                "name": "Queue Track",
                "artist": "Queue Artist",
                "album": "Queue Album",
                "uri": "mass://queue-track",
                "image": {
                    "path": "https://images.example/queue-track.jpg",
                    "provider": "library",
                },
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
                "uri": "mass://queue-track",
            },
        })
        source._extract_player_artwork = AsyncMock(return_value="")
        source._fetch_item_artwork_by_uri = AsyncMock(return_value="")
        source._cache_image_locally = AsyncMock(side_effect=lambda image: image)

        payload = _run(source._build_now_playing_payload("queue-main"))

        assert payload["artwork"].startswith("http://")
        assert "imageproxy?path=" in payload["artwork"]


class TestMassQueuePayload:
    def test_extracts_paginated_queue_items(self):
        source = _make_mass_source()

        payload = {
            "items": {
                "items": [
                    {"queue_item_id": "one", "name": "Track One", "uri": "mass://one"},
                    {"queue_item_id": "two", "name": "Track Two", "uri": "mass://two"},
                ],
                "total": 2,
            }
        }

        items = source._extract_queue_items(payload)

        assert [item["name"] for item in items] == ["Track One", "Track Two"]

    def test_get_queue_returns_standard_source_queue_shape(self):
        source = _make_mass_source()
        source._get_queue_snapshot = AsyncMock(return_value={
            "resolved_queue_id": "queue-main",
            "current_index": 1,
            "items": {
                "items": [
                    {"queue_item_id": "one", "name": "Track One", "artist": "Artist A", "uri": "mass://one"},
                    {"queue_item_id": "two", "name": "Track Two", "artist": "Artist B", "uri": "mass://two"},
                ]
            },
        })
        source._resolve_queue_candidates = AsyncMock(return_value=["queue-main"])
        source._cache_image_locally = AsyncMock(side_effect=lambda image: image)

        queue = _run(source.get_queue())

        assert queue["queue_id"] == "queue-main"
        assert queue["total"] == 2
        assert queue["current_index"] == 1
        assert queue["tracks"][1]["title"] == "Track Two"
        assert queue["tracks"][1]["current"] is True

    def test_get_queue_keeps_items_without_playable_uri(self):
        source = _make_mass_source()
        source._get_queue_snapshot = AsyncMock(return_value={
            "resolved_queue_id": "queue-main",
            "current_index": 0,
            "items": {
                "items": [
                    {
                        "queue_item_id": "one",
                        "media_item": {
                            "item_id": "track-one",
                            "name": "Track One",
                            "metadata": {
                                "images": [
                                    {
                                        "type": "thumb",
                                        "path": "https://images.example/track-one.jpg",
                                        "provider": "library",
                                    },
                                ],
                            },
                        },
                    },
                    {
                        "queue_item_id": "two",
                        "name": "Track Two",
                        "artist": "Artist B",
                    },
                ]
            },
        })
        source._resolve_queue_candidates = AsyncMock(return_value=["queue-main"])
        source._cache_image_locally = AsyncMock(side_effect=lambda image: image)

        queue = _run(source.get_queue())

        assert queue["total"] == 2
        assert [track["title"] for track in queue["tracks"]] == ["Track One", "Track Two"]
        assert queue["tracks"][0]["uri"] == ""
        assert queue["tracks"][0]["current"] is True
        assert "imageproxy?path=" in queue["tracks"][0]["artwork"]
        source._cache_image_locally.assert_not_called()


class TestMassResync:
    def test_handle_resync_clears_available_when_queue_is_idle(self):
        source = _make_mass_source()
        source._resolve_queue_candidates = AsyncMock(return_value=["queue-main"])
        source._get_queue_snapshot = AsyncMock(return_value={
            "resolved_queue_id": "queue-main",
            "state": "idle",
            "items": [],
        })
        source._resolve_player_candidates = AsyncMock(return_value=["player-main"])
        source._get_player_state = AsyncMock(return_value={
            "active_queue": "queue-main",
            "state": "playing",
            "current_media": {
                "name": "Stale Track",
                "artist_str": "Stale Artist",
                "album_name": "Stale Album",
                "uri": "mass://stale-track",
            },
        })
        source.register = AsyncMock()
        source._publish_now_playing = AsyncMock()

        result = _run(source.handle_resync())

        assert result == {"status": "ok", "resynced": False}
        source.register.assert_awaited_once_with("available")
        source._publish_now_playing.assert_not_called()


class TestMassLibraryMenu:
    def test_normalize_renames_legacy_mixes_root_to_radio(self):
        source = _make_mass_source()
        tree = [
            {"id": "mixes", "name": "Mixes", "tracks": []},
            {"id": "playlist_mixes", "name": "Old", "tracks": []},
        ]

        source._normalize_library_tree(tree)

        assert tree[0]["name"] == "Radio"
        assert tree[1]["name"] == "Mixes"

    def test_identifies_library_mixes_playlist(self):
        source = _make_mass_source()

        assert source._is_mixes_playlist({
            "item_id": "98",
            "provider": "library",
            "uri": "playlist://library/98",
        })

    def test_build_playlist_folder_node_can_create_mixes_root(self):
        source = _make_mass_source()

        node = source._build_playlist_folder_node(
            {
                "item_id": "98",
                "name": "Anything",
                "provider": "library",
            },
            [
                {"item_id": "track-1", "name": "Track One", "uri": "mass://track-one"},
            ],
            "http://mass.example",
            root_id="playlist_mixes",
            root_name="Mixes",
        )

        assert node["id"] == "playlist_mixes"
        assert node["name"] == "Mixes"
        assert node["url"] == "playlist://library/98"
        assert node["tracks"][0]["url"] == "mass://track-one"

    def test_build_playlist_folder_node_uses_album_art_when_playlist_art_is_missing(self):
        source = _make_mass_source()

        node = source._build_playlist_folder_node(
            {
                "item_id": "11",
                "name": "Fallback Playlist",
                "provider": "library",
                "metadata": {"images": []},
            },
            [
                {
                    "item_id": "track-1",
                    "name": "Track One",
                    "provider": "library",
                    "uri": "mass://track-one",
                    "metadata": {"images": []},
                    "album": {
                        "name": "Album One",
                        "metadata": {
                            "images": [
                                {"type": "thumb", "path": "album-art.jpg", "provider": "library"}
                            ]
                        },
                    },
                    "artists": [{"name": "Artist One"}],
                }
            ],
            "http://mass.example",
        )

        assert "/imageproxy?" in node["image"]
        assert "/imageproxy?" in node["tracks"][0]["image"]

    def test_build_playlist_folder_node_keeps_placeholder_art_after_finalize(self):
        source = _make_mass_source()

        node = source._build_playlist_folder_node(
            {
                "item_id": "12",
                "name": "Sparse Playlist",
                "provider": "library",
                "metadata": {"images": []},
            },
            [
                {
                    "item_id": "track-2",
                    "name": "Track Two",
                    "provider": "library",
                    "uri": "mass://track-two",
                    "metadata": {"images": []},
                    "album": {"name": "Album Two", "metadata": {"images": []}},
                    "artists": [{"name": "Artist Two", "metadata": {"images": []}}],
                }
            ],
            "http://mass.example",
        )
        source._finalize_node(node)

        assert node["image"].startswith("data:image/svg+xml")
        assert node["tracks"][0]["image"].startswith("data:image/svg+xml")

    def test_backfill_cached_playlist_art_repairs_legacy_cache_nodes(self):
        source = _make_mass_source()
        tree = [
            {
                "id": "playlists",
                "name": "Playlists",
                "tracks": [
                    {
                        "id": "playlist:1",
                        "name": "Legacy Playlist",
                        "tracks": [
                            {"id": "track:1", "name": "First Track", "artist": "Artist A", "image": ""},
                            {"id": "track:2", "name": "Second Track", "artist": "Artist B"},
                        ],
                    }
                ],
            }
        ]

        changed = source._backfill_cached_playlist_art(tree)

        assert changed is True
        playlist = tree[0]["tracks"][0]
        assert playlist["image"].startswith("data:image/svg+xml")
        assert playlist["tracks"][0]["image"] == playlist["image"]
        assert playlist["tracks"][1]["image"] == playlist["image"]

    def test_normalize_renames_and_preserves_podcasts_root_order(self):
        source = _make_mass_source()
        tree = [
            {
                "id": "podcasts",
                "name": "Old",
                "tracks": [
                    {"id": "b", "name": "Zulu"},
                    {"id": "a", "name": "Alpha"},
                ],
            },
        ]

        source._normalize_library_tree(tree)

        assert tree[0]["name"] == "Podcasts"
        assert [item["name"] for item in tree[0]["tracks"]] == ["Zulu", "Alpha"]

    def test_library_status_includes_podcasts(self):
        source = _make_mass_source()
        source._library_data = [
            {"id": "podcasts", "tracks": [{}, {}]},
            {"id": "playlist_mixes", "tracks": [{}]},
        ]

        status = source._build_library_status()

        assert status["podcasts"] == 2
        assert status["mixes"] == 1


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
