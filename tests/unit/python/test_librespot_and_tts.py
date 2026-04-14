"""Tests for lib.librespot and lib.tts — small high-confidence bits.

Both modules had zero direct coverage.  Both are heavily subprocess-
and HTTP-bound, which makes full integration testing impractical
without a real pipewire/piper/librespot stack.  This file focuses on
the pure functions and module-level state, which is where actual
parsing/encoding bugs hide.
"""

from __future__ import annotations

import array

import pytest

from lib.librespot import share_url_to_uri
from lib import tts


# ── librespot: share URL parser ─────────────────────────────────────


class TestShareUrlToUri:
    @pytest.mark.parametrize("url,expected", [
        ("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M",
         "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"),
        ("https://open.spotify.com/track/0eGsygTp906u18L0Oimnem",
         "spotify:track:0eGsygTp906u18L0Oimnem"),
        ("https://open.spotify.com/album/1DFixLWuPkv3KT3TnV35m3",
         "spotify:album:1DFixLWuPkv3KT3TnV35m3"),
        ("https://open.spotify.com/artist/06HL4z0CvFAxyc27GXpf02",
         "spotify:artist:06HL4z0CvFAxyc27GXpf02"),
        # Internationalised share URLs include a locale segment
        ("https://open.spotify.com/intl-de/playlist/abcDEF123",
         "spotify:playlist:abcDEF123"),
        ("https://open.spotify.com/intl-fr/track/XYZ789abc",
         "spotify:track:XYZ789abc"),
    ])
    def test_converts_share_urls(self, url, expected):
        assert share_url_to_uri(url) == expected

    def test_passes_through_native_uri(self):
        uri = "spotify:playlist:something"
        assert share_url_to_uri(uri) == uri

    def test_returns_none_for_unrelated_url(self):
        assert share_url_to_uri("https://example.com/foo") is None

    def test_returns_none_for_empty(self):
        assert share_url_to_uri("") is None

    def test_http_scheme_allowed(self):
        # The regex allows http:// as well as https://.
        assert share_url_to_uri(
            "http://open.spotify.com/track/abc123"
        ) == "spotify:track:abc123"


# ── tts: _clean_audio (silence trim + fade-out) ──────────────────────


def _pcm(samples):
    """Pack a list of int16 samples into raw PCM bytes."""
    return array.array("h", samples).tobytes()


class TestCleanAudio:
    def test_trims_trailing_silence(self):
        # 1000 "loud" samples followed by 500 silent ones.
        audio = _pcm([3000] * 1000 + [0] * 500)
        cleaned = tts._clean_audio(audio)
        # Result must be shorter than the input (silence dropped).
        assert len(cleaned) < len(audio)
        # And not longer than 1000 samples * 2 bytes.
        assert len(cleaned) <= 2000

    def test_keeps_loud_leading_audio(self):
        audio = _pcm([5000] * 2000)
        cleaned = tts._clean_audio(audio)
        # Nothing was silent, so the cleaner shouldn't shrink beyond
        # the fade-out window (1102 samples at tail get faded, not
        # dropped).
        assert len(cleaned) == len(audio)

    def test_fade_out_zeroes_tail(self):
        """The last sample of a loud signal should be very close to 0
        after the fade-out pass."""
        audio = _pcm([10000] * 3000)
        cleaned = tts._clean_audio(audio)
        samples = array.array("h")
        samples.frombytes(cleaned)
        # Last sample should be ~0 (fade-out multiplier at i=fade_len-1
        # is (1 - (fade_len-1)/fade_len) ≈ 1/fade_len ≈ 10000/1102 ≈ 9).
        assert abs(samples[-1]) < 50, f"tail not faded: {samples[-1]}"
        # But something well before the fade window is still loud.
        assert samples[-2000] == 10000

    def test_all_silent_returns_original(self):
        """If the whole clip is below threshold, the cleaner bails and
        returns the input unchanged (division-by-zero guard)."""
        audio = _pcm([0] * 500)
        cleaned = tts._clean_audio(audio)
        assert cleaned == audio

    def test_odd_byte_length_handled(self):
        """The input may have an odd byte length (truncated last sample);
        the cleaner must not crash on that."""
        audio = _pcm([3000] * 100) + b"\x00"   # odd length
        cleaned = tts._clean_audio(audio)
        # Must return some bytes, not raise.
        assert isinstance(cleaned, (bytes, bytearray))


# ── tts: precache short-circuits on identical text ──────────────────


class TestPrecacheShortCircuit:
    def setup_method(self):
        tts._cached_text = None
        tts._cached_audio = None

    def test_noop_when_text_matches_cache(self, monkeypatch):
        """tts_precache must return immediately if ``text`` matches the
        currently cached text — otherwise every track-change would fire
        a fresh piper subprocess."""
        import asyncio

        tts._cached_text = "Now playing: Test Song"
        tts._cached_audio = b"fake_cached_audio"

        # If tts_precache ran piper, create_subprocess_exec would be
        # called.  Patch it to blow up so we can assert it wasn't.
        async def _boom(*a, **k):
            raise AssertionError("piper should not run — cache hit")
        monkeypatch.setattr(
            "asyncio.create_subprocess_exec", _boom
        )

        asyncio.new_event_loop().run_until_complete(
            tts.tts_precache("Now playing: Test Song")
        )
        assert tts._cached_audio == b"fake_cached_audio"

    def test_noop_on_empty_text(self, monkeypatch):
        import asyncio

        async def _boom(*a, **k):
            raise AssertionError("piper should not run — empty text")
        monkeypatch.setattr("asyncio.create_subprocess_exec", _boom)

        asyncio.new_event_loop().run_until_complete(
            tts.tts_precache("")
        )
