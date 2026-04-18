"""Tests for SpotifyService playlist playback logic — verifies the correct URI
is sent to the player service and that track advancement doesn't wrap around
(which would cause stale metadata when librespot has the full playlist).

Runs standalone without importing the full service stack (avoids Python 3.10+
type annotation issues on the dev Mac)."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest


SAMPLE_PLAYLISTS = [
    {
        "id": "7BtiUiOcjrnjYO6ej7uYRz",
        "name": "Canvas",
        "tracks": [
            {"uri": "spotify:track:01RdEXps15f3VmQMV6OuTM",
             "name": "Tusen spänn", "artist": "Tjuvjakt", "image": "art1.jpg"},
            {"uri": "spotify:track:5xsHQu1SXYS6DYOJwIWhSC",
             "name": "Svagare än jag", "artist": "Ida-Lova", "image": "art2.jpg"},
            {"uri": "spotify:track:AAAA", "name": "Track 3", "artist": "X", "image": ""},
            {"uri": "spotify:track:BBBB", "name": "Track 4", "artist": "Y", "image": ""},
        ],
    },
    {
        "id": "liked-songs",
        "name": "Liked Songs",
        "tracks": [
            {"uri": "spotify:track:3QnOeUwRd1v1eniL3VaQTg",
             "name": "Eld och djupa vatten", "artist": "Ken Ring", "image": ""},
            {"uri": "spotify:track:CCCC",
             "name": "Another Song", "artist": "Someone", "image": ""},
        ],
    },
]


def _spotify_uri_to_url(uri):
    """Mirror of SpotifyService._spotify_uri_to_url."""
    parts = uri.split(':')
    if len(parts) == 3 and parts[0] == 'spotify':
        return "https://open.spotify.com/{}/{}".format(parts[1], parts[2])
    return uri


async def _play_playlist(svc, playlist_id, track_index=None):
    """Extracted logic from SpotifyService._play_playlist — mirrors the
    production code so we can test the URI-selection logic in isolation."""
    now = time.monotonic()
    if now - svc._last_play_time < 2 and svc._last_playlist_id == playlist_id:
        return  # debounced
    svc._last_play_time = now

    if track_index is None:
        track_index = 0

    track_uri = None
    track_meta = None
    all_track_uris = None
    for pl in svc.playlists:
        if pl.get('id') == playlist_id:
            tracks = pl.get('tracks', [])
            if 0 <= track_index < len(tracks):
                track_meta = tracks[track_index]
                track_uri = track_meta.get('uri', '')
            all_track_uris = [t['uri'] for t in tracks if t.get('uri')]
            break

    # --- This is the fix under test ---
    if playlist_id and not playlist_id.startswith(('liked', 'collection')):
        play_uri = "https://open.spotify.com/playlist/{}".format(playlist_id)
    else:
        play_uri = _spotify_uri_to_url(track_uri) if track_uri else None

    svc._last_playlist_id = playlist_id
    svc._last_track_uri = track_uri

    await svc.player_play(uri=play_uri, track_uri=track_uri, track_uris=all_track_uris)


@pytest.fixture
def svc():
    """Minimal mock service with just the fields _play_playlist needs."""
    s = MagicMock()
    s.playlists = SAMPLE_PLAYLISTS
    s.player_play = AsyncMock(return_value=True)
    s._last_play_time = 0
    s._last_playlist_id = None
    s._last_track_uri = None
    return s


class TestPlayPlaylistURI:
    """_play_playlist must send the playlist context URI for real playlists
    so librespot queues all tracks, preventing Spotify autoplay."""

    def test_real_playlist_sends_playlist_uri(self, svc):
        asyncio.run(
            _play_playlist(svc, "7BtiUiOcjrnjYO6ej7uYRz", track_index=0))

        svc.player_play.assert_called_once()
        kwargs = svc.player_play.call_args.kwargs
        assert kwargs['uri'] == "https://open.spotify.com/playlist/7BtiUiOcjrnjYO6ej7uYRz"

    def test_real_playlist_sends_skip_to_track(self, svc):
        asyncio.run(
            _play_playlist(svc, "7BtiUiOcjrnjYO6ej7uYRz", track_index=1))

        kwargs = svc.player_play.call_args.kwargs
        assert kwargs['track_uri'] == "spotify:track:5xsHQu1SXYS6DYOJwIWhSC"

    def test_liked_songs_sends_track_uri(self, svc):
        asyncio.run(
            _play_playlist(svc, "liked-songs", track_index=0))

        kwargs = svc.player_play.call_args.kwargs
        assert kwargs['uri'] == "https://open.spotify.com/track/3QnOeUwRd1v1eniL3VaQTg"

    def test_real_playlist_passes_all_track_uris(self, svc):
        asyncio.run(
            _play_playlist(svc, "7BtiUiOcjrnjYO6ej7uYRz", track_index=0))

        kwargs = svc.player_play.call_args.kwargs
        assert len(kwargs['track_uris']) == 4
        assert kwargs['track_uris'][0] == "spotify:track:01RdEXps15f3VmQMV6OuTM"

    def test_default_track_index_is_zero(self, svc):
        asyncio.run(
            _play_playlist(svc, "7BtiUiOcjrnjYO6ej7uYRz"))

        kwargs = svc.player_play.call_args.kwargs
        assert kwargs['track_uri'] == "spotify:track:01RdEXps15f3VmQMV6OuTM"

    def test_debounce_blocks_duplicate(self, svc):
        asyncio.run(
            _play_playlist(svc, "7BtiUiOcjrnjYO6ej7uYRz", track_index=0))
        asyncio.run(
            _play_playlist(svc, "7BtiUiOcjrnjYO6ej7uYRz", track_index=0))

        assert svc.player_play.call_count == 1

    def test_different_playlist_not_debounced(self, svc):
        asyncio.run(
            _play_playlist(svc, "7BtiUiOcjrnjYO6ej7uYRz", track_index=0))
        asyncio.run(
            _play_playlist(svc, "liked-songs", track_index=0))

        assert svc.player_play.call_count == 2

    def test_missing_playlist_no_crash(self, svc):
        """Playing a playlist ID not in the list should not crash."""
        asyncio.run(
            _play_playlist(svc, "nonexistent-id", track_index=0))

        kwargs = svc.player_play.call_args.kwargs
        # Still constructs a playlist URI (librespot will handle the 404)
        assert kwargs['uri'] == "https://open.spotify.com/playlist/nonexistent-id"
        assert kwargs['track_uri'] is None
        assert kwargs['track_uris'] is None


def _advance_track_uri(svc, direction):
    """Mirror of SpotifyService._advance_track_uri — must NOT wrap around
    since librespot may have more tracks than our local cache."""
    if not svc._last_playlist_id or not svc._last_track_uri:
        return
    for pl in svc.playlists:
        if pl.get('id') == svc._last_playlist_id:
            tracks = pl.get('tracks', [])
            if not tracks:
                return
            for i, track in enumerate(tracks):
                if track.get('uri') == svc._last_track_uri:
                    new_idx = i + direction
                    if 0 <= new_idx < len(tracks):
                        svc._last_track_uri = tracks[new_idx].get('uri')
                    else:
                        svc._last_track_uri = None
                    return
            break


class TestAdvanceTrackURI:
    """_advance_track_uri must NOT wrap around — librespot plays the full
    Spotify playlist which may have more tracks than our local cache."""

    def test_advance_forward(self, svc):
        svc._last_playlist_id = "7BtiUiOcjrnjYO6ej7uYRz"
        svc._last_track_uri = "spotify:track:01RdEXps15f3VmQMV6OuTM"  # track 0
        _advance_track_uri(svc, 1)
        assert svc._last_track_uri == "spotify:track:5xsHQu1SXYS6DYOJwIWhSC"  # track 1

    def test_advance_backward(self, svc):
        svc._last_playlist_id = "7BtiUiOcjrnjYO6ej7uYRz"
        svc._last_track_uri = "spotify:track:5xsHQu1SXYS6DYOJwIWhSC"  # track 1
        _advance_track_uri(svc, -1)
        assert svc._last_track_uri == "spotify:track:01RdEXps15f3VmQMV6OuTM"  # track 0

    def test_no_wrap_forward(self, svc):
        """At the last track, advancing should clear the URI, not wrap to track 0."""
        svc._last_playlist_id = "7BtiUiOcjrnjYO6ej7uYRz"
        svc._last_track_uri = "spotify:track:BBBB"  # track 3 (last)
        _advance_track_uri(svc, 1)
        assert svc._last_track_uri is None

    def test_no_wrap_backward(self, svc):
        """At the first track, going back should clear the URI, not wrap to last."""
        svc._last_playlist_id = "7BtiUiOcjrnjYO6ej7uYRz"
        svc._last_track_uri = "spotify:track:01RdEXps15f3VmQMV6OuTM"  # track 0
        _advance_track_uri(svc, -1)
        assert svc._last_track_uri is None

    def test_unknown_uri_no_crash(self, svc):
        """If current URI isn't in the playlist, nothing should change."""
        svc._last_playlist_id = "7BtiUiOcjrnjYO6ej7uYRz"
        svc._last_track_uri = "spotify:track:UNKNOWN"
        _advance_track_uri(svc, 1)
        assert svc._last_track_uri == "spotify:track:UNKNOWN"

    def test_no_playlist_id_no_crash(self, svc):
        svc._last_playlist_id = None
        svc._last_track_uri = "spotify:track:01RdEXps15f3VmQMV6OuTM"
        _advance_track_uri(svc, 1)
        assert svc._last_track_uri == "spotify:track:01RdEXps15f3VmQMV6OuTM"
