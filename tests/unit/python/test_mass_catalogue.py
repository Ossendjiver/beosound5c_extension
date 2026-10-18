import asyncio
from unittest.mock import patch
import pytest
from lib.mass_catalogue import merge, mappings
from sources.mass.service import MassSource


def release(id, name="King Steps", kind="album", explicit=True, depth=16, version=None):
    return {"item_id": id, "provider": "tidal", "media_type": kind, "name": name, "version": version,
            "artists": [{"name": "Disclosure"}, {"name": "Pa Salieu"}], "metadata": {"explicit": explicit},
            "audio_format": {"content_type": "flac", "bit_depth": depth, "sample_rate": 96000}}


def test_album_versions_prefer_available_explicit_high_quality():
    clean = release("clean", explicit=False, depth=24)
    cd = release("cd", version="Street Remix")
    best = release("best", depth=24, version="Interplanetary Criminal Remix")
    assert merge([clean, cd, best]) == [best]
    best["available"] = False
    assert merge([clean, cd, best]) == [cd]


def test_album_subtitle_alias_does_not_hide_the_containing_album():
    single = release("single", "Big Smile")
    best = release("best", "Big Smile (Pose For Me)", depth=24)
    album = release("album", "Afrikan Alien")
    assert merge([single, best, album]) == [best, album]


def test_missing_artist_and_unicode_and_musical_track_versions_are_safe():
    a, b = release("a", "夜"), release("b", "朝")
    assert len(merge([a, b])) == 2
    a, b = release("a", kind="track"), release("b", kind="track", version="Live")
    assert len(merge([a, b])) == 2
    b["version"] = None
    assert len(merge([a, b])) == 1
    a.pop("artists"); b.pop("artists")
    assert len(merge([a, b])) == 2


def test_provider_identity_transitively_merges_library_and_explicit_version():
    a = release("1"); a["provider"] = "library"
    a["provider_mappings"] = [{"provider_instance": "tidal", "item_id": "2"}]
    b = release("2", version="Original")
    c = release("3", depth=24, version="Deluxe")
    assert merge([a, b, c]) == [c]
    assert mappings({"provider_mappings": [{"provider_instance": "tidal", "item_id": "1", "available": False}]}) == []


def source():
    with patch.object(MassSource, "_load_local_cache", return_value=False):
        return MassSource()


def run(coro):
    return asyncio.run(coro)


def test_artist_show_all_follows_provider_ids_and_exposes_lazy_albums_without_playback():
    s = source(); calls = []
    artist = {"item_id": "1", "name": "Pa Salieu", "provider": "library", "media_type": "artist", "uri": "library://artist/1",
              "provider_mappings": [{"provider_instance": "tidal-one", "item_id": "exact-id"}, {"provider_instance": "soundcloud", "item_id": "no", "available": False}]}
    async def command(cmd, **kw):
        calls.append((cmd, kw))
        if cmd == "music/item_by_uri": return artist
        assert kw["item_id"] in ("1", "exact-id")
        assert "in_library_only" not in kw  # Not accepted by MA artist API.
        if cmd.endswith("albums"):
            item = release("best", depth=24, version="Deluxe")
            item["uri"] = "tidal-one://album/best"
            return [item, release("cd")]
        return [release("track", kind="track")]
    s.send_command = command
    result = run(s._build_catalogue(artist["uri"]))
    assert result["state"] == "ready"
    assert len(result["item"]["tracks"]) == 2
    album = result["item"]["tracks"][0]
    assert album["catalogue_expandable"] and album["play_url"] == "tidal-one://album/best"
    assert "url" not in album
    assert all(cmd.startswith("music/") for cmd, _ in calls)
    assert not any("soundcloud" in str(kw) for _, kw in calls)


def test_album_show_all_returns_provider_tracks_in_album_order_with_no_library_writes():
    s = source(); album = release("album"); album["uri"] = "tidal://album/album"
    async def command(cmd, **kw):
        if cmd == "music/item_by_uri": return album
        assert cmd == "music/albums/album_tracks"
        assert kw["in_library_only"] is False
        return [dict(release("two", "Second", kind="track"), uri="tidal://track/two"), dict(release("one", "First", kind="track"), uri="tidal://track/one")]
    s.send_command = command
    result = run(s._build_catalogue(album["uri"]))
    assert [x["name"] for x in result["item"]["tracks"]] == ["Second", "First"]
    assert all("url" in x and "catalogue_expandable" not in x for x in result["item"]["tracks"])


def test_partial_provider_failure_reports_warning_and_total_failure_reports_error():
    s = source()
    async def command(cmd, **kw):
        if cmd == "music/item_by_uri": return {"item_id": "1", "name": "Artist", "provider": "library", "media_type": "artist", "uri": "library://artist/1"}
        return [] if cmd.endswith("albums") else None
    s.send_command = command
    result = run(s._build_catalogue("library://artist/1"))
    assert result["warnings"] == ["library: tracks unavailable"]
    async def unavailable(cmd, **kw): return {"media_type": "album", "item_id": "a"} if cmd == "music/item_by_uri" else None
    s.send_command = unavailable
    with pytest.raises(ValueError, match="unavailable"):
        run(s._build_catalogue("tidal://album/a"))


def test_non_container_selection_is_rejected():
    s = source()
    async def command(*args, **kw): return {"media_type": "track"}
    s.send_command = command
    with pytest.raises(ValueError, match="artist or album"):
        run(s._build_catalogue("tidal://track/1"))
