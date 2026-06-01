from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from sources.kodi.service import KodiSource, PLAYLIST_VIDEO


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _make_kodi_source():
    with patch.object(KodiSource, "_load_local_cache", return_value=False):
        return KodiSource()


class TestKodiLibraryOptions:
    def test_play_next_inserts_after_active_item(self):
        source = _make_kodi_source()
        source._active_playlist_context = AsyncMock(return_value=(7, PLAYLIST_VIDEO, 2))
        source._rpc = AsyncMock(return_value={"ok": True})

        result = _run(source.handle_command("play_next", {
            "url": "kodi://movie/42",
        }))

        source._rpc.assert_awaited_once_with(
            "Playlist.Insert",
            {"playlistid": PLAYLIST_VIDEO, "position": 3, "item": {"movieid": 42}},
            target=None,
        )
        assert result["state"] == "queued"
        assert result["option"] == "next"
        assert result["target_index"] == 3

    def test_mark_unwatched_updates_movie_playcount(self):
        source = _make_kodi_source()
        source._rpc = AsyncMock(return_value={"ok": True})

        result = _run(source.handle_command("mark_unwatched", {
            "url": "kodi://movie/17",
        }))

        source._rpc.assert_awaited_once_with(
            "VideoLibrary.SetMovieDetails",
            {"movieid": 17, "playcount": 0},
            target=None,
        )
        assert result["state"] == "updated"
        assert result["action"] == "mark_unwatched"

    def test_favorite_add_for_playlist_uses_kodi_favourites_api(self):
        source = _make_kodi_source()
        source._rpc = AsyncMock(return_value={"ok": True})

        result = _run(source.handle_command("favorite_add", {
            "url": "kodi://playlist/special%3A%2F%2Fprofile%2Fplaylists%2Fvideo%2FRoad%2520Trip.xsp",
        }))

        source._rpc.assert_awaited_once_with(
            "Favourites.AddFavourite",
            {
                "title": "Road%20Trip",
                "type": "media",
                "path": "special://profile/playlists/video/Road%20Trip.xsp",
                "thumbnail": None,
            },
            target=None,
        )
        assert result["state"] == "favorited"

    def test_play_from_here_rebuilds_playlist_from_selected_item(self):
        source = _make_kodi_source()
        source._library_data = [
            {
                "id": "movies",
                "name": "Movies",
                "tracks": [
                    {"name": "One", "url": "kodi://movie/1"},
                    {"name": "Two", "url": "kodi://movie/2"},
                    {"name": "Three", "url": "kodi://movie/3"},
                ],
            }
        ]
        source._rpc = AsyncMock(return_value={"ok": True})
        source._build_active_media_payload = AsyncMock(return_value={"title": "Two", "state": "playing"})
        source._build_cached_media_payload = AsyncMock(return_value=None)
        source._post_media_snapshot = AsyncMock()
        source.register = AsyncMock()

        result = _run(source.handle_command("play_from_here", {
            "url": "kodi://movie/2",
        }))

        awaited = source._rpc.await_args_list
        assert awaited[0].args[0] == "Playlist.Clear"
        assert awaited[0].args[1] == {"playlistid": PLAYLIST_VIDEO}
        assert awaited[1].args[0] == "Playlist.Add"
        assert awaited[1].args[1] == {"playlistid": PLAYLIST_VIDEO, "item": {"movieid": 2}}
        assert awaited[2].args[0] == "Playlist.Add"
        assert awaited[2].args[1] == {"playlistid": PLAYLIST_VIDEO, "item": {"movieid": 3}}
        assert awaited[3].args[0] == "Player.Open"
        assert awaited[3].args[1] == {"item": {"playlistid": PLAYLIST_VIDEO, "position": 0}}
        source._post_media_snapshot.assert_awaited_once()
        source.register.assert_not_called()
        assert result["state"] == "playing"
        assert result["items"] == 2


class TestKodiArtworkFallbacks:
    def test_load_playlist_nodes_backfills_directory_and_leaf_art(self):
        source = _make_kodi_source()

        async def fake_rpc(method, params, target=None):
            assert method == "Files.GetDirectory"
            if params["directory"] == "special://videoplaylists":
                return {
                    "files": [
                        {
                            "file": "special://videoplaylists/Collections",
                            "filetype": "directory",
                            "label": "Collections",
                            "thumbnail": "",
                        },
                        {
                            "file": "special://videoplaylists/Road Trip.xsp",
                            "filetype": "file",
                            "label": "Road Trip.xsp",
                            "thumbnail": "",
                        },
                    ]
                }
            if params["directory"] == "special://videoplaylists/Collections":
                return {
                    "files": [
                        {
                            "file": "special://videoplaylists/Collections/Night Watch.xsp",
                            "filetype": "file",
                            "label": "Night Watch.xsp",
                            "thumbnail": "",
                        }
                    ]
                }
            raise AssertionError(f"unexpected directory lookup: {params['directory']}")

        source._rpc = AsyncMock(side_effect=fake_rpc)

        nodes = _run(source._load_playlist_nodes("special://videoplaylists", "Video Playlist"))

        assert len(nodes) == 2
        assert nodes[0]["image"].startswith("data:image/svg+xml")
        assert nodes[0]["tracks"][0]["image"].startswith("data:image/svg+xml")
        assert nodes[1]["image"].startswith("data:image/svg+xml")

    def test_queue_items_use_placeholder_art_when_kodi_returns_none(self):
        source = _make_kodi_source()
        source._active_playlist_context = AsyncMock(return_value=(7, PLAYLIST_VIDEO, 0))
        source._playlist_items = AsyncMock(return_value=[
            {
                "title": "Road Trip",
                "album": "",
                "artist": [],
                "showtitle": "",
                "thumbnail": "",
                "art": {},
                "file": "special://videoplaylists/Road Trip.xsp",
            }
        ])

        result = _run(source.get_queue())

        assert result["tracks"][0]["artwork"].startswith("data:image/svg+xml")

    def test_active_media_payload_uses_placeholder_art_when_item_is_blank(self):
        source = _make_kodi_source()
        source._session = object()
        source._get_active_player_ids = AsyncMock(return_value=[1])
        source._find_node_by_uri = lambda uri: (None, [])

        async def fake_rpc(method, params, target=None):
            if method == "Player.GetProperties":
                return {"speed": 1, "time": {}, "totaltime": {}}
            if method == "Player.GetItem":
                return {
                    "item": {
                        "type": "movie",
                        "movieid": 42,
                        "title": "Untitled",
                        "album": "",
                        "artist": [],
                        "showtitle": "",
                        "thumbnail": "",
                        "art": {},
                        "file": "/media/untitled.mkv",
                    }
                }
            raise AssertionError(f"unexpected RPC method: {method}")

        source._rpc = AsyncMock(side_effect=fake_rpc)

        payload = _run(source._build_active_media_payload())

        assert payload["artwork"].startswith("data:image/svg+xml")
