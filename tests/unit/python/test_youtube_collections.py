"""Versioned YouTube collections cannot overwrite concurrent changes or resurrect deletes."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services"))
from lib.youtube_history import YouTubeHistory
from lib.youtube_collections import Collections, validate_change


def change(id="UC1234567890123456789012", base=0, deleted=False, **item):
    return {"id": id, "device_id": "bedroom", "base_version": base,
            "deleted": deleted, "item": item or {"id": id, "title": "Channel"}}


def test_versions_conflicts_and_tombstones(tmp_path):
    store = YouTubeHistory(tmp_path / "youtube.sqlite3")
    collections = Collections(store.db)
    first = collections.change("follows", change())
    assert first["accepted"] and first["version"] == 1
    assert collections.change("follows", change())["conflict"]
    assert collections.change("follows", change(base=1, deleted=True, **{"id": "UC1234567890123456789012"}))["version"] == 2
    assert collections.snapshot("follows")[0]["deleted"] is True
    assert collections.change("follows", change(base=1))["conflict"]
    store.close()


def test_home_playlists_separate_from_smarttube(tmp_path):
    store = YouTubeHistory(tmp_path / "youtube.sqlite3")
    collections = Collections(store.db)
    p = {"id": "local-list-1", "name": "For later",
         "videos": [{"videoId": "dQw4w9WgXcQ", "title": "Example"}]}
    assert collections.change("home_playlists", change(id="local-list-1", name=p["name"], videos=p["videos"]))["accepted"]
    assert collections.snapshot("playlists") == []
    assert len(collections.snapshot("home_playlists")) == 1
    with pytest.raises(ValueError):
        validate_change(change(id="local-list-1", videos=[{"videoId": "oops"}]), "home_playlists")
    store.close()
