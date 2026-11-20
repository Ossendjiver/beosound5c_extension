"""Unified YouTube history: validation, idempotency, and persistence."""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services"))
from lib.youtube_history import YouTubeHistory, validate_event


def sample(**overrides):
    event = {
        "video_id": "dQw4w9WgXcQ", "device_id": "bedroom",
        "title": "Example", "channel": "Channel",
        "position_ms": 30000, "duration_ms": 120000,
        "watched_at_ms": 1000000, "ended": False,
    }
    event.update(overrides)
    return event


def test_validation_limits_and_unknown_fields():
    assert validate_event(sample(), now_ms=2000000)["video_id"] == "dQw4w9WgXcQ"
    for event in (
        sample(video_id="invalid"),
        sample(device_id="../bedroom"),
        sample(position_ms=True),
        sample(duration_ms=-1),
        sample(ended=1),
        sample(title="x"*301),
        sample(url="https://example.com"),
        sample(watched_at_ms=2400001),
    ):
        with pytest.raises(ValueError):
            validate_event(event, now_ms=2000000)


def test_persistent_history_latest_wins_and_deduplicates(monkeypatch):
    monkeypatch.setattr("lib.youtube_history.time.time", lambda: 2000.0)
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "history.sqlite3"
        store = YouTubeHistory(path)
        assert store.upsert(sample())
        assert not store.upsert(sample())
        assert not store.upsert(sample(watched_at_ms=999999, position_ms=0))
        assert store.upsert(sample(watched_at_ms=1001000, position_ms=45000,
                                   device_id="lounge", title=""))
        item, = store.entries(video_id="dQw4w9WgXcQ")
        assert item["position_ms"] == 45000
        assert item["title"] == "Example"
        assert item["device_id"] == "lounge"
        assert len(store.entries()) == 1
        store.close()
        reopened = YouTubeHistory(path)
        assert reopened.entries()[0]["position_ms"] == 45000
        reopened.close()


def test_no_existing_music_tables_are_modified(monkeypatch):
    monkeypatch.setattr("lib.youtube_history.time.time", lambda: 2000.0)
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "youtube.sqlite3"
        store = YouTubeHistory(path)
        store.upsert(sample())
        tables = {row[0] for row in store.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "youtube_history" in tables
        assert "listens" not in tables
        store.close()


def test_dated_rewatches_and_historical_backfill_are_preserved(monkeypatch):
    monkeypatch.setattr("lib.youtube_history.time.time", lambda: 2000.0)
    with tempfile.TemporaryDirectory() as folder:
        store = YouTubeHistory(Path(folder) / "youtube.sqlite3")
        assert store.upsert(sample(watched_at_ms=1700000, position_ms=110000, ended=True))
        assert store.upsert(sample(watched_at_ms=1800000, position_ms=70000))
        # Import an older Google Takeout event: keep the real date, but never
        # replace the more recent 70-second live resume point.
        historical = sample(device_id="takeout", watched_at_ms=1200000,
                            position_ms=0, duration_ms=0)
        assert store.import_events([historical, historical]) == 2
        assert store.entries()[0]["watched_at_ms"] == 1800000
        assert store.entries()[0]["position_ms"] == 70000
        events = store.entries(events=True)
        assert {e["watched_at_ms"] for e in events} == {1200000, 1700000, 1800000}
        assert len(events) == 3
        store.close()


def test_invalid_batch_is_atomic_and_cannot_mutate_live_history(monkeypatch):
    monkeypatch.setattr("lib.youtube_history.time.time", lambda: 2000.0)
    with tempfile.TemporaryDirectory() as folder:
        store = YouTubeHistory(Path(folder) / "youtube.sqlite3")
        with pytest.raises(ValueError):
            store.import_events([sample(device_id="takeout"), sample(video_id="bad")])
        assert not store.entries(events=True)
        store.close()
