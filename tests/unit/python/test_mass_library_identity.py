from __future__ import annotations

import asyncio
from unittest.mock import patch

from sources.mass.service import MASS_MIXES_PLAYLIST_ROOT_ID, MassSource


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _make_mass_source():
    with patch.object(MassSource, "_load_local_cache", return_value=False):
        return MassSource()


class TestMassLibraryIdentity:
    def test_fetch_child_item_lists_deduplicates_provider_item_pairs(self):
        source = _make_mass_source()
        calls = []

        async def fake_fetch_list(cmd, **kwargs):
            calls.append((cmd, kwargs["item_id"], kwargs["provider_instance_id_or_domain"]))
            await asyncio.sleep(0)
            return [kwargs["item_id"]]

        source.fetch_list = fake_fetch_list

        result = _run(
            source._fetch_child_item_lists(
                items=[
                    {"item_id": "11", "provider": "library"},
                    {"item_id": "11", "provider": "library"},
                    {"item_id": "22", "provider": "spotify"},
                ],
                command="music/playlists/playlist_tracks",
                media_label="playlist",
                concurrency=2,
            )
        )

        assert calls == [
            ("music/playlists/playlist_tracks", "11", "library"),
            ("music/playlists/playlist_tracks", "22", "spotify"),
        ]
        assert result[source._provider_item_lookup_key("11", "library")] == ["11"]
        assert result[source._provider_item_lookup_key("22", "spotify")] == ["22"]

    def test_legacy_cache_upgrade_and_indexes_disambiguate_media_types(self):
        source = _make_mass_source()
        source._library_data = [
            {
                "id": "artists",
                "name": "Artists",
                "tracks": [
                    {
                        "id": "377",
                        "name": "Billie Eilish",
                        "url": "artist://library/377",
                        "tracks": [],
                    }
                ],
            },
            {
                "id": "albums",
                "name": "Albums",
                "tracks": [
                    {
                        "id": "377",
                        "name": "Tohu Bohu (Deluxe Edition)",
                        "url": "album://library/377",
                        "tracks": [
                            {
                                "id": "1234",
                                "name": "A Track",
                                "url": "track://library/1234",
                            }
                        ],
                    }
                ],
            },
            {
                "id": "songs",
                "name": "Songs",
                "tracks": [
                    {
                        "id": "377",
                        "name": "Guilty Pleasure",
                        "url": "track://library/377",
                    }
                ],
            },
        ]

        source._upgrade_cached_library_identities(source._library_data)
        source._rebuild_library_indexes()

        artist = source._library_data[0]["tracks"][0]
        album = source._library_data[1]["tracks"][0]
        track = source._library_data[2]["tracks"][0]

        assert artist["id"] == "artist:377"
        assert album["id"] == "album:377"
        assert track["id"] == "track:377"
        assert source._find_node_by_id("artist:377")["name"] == "Billie Eilish"
        assert source._find_node_by_id("album:377")["name"] == "Tohu Bohu (Deluxe Edition)"
        assert source._find_node_by_id("track:377")["name"] == "Guilty Pleasure"
        assert source._find_node_by_uri("album://library/377")["id"] == "album:377"

    def test_playlist_root_override_keeps_section_id_but_carries_canonical_identity(self):
        source = _make_mass_source()

        playlist = {
            "item_id": "98",
            "provider": "library",
            "name": "Mixes",
            "uri": "playlist://library/98",
        }
        tracks = [
            {
                "item_id": "12",
                "provider": "library",
                "name": "Song A",
                "uri": "track://library/12",
                "album": {"name": "Album A"},
                "artists": [{"name": "Artist A"}],
            }
        ]

        node = source._build_playlist_folder_node(
            playlist,
            tracks,
            "http://localhost:8095",
            root_id=MASS_MIXES_PLAYLIST_ROOT_ID,
            root_name="Mixes",
        )

        assert node["id"] == MASS_MIXES_PLAYLIST_ROOT_ID
        assert node["media_type"] == "playlist"
        assert node["item_id"] == "98"
        assert node["tracks"][0]["id"] == "track:12"
        assert node["tracks"][0]["album"] == "Album A"

    def test_update_library_cache_uses_bulk_music_indexes(self):
        source = _make_mass_source()
        source._incremental_save = lambda data: asyncio.sleep(0)

        artists_payload = [
            {
                "item_id": "377",
                "name": "Billie Eilish",
                "provider": "library",
                "uri": "library://artist/377",
                "metadata": {"images": []},
            }
        ]
        albums_payload = [
            {
                "item_id": "1005",
                "name": "WHEN WE ALL FALL ASLEEP, WHERE DO WE GO?",
                "provider": "library",
                "uri": "library://album/1005",
                "artists": [{"item_id": "377", "name": "Billie Eilish", "uri": "library://artist/377"}],
                "metadata": {"images": []},
            }
        ]
        tracks_payload = [
            {
                "item_id": "21981",
                "name": "bad guy",
                "provider": "library",
                "uri": "library://track/21981",
                "track_number": 2,
                "disc_number": 1,
                "album": {"item_id": "1005", "name": "WHEN WE ALL FALL ASLEEP, WHERE DO WE GO?"},
                "artists": [{"item_id": "377", "name": "Billie Eilish", "uri": "library://artist/377"}],
                "metadata": {"images": []},
            },
            {
                "item_id": "21980",
                "name": "!!!!!!!",
                "provider": "library",
                "uri": "library://track/21980",
                "track_number": 1,
                "disc_number": 1,
                "album": {"item_id": "1005", "name": "WHEN WE ALL FALL ASLEEP, WHERE DO WE GO?"},
                "artists": [{"item_id": "377", "name": "Billie Eilish", "uri": "library://artist/377"}],
                "metadata": {"images": []},
            },
        ]
        playlists_payload = [
            {
                "item_id": "98",
                "name": "Mixes",
                "provider": "library",
                "uri": "playlist://library/98",
                "metadata": {"images": []},
            },
            {
                "item_id": "44",
                "name": "Roadtrip",
                "provider": "library",
                "uri": "playlist://library/44",
                "metadata": {"images": []},
            },
        ]
        podcasts_payload = [
            {
                "item_id": "501",
                "name": "Alpha Show",
                "provider": "library",
                "uri": "podcast://library/501",
                "publisher": "Publisher A",
                "metadata": {"images": []},
            },
            {
                "item_id": "502",
                "name": "Beta Show",
                "provider": "library",
                "uri": "podcast://library/502",
                "publisher": "Publisher B",
                "metadata": {"images": []},
            },
        ]
        paginated_calls = []
        list_calls = []

        async def fake_fetch_paginated(cmd, **kwargs):
            paginated_calls.append(cmd)
            mapping = {
                "music/artists/library_items": artists_payload,
                "music/albums/library_items": albums_payload,
                "music/tracks/library_items": tracks_payload,
                "music/playlists/library_items": playlists_payload,
                "music/podcasts/library_items": podcasts_payload,
                "music/radios/library_items": [],
            }
            return mapping[cmd]

        async def fake_fetch_list(cmd, **kwargs):
            list_calls.append((cmd, kwargs["item_id"], kwargs["provider_instance_id_or_domain"]))
            if cmd == "music/playlists/playlist_tracks":
                playlist_tracks = {
                    "98": [
                        {
                            "item_id": "12",
                            "provider": "library",
                            "name": "Mix Track",
                            "uri": "library://track/12",
                            "album": {"name": "Mix Album"},
                            "artists": [{"name": "Mix Artist"}],
                            "metadata": {"images": []},
                        }
                    ],
                    "44": [
                        {
                            "item_id": "13",
                            "provider": "library",
                            "name": "Road Song",
                            "uri": "library://track/13",
                            "album": {"name": "Road Album"},
                            "artists": [{"name": "Road Artist"}],
                            "metadata": {"images": []},
                        }
                    ],
                }
                return playlist_tracks[kwargs["item_id"]]
            if cmd == "music/podcasts/podcast_episodes":
                podcast_episodes = {
                    "501": [
                        {
                            "item_id": "801",
                            "provider": "library",
                            "name": "Episode Old",
                            "uri": "podcast-episode://library/801",
                            "added_at": "2024-01-01T08:00:00+00:00",
                            "metadata": {"images": []},
                        }
                    ],
                    "502": [
                        {
                            "item_id": "802",
                            "provider": "library",
                            "name": "Episode New",
                            "uri": "podcast-episode://library/802",
                            "added_at": "2024-02-01T08:00:00+00:00",
                            "metadata": {"images": []},
                        }
                    ],
                }
                return podcast_episodes[kwargs["item_id"]]
            raise AssertionError(f"unexpected fetch_list call: {cmd}")

        source.fetch_paginated = fake_fetch_paginated
        source.fetch_list = fake_fetch_list

        _run(source.update_library_cache())

        list_commands = [entry[0] for entry in list_calls]
        assert "music/artists/artist_albums" not in list_commands
        assert "music/albums/album_tracks" not in list_commands
        assert paginated_calls[:3] == [
            "music/artists/library_items",
            "music/albums/library_items",
            "music/tracks/library_items",
        ]

        artists_root, albums_root, songs_root = source._library_data[:3]
        assert artists_root["tracks"][0]["id"] == "artist:377"
        assert artists_root["tracks"][0]["tracks"][0]["id"] == "album:1005"
        assert [track["id"] for track in artists_root["tracks"][0]["tracks"][0]["tracks"]] == [
            "track:21980",
            "track:21981",
        ]
        assert albums_root["tracks"][0]["id"] == "album:1005"
        assert [track["id"] for track in albums_root["tracks"][0]["tracks"]] == [
            "track:21980",
            "track:21981",
        ]
        assert [track["id"] for track in songs_root["tracks"]] == [
            "track:21980",
            "track:21981",
        ]

        playlists_root = source._library_data[3]
        mixes_root = source._library_data[4]
        podcasts_root = source._library_data[5]

        assert [playlist["id"] for playlist in playlists_root["tracks"]] == [
            "playlist:98",
            "playlist:44",
        ]
        assert mixes_root["id"] == MASS_MIXES_PLAYLIST_ROOT_ID
        assert mixes_root["item_id"] == "98"
        assert [track["id"] for track in mixes_root["tracks"]] == ["track:12"]
        assert [podcast["id"] for podcast in podcasts_root["tracks"]] == [
            "podcast:502",
            "podcast:501",
        ]
        assert [track["id"] for track in podcasts_root["tracks"][0]["tracks"]] == ["podcast_episode:802"]
