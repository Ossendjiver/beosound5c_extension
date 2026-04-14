"""Regression tests for fetch.py's incremental-sync cache loader.

The cache is keyed by playlist snapshot_id: if a playlist's snapshot
hasn't changed since last fetch, the cached tracks are reused as-is.
This means schema drift (e.g. a new field added to the track dict)
never reaches playlists that haven't been edited — they stay frozen
on the old schema forever.

Kitchen symptom: "I Will Always Love You" played with an empty
album field for ~3 seconds. The track was cached in spotify_playlists.json
from an old fetch run where ``album`` wasn't stored, and snapshot_id
matching kept preserving that old schema. _lookup_track_meta returned
a track dict with no ``album`` key, so Spotify source broadcast
``album=""`` until the Sonos monitor caught up with the real album
from SoCo's DIDL.

Fix: _load_cache drops entries whose first track is missing any
required field, forcing a refetch on the next run.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sources.spotify.fetch import _load_cache


def _write(tmp_path: Path, data) -> Path:
    p = tmp_path / "spotify_playlists.json"
    p.write_text(json.dumps(data))
    return p


def test_loads_cache_when_every_track_has_required_fields(tmp_path):
    data = [
        {
            "id": "pl1", "snapshot_id": "abc",
            "tracks": [
                {"name": "t1", "artist": "a", "album": "alb", "uri": "u"},
            ],
        },
    ]
    p = _write(tmp_path, data)
    cache, stale = _load_cache(str(p))
    assert stale == 0
    assert list(cache) == ["pl1"]
    assert cache["pl1"]["snapshot_id"] == "abc"
    assert cache["pl1"]["tracks"][0]["album"] == "alb"


def test_drops_playlist_whose_tracks_lack_album(tmp_path):
    """Regression: Kitchen cache had tracks without an ``album`` key.
    snapshot-id matching would reuse them indefinitely. _load_cache
    must drop the stale-schema entry so the playlist refetches."""
    data = [
        {
            "id": "old", "snapshot_id": "xyz",
            "tracks": [
                {"name": "I Will Always Love You", "artist": "Whitney",
                 "uri": "spotify:track:4eHbdreAnSOrDDsFfc4Fpm",
                 "image": "http://a.jpg"},  # no album key
            ],
        },
        {
            "id": "fresh", "snapshot_id": "lmn",
            "tracks": [
                {"name": "t1", "artist": "a", "album": "alb", "uri": "u"},
            ],
        },
    ]
    p = _write(tmp_path, data)
    cache, stale = _load_cache(str(p))
    assert stale == 1
    assert "old" not in cache
    assert "fresh" in cache


def test_empty_tracks_list_is_kept(tmp_path):
    """A playlist with zero tracks has nothing to schema-check —
    keep it in the cache (no refetch needed just because it's empty)."""
    data = [
        {"id": "empty", "snapshot_id": "z", "tracks": []},
    ]
    p = _write(tmp_path, data)
    cache, stale = _load_cache(str(p))
    assert stale == 0
    assert "empty" in cache


def test_missing_snapshot_id_defaults_to_empty(tmp_path):
    data = [
        {
            "id": "pl1",  # no snapshot_id
            "tracks": [
                {"name": "t1", "artist": "a", "album": "alb", "uri": "u"},
            ],
        },
    ]
    p = _write(tmp_path, data)
    cache, _ = _load_cache(str(p))
    assert cache["pl1"]["snapshot_id"] == ""
