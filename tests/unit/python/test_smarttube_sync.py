"""SmartTube to BS5c adapter contract tests without a Kodi installation."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "integrations/kodi/service.bs5c.youtube.history/resources/lib"))
from sync import event_for, sync_once, read_history


def row(**values):
    result = {"id": "dQw4w9WgXcQ", "title": "Video", "channel": "Artist",
              "position": 31, "total": 100, "watched_at": 1700000000}
    result.update(values)
    return result


def test_converts_smarttube_seconds_to_milliseconds_and_marks_watched():
    e = event_for(row(position=92), "bedroom", observed_ms=1700000001000)
    assert e["position_ms"] == 92000
    assert e["duration_ms"] == 100000
    assert e["watched_at_ms"] == 1700000001000
    assert e["ended"] is True


def test_sync_ack_deduplication_and_retries(tmp_path):
    source, ack = tmp_path / "history.json", tmp_path / "acks.json"
    source.write_text(json.dumps([row()]), encoding="utf-8")
    sent = []

    def send(host, key, event):
        sent.append(event)

    args = (str(source), str(ack), "bedroom", "192.168.4.103", "k" * 40)
    assert sync_once(*args, sender=send, observed_ms=1700000001000) == 1
    assert sync_once(*args, sender=send, observed_ms=1700000002000) == 0
    assert len(sent) == 1
    source.write_text(json.dumps([row(position=50)]), encoding="utf-8")
    def failure(*_):
        raise ConnectionError("offline")
    import pytest
    with pytest.raises(ConnectionError):
        sync_once(*args, sender=failure, observed_ms=1700000003000)
    assert sync_once(*args, sender=send, observed_ms=1700000004000) == 1
    assert sent[-1]["position_ms"] == 50000


def test_missing_or_invalid_smarttube_history(tmp_path):
    assert read_history(tmp_path / "missing.json") == []
    path = tmp_path / "history.json"
    path.write_text(json.dumps([row(id="invalid"), row()]), encoding="utf-8")
    sent = []
    assert sync_once(str(path), str(tmp_path / "acks.json"), "lounge",
                     "192.168.4.103", "x"*40,
                     sender=lambda host, key, event: sent.append(event),
                     observed_ms=1700000001000) == 1
    assert sent[0]["video_id"] == "dQw4w9WgXcQ"
