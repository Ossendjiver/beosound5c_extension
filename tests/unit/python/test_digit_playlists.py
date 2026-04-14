"""Tests for lib.digit_playlists — 0-9 playlist number mapping.

The "pin by name, fill the rest alphabetically" semantics are
non-obvious and have broken at least once (commit 9e268be — digit
playlists file path was an instance attr instead of class attr).
This file pins the behaviours:

  * Pinned digits take precedence.
  * The first pinned match for a digit wins.
  * Pinned playlists are not duplicated into remaining slots.
  * Remaining slots fill in input order.
  * DigitPlaylistMixin caches on first read and survives missing files.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.digit_playlists import (
    DigitPlaylistMixin,
    build_digit_mapping,
    detect_digit_playlist,
)


class TestDetectDigitPlaylist:
    @pytest.mark.parametrize("name,expected", [
        ("5: Jazz Classics", "5"),
        ("5 - Jazz", "5"),
        ("3:Quick",    "3"),           # no space
        ("0 : Zero",    "0"),
        ("9 -- Dashes", "9"),
        ("No digit prefix", None),
        ("12: Too many digits",  None),  # only 1-digit match counts
        ("",             None),
        ("5no separator", None),        # digit not followed by : or -
    ])
    def test_detection(self, name, expected):
        assert detect_digit_playlist(name) == expected


class TestBuildDigitMapping:
    def test_explicit_digits_pinned(self):
        playlists = [
            {"id": "a", "name": "5: Jazz"},
            {"id": "b", "name": "3: Rock"},
        ]
        m = build_digit_mapping(playlists)
        assert m["5"]["id"] == "a"
        assert m["3"]["id"] == "b"
        # No other slots filled
        assert set(m.keys()) == {"3", "5"}

    def test_fills_remaining_alphabetically_in_input_order(self):
        """Unpinned playlists fill the remaining slots in the order
        they appear in the input list (caller typically sorts
        alphabetically)."""
        playlists = [
            {"id": "p1", "name": "Alpha"},
            {"id": "p2", "name": "Bravo"},
            {"id": "p3", "name": "Charlie"},
        ]
        m = build_digit_mapping(playlists)
        assert m["0"]["id"] == "p1"
        assert m["1"]["id"] == "p2"
        assert m["2"]["id"] == "p3"
        assert "3" not in m  # only 3 playlists, remaining slots empty

    def test_pinned_skipped_when_filling_remaining(self):
        playlists = [
            {"id": "p1", "name": "Alpha"},
            {"id": "pinned", "name": "5: Pinned Jazz"},
            {"id": "p3", "name": "Bravo"},
        ]
        m = build_digit_mapping(playlists)
        assert m["5"]["id"] == "pinned"
        # Pinned playlist does not appear anywhere else
        other_ids = {v["id"] for k, v in m.items() if k != "5"}
        assert "pinned" not in other_ids
        # Remaining slots still get p1, p3 in order
        assert m["0"]["id"] == "p1"
        assert m["1"]["id"] == "p3"

    def test_first_pinned_wins_on_collision(self):
        """Two playlists both prefixed '5:' — only the first wins."""
        playlists = [
            {"id": "first", "name": "5: First"},
            {"id": "second", "name": "5: Second"},
        ]
        m = build_digit_mapping(playlists)
        assert m["5"]["id"] == "first"
        # "second" becomes a regular playlist and fills the next slot
        other = [v["id"] for k, v in m.items() if k != "5"]
        assert "second" in other

    def test_preserves_url_field_when_present(self):
        playlists = [{"id": "a", "name": "Play", "url": "https://x"}]
        m = build_digit_mapping(playlists)
        assert m["0"]["url"] == "https://x"

    def test_omits_url_field_when_absent(self):
        playlists = [{"id": "a", "name": "Play"}]
        m = build_digit_mapping(playlists)
        assert "url" not in m["0"]

    def test_empty_input(self):
        assert build_digit_mapping([]) == {}


class _FakeSource(DigitPlaylistMixin):
    """Minimal subclass that mirrors the real usage: a class-level file path
    attribute set by the source service on startup."""
    def __init__(self, path):
        self.DIGIT_PLAYLISTS_FILE = str(path)
        self._digit_cache = None  # shadow the class attr on each instance


class TestDigitPlaylistMixin:
    def test_reload_populates_cache(self, tmp_path):
        f = tmp_path / "digits.json"
        f.write_text(json.dumps({
            "0": {"id": "a", "name": "Alpha"},
            "5": {"id": "b", "name": "5: Jazz"},
        }))
        s = _FakeSource(f)
        s._reload_digit_playlists()
        assert s._digit_cache["0"]["id"] == "a"
        assert s._digit_cache["5"]["name"] == "5: Jazz"

    def test_missing_file_gives_empty_cache(self, tmp_path):
        s = _FakeSource(tmp_path / "nope.json")
        s._reload_digit_playlists()
        assert s._digit_cache == {}

    def test_get_digit_loads_cache_lazily(self, tmp_path):
        f = tmp_path / "digits.json"
        f.write_text(json.dumps({"5": {"id": "b", "name": "Jazz"}}))
        s = _FakeSource(f)
        # No _reload call — _get_digit_playlist must trigger it.
        assert s._get_digit_playlist("5")["id"] == "b"
        assert s._digit_cache is not None

    def test_get_digit_returns_none_for_missing(self, tmp_path):
        f = tmp_path / "digits.json"
        f.write_text("{}")
        s = _FakeSource(f)
        assert s._get_digit_playlist("7") is None

    def test_get_digit_returns_none_for_malformed_entry(self, tmp_path):
        """An entry without an ``id`` should be treated as empty."""
        f = tmp_path / "digits.json"
        f.write_text(json.dumps({"5": {"name": "no id"}}))
        s = _FakeSource(f)
        assert s._get_digit_playlist("5") is None

    def test_get_digit_names(self, tmp_path):
        f = tmp_path / "digits.json"
        f.write_text(json.dumps({
            "0": {"id": "a", "name": "Alpha"},
            "5": {"id": "b", "name": "Jazz"},
            "9": {"id": None, "name": "Broken"},  # no id — skipped
        }))
        s = _FakeSource(f)
        names = s._get_digit_names()
        assert names == {"0": "Alpha", "5": "Jazz", "9": "Broken"}
