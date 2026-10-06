"""Tests for fetch.py's shrink guard (should_refuse_shrink).

The guard exists to protect a healthy cache from an errored run (auth,
scope, rate-limit) that comes back with far fewer playlists.  It must
NOT trigger on a *clean* run: a user who pruned 34 playlists down to 5
in Spotify was permanently wedged — every automatic refresh path
(view-open, startup, nightly, ``refresh_playlists`` command) runs
without ``--force``, so the smaller-but-correct result was refused
forever while fetch.py reported success (rc=0).

Rule pinned here: refuse a large shrink only when the run had problems
(``list_error`` from GET /me/playlists, or failed track fetches).
"""

from sources.spotify.fetch import should_refuse_shrink


class TestShrinkGuard:
    def test_clean_fetch_with_large_shrink_writes(self):
        """34 -> 5 with a clean list fetch and zero failed track fetches
        is a legitimate deletion — must be written, not refused."""
        assert should_refuse_shrink(
            force=False, cached_count=34, final_count=5,
            list_error=None, fetched_failed=0) is False

    def test_list_error_with_large_shrink_refuses(self):
        assert should_refuse_shrink(
            force=False, cached_count=34, final_count=5,
            list_error="http_401", fetched_failed=0) is True

    def test_failed_track_fetches_with_large_shrink_refuses(self):
        assert should_refuse_shrink(
            force=False, cached_count=34, final_count=5,
            list_error=None, fetched_failed=3) is True

    def test_force_always_writes(self):
        assert should_refuse_shrink(
            force=True, cached_count=34, final_count=5,
            list_error="http_401", fetched_failed=3) is False

    def test_small_shrink_writes_even_with_errors(self):
        """Result >= half the cache is not a 'drastic' shrink — the
        per-playlist cache fallback already handled the failures."""
        assert should_refuse_shrink(
            force=False, cached_count=34, final_count=20,
            list_error=None, fetched_failed=2) is False

    def test_tiny_cache_never_refuses(self):
        """Caches under 4 playlists are below the guard threshold."""
        assert should_refuse_shrink(
            force=False, cached_count=3, final_count=1,
            list_error="http_401", fetched_failed=1) is False
