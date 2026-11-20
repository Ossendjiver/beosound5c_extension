"""Verified Samsung HA watch events; no invented playback from TV launch status."""
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services"))
from lib.youtube_samsung import (
    sample_youtube_state, video_id_from_content, _private_ha_api_url,
    is_user_home, samsung_history_enabled, SamsungWatchMonitor,
)


NOW = 1_800_000_000_000


def state(**attrs):
    default = {
        "media_content_id": "https://www.youtube.com/watch?v=abcdefghijk",
        "media_position": 54.5, "media_duration": 600,
        "media_title": "The video", "media_artist": "Channel",
    }
    default.update(attrs)
    return {
        "state": "playing", "attributes": default,
        "last_updated": dt.datetime.fromtimestamp(NOW / 1000, dt.timezone.utc).isoformat(),
    }


def test_confirmed_samsung_session_is_a_real_watch_event():
    event = sample_youtube_state(state(), now_ms=NOW)
    assert event == {
        "video_id": "abcdefghijk", "device_id": "lounge", "title": "The video",
        "channel": "Channel", "position_ms": 54500, "duration_ms": 600000,
        "watched_at_ms": NOW, "ended": False,
    }


def test_missing_position_video_identity_or_freshness_is_never_watched():
    sample = state()
    assert sample_youtube_state({**sample, "state": "on"}, now_ms=NOW) is None
    assert sample_youtube_state(state(media_position=0), now_ms=NOW) is None
    assert sample_youtube_state(state(media_content_id=""), now_ms=NOW) is None
    assert sample_youtube_state(state(media_content_id="https://attacker.test/watch?v=abcdefghijk"), now_ms=NOW) is None
    assert sample_youtube_state(state(), now_ms=NOW + 3600_000) is None


def test_only_local_ha_api_targets_are_allowed():
    assert _private_ha_api_url("http://homeassistant.local:8123") == "http://homeassistant.local:8123/api/states/"
    assert _private_ha_api_url("http://192.168.4.2:8123/api") == "http://192.168.4.2:8123/api/states/"
    assert _private_ha_api_url("https://external.example.com") == ""
    assert video_id_from_content("abcdefghijk") == "abcdefghijk"
    assert video_id_from_content("https://youtu.be/abcdefghijk") == "abcdefghijk"


def test_samsung_opt_in_defaults_off_and_presence_fails_closed():
    assert not samsung_history_enabled(None)
    assert not samsung_history_enabled({})
    assert not samsung_history_enabled({"include_samsung": "true"})
    assert not samsung_history_enabled({"include_samsung": 1})
    assert not samsung_history_enabled({"include_samsung": False})
    assert samsung_history_enabled({"include_samsung": True})

    for wrong in (None, {}, {"entity_id": "person.thomas", "state": "not_home"},
                  {"entity_id": "person.thomas", "state": "unavailable"},
                  {"entity_id": "person.thomas", "state": "unknown"},
                  {"entity_id": "person.someone_else", "state": "home"}):
        assert not is_user_home(wrong)
    assert is_user_home({"entity_id": "person.thomas", "state": "home"})


def test_samsung_monitor_checks_presence_before_and_after_video(monkeypatch):
    import asyncio
    from lib import youtube_samsung

    monkeypatch.setattr(youtube_samsung.time, "time", lambda: NOW / 1000)

    class Response:
        def __init__(self, state):
            self.state = state
            self.status = 200
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            return False
        async def json(self):
            return self.state

    class Session:
        def __init__(self, values):
            self.values = iter(values)
            self.requests = []
        def get(self, url, **_kwargs):
            self.requests.append(url)
            return Response(next(self.values))

    class History:
        def __init__(self):
            self.events = []
        def upsert(self, event):
            self.events.append(event)

    home = {"entity_id": "person.thomas", "state": "home"}
    away = {"entity_id": "person.thomas", "state": "not_home"}
    video = state()

    def monitor(enabled, values):
        history = History()
        session = Session(values)
        watcher = SamsungWatchMonitor(history, session, host="http://ha.local/api/states/", token="redacted")
        monkeypatch.setattr(watcher, "enabled", lambda: enabled)
        asyncio.run(watcher.sample())
        return history.events, session.requests

    entries, requests = monitor(False, [])
    assert not entries and not requests
    entries, requests = monitor(True, [away])
    assert not entries and requests == ["http://ha.local/api/states/person.thomas"]
    entries, requests = monitor(True, [home, video, away])
    assert not entries and requests[-1].endswith("/person.thomas")
    entries, requests = monitor(True, [home, video, home])
    assert len(entries) == 1
    assert entries[0]["device_id"] == "lounge"
    assert entries[0]["position_ms"] == 54500
