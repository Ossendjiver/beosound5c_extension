"""Tests for explicit digit pins (Config UI favourites) in
lib.digit_playlists.build_digit_mapping.

Per-slot precedence pinned here:
  1. explicit pins from spotify_favourites.json,
  2. name convention ("5: Jazz" pins to 5),
  3. alphabetical fill (input order) for whatever is left.

A pinned playlist id that no longer exists in the fetched playlists is
skipped — the slot falls through to convention/alphabetical.
"""

import json

from lib.digit_playlists import build_digit_mapping, load_digit_pins


PLAYLISTS = [
    {"id": "alpha", "name": "Alpha"},
    {"id": "bravo", "name": "Bravo"},
    {"id": "jazz", "name": "5: Jazz"},
]


class TestPinPrecedence:
    def test_pin_beats_name_convention(self):
        """An explicit pin on slot 5 wins over the '5: Jazz' playlist."""
        pins = {"5": {"id": "alpha", "name": "Alpha"}}
        m = build_digit_mapping(PLAYLISTS, pins=pins)
        assert m["5"]["id"] == "alpha"
        # The convention playlist isn't dropped — it joins the fill pool.
        assert "jazz" in {v["id"] for v in m.values()}

    def test_missing_pinned_id_falls_through_to_convention(self):
        """Pin points at a deleted playlist — slot 5 falls back to the
        '5: Jazz' name convention as if unpinned."""
        pins = {"5": {"id": "deleted-playlist", "name": "Gone"}}
        m = build_digit_mapping(PLAYLISTS, pins=pins)
        assert m["5"]["id"] == "jazz"

    def test_unpinned_slots_fill_alphabetically_as_before(self):
        pins = {"3": {"id": "bravo", "name": "Bravo"}}
        m = build_digit_mapping(PLAYLISTS, pins=pins)
        assert m["3"]["id"] == "bravo"    # explicit pin
        assert m["5"]["id"] == "jazz"     # name convention still applies
        assert m["0"]["id"] == "alpha"    # fill in input order
        # Pinned/convention playlists are not duplicated into the fill.
        assert set(m) == {"0", "3", "5"}

    def test_pinned_playlist_not_duplicated_into_fill(self):
        pins = {"1": {"id": "alpha", "name": "Alpha"}}
        m = build_digit_mapping(PLAYLISTS, pins=pins)
        assert m["1"]["id"] == "alpha"
        assert m["0"]["id"] == "bravo"    # fill skips the pinned playlist
        assert [v["id"] for v in m.values()].count("alpha") == 1

    def test_same_playlist_pinned_to_two_slots_is_honoured(self):
        """Explicit pins are the user's word — duplicates are allowed."""
        pins = {"1": {"id": "alpha"}, "2": {"id": "alpha"}}
        m = build_digit_mapping(PLAYLISTS, pins=pins)
        assert m["1"]["id"] == "alpha"
        assert m["2"]["id"] == "alpha"

    def test_no_pins_matches_legacy_behaviour(self):
        assert build_digit_mapping(PLAYLISTS) == \
            build_digit_mapping(PLAYLISTS, pins={})


class TestLoadDigitPins:
    def test_loads_valid_pins(self, tmp_path):
        f = tmp_path / "spotify_favourites.json"
        f.write_text(json.dumps({
            "5": {"id": "abc", "name": "Dinner"},
            "0": {"id": "xyz"},
        }))
        pins = load_digit_pins(str(f))
        assert pins == {
            "5": {"id": "abc", "name": "Dinner"},
            "0": {"id": "xyz", "name": ""},
        }

    def test_missing_file_gives_empty(self, tmp_path):
        assert load_digit_pins(str(tmp_path / "nope.json")) == {}

    def test_malformed_json_gives_empty(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("{not json")
        assert load_digit_pins(str(f)) == {}

    def test_non_object_gives_empty(self, tmp_path):
        f = tmp_path / "list.json"
        f.write_text("[1, 2, 3]")
        assert load_digit_pins(str(f)) == {}

    def test_drops_bad_slots_and_idless_entries(self, tmp_path):
        f = tmp_path / "mixed.json"
        f.write_text(json.dumps({
            "5": {"id": "keep"},
            "x": {"id": "bad-slot"},
            "12": {"id": "two-chars"},
            "7": {"name": "no id"},
            "8": None,
        }))
        pins = load_digit_pins(str(f))
        assert set(pins) == {"5"}
