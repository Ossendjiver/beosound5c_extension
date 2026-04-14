"""Tests for ``lib.spotify_canvas.extract_spotify_track_id`` and
``normalize_spotify_track_uri``.

The Sonos player exposes track URIs like
``x-sonos-spotify:spotify%3atrack%3a01RdEXps15f3VmQMV6OuTM?sid=9&flags=8232&sn=9``
while the Spotify canvas API only accepts canonical
``spotify:track:01RdEXps15f3VmQMV6OuTM``. Before this helper existed,
the router → spotify-source canvas HTTP path was silently broken for
every player-originated broadcast: the URL builder shipped a bare
track id under ``?track_id=`` while the handler read ``?uri=``, so
the canvas service always saw an empty input and returned no canvas.

These tests lock the contract of the normalizer so the same class of
silent breakage can't return through any of the URI shapes we've
seen on real devices."""

from __future__ import annotations

import pytest

from lib.spotify_canvas import extract_spotify_track_id, normalize_spotify_track_uri


VALID_ID = "01RdEXps15f3VmQMV6OuTM"   # 22-char base62, real Spotify id format


class TestExtractSpotifyTrackId:
    def test_canonical_uri(self):
        assert extract_spotify_track_id(f"spotify:track:{VALID_ID}") == VALID_ID

    def test_sonos_wrapped_uri_url_encoded(self):
        """The exact URI shape Sonos returns from get_current_track_info()."""
        sonos = (f"x-sonos-spotify:spotify%3atrack%3a{VALID_ID}"
                 f"?sid=9&flags=8232&sn=9")
        assert extract_spotify_track_id(sonos) == VALID_ID

    def test_sonos_wrapped_uri_already_decoded(self):
        sonos = f"x-sonos-spotify:spotify:track:{VALID_ID}?sid=9"
        assert extract_spotify_track_id(sonos) == VALID_ID

    def test_open_spotify_https_url(self):
        assert extract_spotify_track_id(
            f"https://open.spotify.com/track/{VALID_ID}?si=abc") == VALID_ID

    def test_bare_22_char_id(self):
        assert extract_spotify_track_id(VALID_ID) == VALID_ID

    def test_short_id_rejected(self):
        """Anything that's not exactly 22 chars and isn't inside a
        recognised URI shape must be rejected — we don't want random
        strings being treated as track ids."""
        assert extract_spotify_track_id("01RdEXps") is None

    def test_long_random_string_rejected(self):
        assert extract_spotify_track_id("a" * 30) is None

    def test_empty_string(self):
        assert extract_spotify_track_id("") is None

    def test_none(self):
        assert extract_spotify_track_id(None) is None

    def test_non_spotify_uri(self):
        assert extract_spotify_track_id(
            "x-rincon-mp3radio://example.com/stream") is None

    def test_album_uri_rejected(self):
        """Album URIs use spotify:album:<id> — must NOT match the
        track regex even though the prefix is similar."""
        assert extract_spotify_track_id(f"spotify:album:{VALID_ID}") is None

    def test_playlist_uri_rejected(self):
        assert extract_spotify_track_id(f"spotify:playlist:{VALID_ID}") is None


class TestNormalizeSpotifyTrackUri:
    def test_returns_canonical_form(self):
        assert normalize_spotify_track_uri(
            f"x-sonos-spotify:spotify%3atrack%3a{VALID_ID}?sid=9"
        ) == f"spotify:track:{VALID_ID}"

    def test_passthrough_canonical(self):
        canonical = f"spotify:track:{VALID_ID}"
        assert normalize_spotify_track_uri(canonical) == canonical

    def test_bare_id_promoted(self):
        assert normalize_spotify_track_uri(VALID_ID) == f"spotify:track:{VALID_ID}"

    def test_unrecognized_returns_none(self):
        assert normalize_spotify_track_uri("garbage") is None

    def test_empty_returns_none(self):
        assert normalize_spotify_track_uri("") is None
