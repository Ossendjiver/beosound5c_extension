"""Tests for Spotify scope drift detection.

When a user authorised the app before a new scope was added to
``SPOTIFY_SCOPES``, refreshing their token will not re-grant the new
scope — Spotify ties scopes to the original authorization grant.  The
service has to compare granted vs. expected and surface a "re-auth
needed" warning, otherwise the symptom is silent (e.g. "only liked
songs sync, no playlists" when ``playlist-read-private`` is missing).

``missing_scopes`` is the small helper that powers that comparison.
"""
from __future__ import annotations

from sources.spotify.spotify_auth import missing_scopes


def test_no_missing_when_granted_is_superset():
    granted = "playlist-read-private user-library-read streaming"
    required = "user-library-read streaming"
    assert missing_scopes(granted, required) == []


def test_no_missing_when_granted_equals_required():
    granted = "a b c"
    required = "c b a"  # order shouldn't matter
    assert missing_scopes(granted, required) == []


def test_reports_missing_scope():
    granted = "user-library-read streaming"
    required = "playlist-read-private user-library-read streaming"
    assert missing_scopes(granted, required) == ["playlist-read-private"]


def test_reports_multiple_missing_scopes_sorted():
    granted = "streaming"
    required = "user-library-read playlist-read-private streaming"
    # Sorted output keeps log lines stable across runs.
    assert missing_scopes(granted, required) == [
        "playlist-read-private", "user-library-read"]


def test_empty_granted_returns_full_required():
    """Token issued before scope tracking — granted is None.  Treat as
    'we don't know what was granted', i.e. assume nothing."""
    assert missing_scopes(None, "a b") == ["a", "b"]
    assert missing_scopes("", "a b") == ["a", "b"]


def test_empty_required_returns_empty():
    assert missing_scopes("a b", "") == []
    assert missing_scopes("a b", None) == []


def test_case_insensitive():
    """Spotify wire format is lowercase, but compare insensitively so
    a future Spotify casing change doesn't trigger a false alarm."""
    granted = "Playlist-Read-Private USER-LIBRARY-READ"
    required = "playlist-read-private user-library-read"
    assert missing_scopes(granted, required) == []


def test_extra_whitespace_tolerated():
    granted = "  playlist-read-private   user-library-read  "
    required = "playlist-read-private user-library-read"
    assert missing_scopes(granted, required) == []
