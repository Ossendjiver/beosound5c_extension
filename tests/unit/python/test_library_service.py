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
            {"title": "Known good", "artist": "Artist A", "album": "", "uri": "track://a", "duration": 200, "selection_origin": "manual"},
            160,
            context,
        )
    ranked = model.rank(
        [
            {"name": "New A", "artist": "Artist A", "uri": "track://new-a", "trusted": True},
            {"name": "New B", "artist": "Artist B", "uri": "track://new-b", "trusted": True},
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
        {"title": "Skip", "artist": "Artist A", "uri": "track://skip", "duration": 200, "selection_origin": "manual", "end_reason": "skip"},
        8,
        context,
    )
    model.record_listen(
        {"title": "Listen", "artist": "Artist B", "uri": "track://listen", "duration": 200, "selection_origin": "manual"},
        160,
        context,
    )
    ranked = model.rank(
        [
            {"name": "Skip", "artist": "Artist A", "uri": "track://skip", "trusted": True},
            {"name": "Listen", "artist": "Artist B", "uri": "track://listen", "trusted": True},
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


def test_cold_start_does_not_sample_untrusted_catalogue(tmp_path):
    m = _model(tmp_path)
    assert m.rank([{"name": "Random", "artist": "Other", "uri": "x"}], {}, 20) == []


def test_seeded_mix_has_ninety_percent_familiar_and_no_unrelated_artists(tmp_path):
    m = _model(tmp_path)
    familiar = [{"name": f"Favourite {i}", "artist": "Known", "uri": f"f{i}", "favorite": True} for i in range(30)]
    related = [{"name": f"Discovery {i}", "artist": "Known", "uri": f"d{i}"} for i in range(10)]
    other = [{"name": "Trash", "artist": "Stranger", "uri": "trash"}]
    picks = m.rank(familiar + related + other, {}, 20)
    assert len(picks) == 20
    assert sum(bool(p.get("favorite")) for p in picks) == 18
    assert all(p["artist"] == "Known" for p in picks)
    small = m.rank(familiar[:9] + related, {}, 20)
    assert len(small) == 10 and sum(bool(p.get("favorite")) for p in small) == 9


def test_no_seed_expansion_from_automatic_or_legacy_playback(tmp_path):
    m = _model(tmp_path)
    for origin in ["automatic", "unknown", "legacy"]:
        m.record_listen({"title": "Played", "artist": "Bad", "uri": "bad", "selection_origin": origin}, 150, {})
    assert m.rank([{"name": "Played", "artist": "Bad", "uri": "bad"}], {}, 20) == []


def test_spoken_audio_does_not_train_or_enter_music_pool(tmp_path):
    m = _model(tmp_path)
    spoken = {"name": "Meditation", "title": "Meditation", "artist": "Sleep Cove", "uri": "spoken", "favorite": True}
    m.record_listen(spoken, 300, {})
    assert m._history() == []
    assert m.rank([spoken], {}, 20) == []
    assert not lib._music_item({"media_type": "podcast_episode", "name": "Episode"})


def test_short_interruptions_are_neutral_and_manual_selection_is_stronger(tmp_path):
    m = _model(tmp_path)
    for reason in ["pause", "transfer", "stop", "unknown"]:
        m.record_listen({"title": reason, "artist": "Known", "end_reason": reason}, 10, {})
    assert all(r["reward"] == 0 for r in m._history())
    for origin in ["manual", "automatic"]:
        m.record_listen({"title": origin, "artist": "Known", "selection_origin": origin}, 150, {})
    rows = {r["title"]: r for r in m._history()}
    assert rows["manual"]["reward"] > rows["automatic"]["reward"]


def test_explicit_dislike_and_duplicate_identities(tmp_path):
    m = _model(tmp_path)
    candidates = [{"name": "Same", "artist": "Known", "uri": "provider", "trusted": True},
                  {"name": "Same", "artist": "Known", "uri": "library", "favorite": True}]
    assert len(m.rank(candidates, {}, 20)) == 1
    m.put_kv("music_feedback", {"library": "dislike"})
    assert m.rank(candidates, {}, 20) == []


def test_trusted_playlist_membership_is_preserved(mock_config, tmp_path, monkeypatch):
    import json
    mock_config({"library": {"trusted_playlists": ["Trusted music"]}})
    monkeypatch.setattr(lib, "DB_PATH", tmp_path / "model.db")
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps([{"id":"playlists", "tracks":[{"name":"Trusted music", "tracks":[{"name":"Good", "artist":"Known", "url":"good"}]}]},
                                {"id":"songs", "tracks":[{"name":"Good", "artist":"Known", "url":"good"}, {"name":"Random", "artist":"Other", "url":"bad"}]}]))
    monkeypatch.setattr(lib, "CACHE_LIBRARY", cache)
    service = lib.LibraryService()
    items = service._load_library()
    assert next(i for i in items if i["uri"] == "good")["trusted"]
    assert service.model.rank(items, {}, 20)[0]["uri"] == "good"


def test_favourites_work_without_local_catalogue_cache(mock_config, tmp_path, monkeypatch):
    mock_config({})
    monkeypatch.setattr(lib, "DB_PATH", tmp_path / "model.db")
    monkeypatch.setattr(lib, "CACHE_LIBRARY", tmp_path / "missing.json")
    service = lib.LibraryService()
    service._seed_metadata = {"favorite": {"uri": "favorite", "name": "Loved", "artist": "Known", "favorite": True}}
    assert service.model.rank(service._load_library(), {}, 20)[0]["uri"] == "favorite"


def test_explicit_dislike_blocks_provider_aliases(tmp_path):
    m = _model(tmp_path)
    m.put_kv("music_feedback", {"provider": "dislike"})
    candidates = [{"name": "Same", "artist": "Known", "uri": "provider", "trusted": True},
                  {"name": "Same", "artist": "Known", "uri": "library", "favorite": True}]
    assert m.rank(candidates, {}, 20) == []


def test_legacy_history_is_preserved_but_cannot_seed_pool(tmp_path):
    import sqlite3
    path = tmp_path / "library.sqlite3"
    m = _model(tmp_path)
    m.record_listen({"title": "Old", "artist": "Old artist", "uri": "old"}, 150, {})
    m.db.execute("UPDATE listens SET reward=1")
    m.db.execute("ALTER TABLE listens DROP COLUMN origin")
    m.db.commit(); m.db.close()
    m = _model(tmp_path)
    assert len(m._history()) == 1
    assert m.rank([{"name": "Old", "artist": "Old artist", "uri": "old"}], {}, 20) == []
