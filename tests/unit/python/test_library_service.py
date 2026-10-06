"""Tests for the local contextual recommendation/library service."""

import datetime as dt
import time

import library as lib


def _model(tmp_path):
    return lib.LocalModel(tmp_path / "library.sqlite3")


def test_time_weather_context_helpers():
    assert lib._time_bucket(7) == "morning"
    assert lib._time_bucket(19) == "evening"
    assert lib._weather_bucket("pouring rain") == "rain"
    assert lib._weather_bucket("sunny") == "clear"


def test_rank_prefers_artist_learned_in_matching_context(tmp_path):
    model = _model(tmp_path)
    context = {
        "hour": 19,
        "time_bucket": "evening",
        "weekpart": "weekday",
        "weather": "rain",
        "temperature": 14.0,
        "room": "lounge",
    }
    for _ in range(4):
        model.record_listen(
            {"title": "Known good", "artist": "Artist A", "album": "", "uri": "track://a", "duration": 200},
            160,
            context,
        )
    ranked = model.rank(
        [
            {"name": "New A", "artist": "Artist A", "uri": "track://new-a"},
            {"name": "New B", "artist": "Artist B", "uri": "track://new-b"},
        ],
        context,
        2,
    )
    assert ranked[0]["artist"] == "Artist A"
    model.db.close()


def test_fast_skip_is_negative_training_signal(tmp_path):
    model = _model(tmp_path)
    context = {
        "hour": 12,
        "time_bucket": "day",
        "weekpart": "weekday",
        "weather": "clear",
        "temperature": 22.0,
        "room": "kitchen",
    }
    model.record_listen(
        {"title": "Skip", "artist": "Artist A", "uri": "track://skip", "duration": 200},
        8,
        context,
    )
    model.record_listen(
        {"title": "Listen", "artist": "Artist B", "uri": "track://listen", "duration": 200},
        160,
        context,
    )
    ranked = model.rank(
        [
            {"name": "Another A", "artist": "Artist A", "uri": "track://a2"},
            {"name": "Another B", "artist": "Artist B", "uri": "track://b2"},
        ],
        context,
        2,
    )
    assert ranked[0]["artist"] == "Artist B"
    model.db.close()


def test_post_run_yoga_file_selector_prefers_seven_minute_video():
    files = [
        {"filetype": "file", "label": "Yoga For Runners Post-Run Yoga.mp4", "file": "smb://x/long.mp4"},
        {"filetype": "file", "label": "7min Post Run Yoga.mp4", "file": "smb://x/7min.mp4"},
        {"filetype": "file", "label": "Pre-Run Yoga.mp4", "file": "smb://x/pre.mp4"},
    ]
    pick = lib.LibraryService._choose_post_run_yoga(files)
    assert pick["file"].endswith("7min.mp4")


def test_recent_garmin_run_detected(mock_config, tmp_path, monkeypatch):
    monkeypatch.setattr(lib, "DB_PATH", tmp_path / "service.sqlite3")
    mock_config({
        "device": "Test",
        "library": {"garmin_run_entities": ["sensor.garmin_last_activity"], "post_run_window_minutes": 90},
    })
    service = lib.LibraryService()
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    states = [{
        "entity_id": "sensor.garmin_last_activity",
        "state": "Running",
        "last_changed": now,
        "attributes": {"friendly_name": "Garmin last activity", "activity_type": "running"},
    }]
    active, marker = service._detect_recent_run(states)
    assert active
    assert marker.startswith("sensor.garmin_last_activity:")
    service.model.db.close()
