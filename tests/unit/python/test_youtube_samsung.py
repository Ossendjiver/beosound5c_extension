"""Verified Samsung HA watch events; no invented playback from TV launch status."""
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services"))
from lib.youtube_samsung import sample_youtube_state, video_id_from_content, _private_ha_api_url


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
