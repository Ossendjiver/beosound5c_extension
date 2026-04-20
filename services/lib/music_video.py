# BeoSound 5c
# Copyright (C) 2024-2026 Markus Kirsten
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Attribution required — see LICENSE, Section 7(b).

"""
Music video lookup for BeoSound 5c.

Uses public Invidious instances for both search and stream resolution —
no API key or account required. Invidious exposes a YouTube-compatible
search API and returns direct muxed mp4 stream URLs.

Two-tier cache: video IDs are cached permanently (avoids repeated search
hits), stream URLs are cached for 2 hours (Googlevideo auth tokens expire).
"""

import asyncio
import collections
import logging
import subprocess
import time
import urllib.parse

import aiohttp

# yt-dlp binary locations to try in order
_YTDLP_BINS = ("/usr/local/bin/yt-dlp", "/usr/bin/yt-dlp", "yt-dlp")

log = logging.getLogger("music-video")

# Public Invidious instances tried in order for both search and stream resolution.
# If one is down or rate-limited, the next is tried.
INVIDIOUS_INSTANCES = [
    "https://invidious.io",
    "https://vid.puffyan.us",
    "https://inv.riverside.rocks",
    "https://invidious.nerdvpn.de",
    "https://y.com.sb",
]

# Minimum video duration — filters out shorts and clips (seconds)
MIN_DURATION_S = 90

# Stream URL cache TTL — Googlevideo tokens expire, so re-resolve periodically
STREAM_URL_TTL_S = 7200  # 2 hours

_ID_CACHE_MAX = 500      # artist+title → youtube video_id (permanent)
_STREAM_CACHE_MAX = 200  # video_id → (url, fetched_time)


class MusicVideoClient:
    """Looks up direct music video stream URLs for artist + title pairs.

    No API key required — uses public Invidious instances for search and
    stream resolution. Designed to be instantiated once and reused across
    track changes.
    """

    def __init__(self):
        # "artist||title" → youtube video_id or "" (meaning "no video found")
        self._id_cache: collections.OrderedDict[str, str] = collections.OrderedDict()
        # video_id → (stream_url, fetched_at)
        self._stream_cache: dict[str, tuple[str, float]] = {}
        self._lock = asyncio.Lock()
        
        # State tracking for Sonos debounce
        self._current_artist = None
        self._current_title = None

    @property
    def configured(self) -> bool:
        return True  # always available — no credentials needed

    def _id_key(self, artist: str, title: str) -> str:
        return f"{artist.lower().strip()}||{title.lower().strip()}"

    async def _on_track_change(self, artist: str, title: str, session: aiohttp.ClientSession) -> str | None:
        """Debounce track changes to handle Sonos state flickers before looking up."""
        # Immediately update the state to the new track
        self._current_artist = artist
        self._current_title = title

        await asyncio.sleep(2.0)  # wait for Sonos state to settle

        # then check if track is still the same before proceeding
        # If another track change occurred during the sleep, these will no longer match
        if self._current_artist != artist or self._current_title != title:
            log.debug("Sonos state flicker detected. Aborting video lookup for %s - %s", artist, title)
            return None  # it was a flicker, abort

        # proceed with lookup...
        return await self.lookup(artist, title, session)

    def get_cached(self, artist: str, title: str) -> str | None:
        """Return cached stream URL (may be ""), or None if not yet looked up.

        Returns:
            str  — valid stream URL (cache hit, video found)
            ""   — cache hit, no video found for this track
            None — not in cache, needs a lookup
        """
        key = self._id_key(artist, title)
        video_id = self._id_cache.get(key)
        if video_id is None:
            return None  # not looked up yet
        if not video_id:
            return ""    # looked up, no video found
        cached = self._stream_cache.get(video_id)
        if cached:
            url, fetched_at = cached
            if time.time() - fetched_at < STREAM_URL_TTL_S:
                return url
        return None  # id known but stream URL expired — needs re-resolve

    async def lookup(self, artist: str, title: str,
                     session: aiohttp.ClientSession) -> str | None:
        """Return a direct video stream URL, or None if not found."""
        if not artist or not title:
            return None

        # Fast path — no lock needed for cache read
        cached = self.get_cached(artist, title)
        if cached is not None:
            return cached or None

        async with self._lock:
            # Re-check after acquiring lock
            cached = self.get_cached(artist, title)
            if cached is not None:
                return cached or None

            return await self._fetch(artist, title, session)

    async def _fetch(self, artist: str, title: str,
                     session: aiohttp.ClientSession) -> str | None:
        key = self._id_key(artist, title)

        # Step 1: search for a video ID (may already be cached if stream expired)
        video_id = self._id_cache.get(key)
        if not video_id:
            video_id = await self._search(artist, title, session)
            if video_id is None:
                # Network failure — don't cache, so the next track play retries
                return None
            # video_id == "" means "searched, no video found" — cache it to skip
            # future lookups; non-empty means found — cache the ID
            if len(self._id_cache) >= _ID_CACHE_MAX:
                self._id_cache.popitem(last=False)
            self._id_cache[key] = video_id  # "" or actual ID
            if not video_id:
                return None

        # Step 2: resolve direct stream URL
        url = await self._resolve_stream(video_id, session)
        if url:
            if len(self._stream_cache) >= _STREAM_CACHE_MAX:
                oldest = next(iter(self._stream_cache))
                del self._stream_cache[oldest]
            self._stream_cache[video_id] = (url, time.time())
            log.info("Music video found for %s – %s: youtube/%s", artist, title, video_id)
        else:
            log.info("No stream URL for youtube/%s (%s – %s)", video_id, artist, title)
        return url

    async def _search(self, artist: str, title: str,
                      session: aiohttp.ClientSession) -> str | None:
        """Search via Invidious API, return first suitable video_id or None."""
        q = f"{artist} {title} official music video"
        params = {"q": q, "type": "video", "fields": "videoId,title,lengthSeconds"}

        for instance in INVIDIOUS_INSTANCES:
            url = f"{instance}/api/v1/search?{urllib.parse.urlencode(params)}"
            try:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=8.0),
                    headers={"User-Agent": "BeoSound5c/1.0"},
                ) as resp:
                    if resp.status != 200:
                        log.debug("Invidious search %s → HTTP %d", instance, resp.status)
                        continue
                    results = await resp.json()
            except Exception as e:
                log.debug("Invidious search %s failed: %s", instance, e)
                continue

            for item in results:
                duration = item.get("lengthSeconds", 0)
                if duration < MIN_DURATION_S:
                    continue  # skip shorts and clips
                video_id = item.get("videoId")
                if video_id:
                    log.info("Music video candidate for %s – %s: youtube/%s (%.0fs)",
                             artist, title, video_id, duration)
                    return video_id

            # Got a response but no suitable result — cache this as "no video"
            log.info("No suitable music video for %s – %s (all results too short or missing)",
                     artist, title)
            return ""  # don't try other instances for search; result set is the same

        log.info("All Invidious instances unreachable for search (%s – %s)", artist, title)
        return None  # network failure — do NOT cache; retry on next track play

    async def _resolve_stream(self, video_id: str,
                               session: aiohttp.ClientSession) -> str | None:
        """Extract a direct stream URL via yt-dlp.

        Runs in a thread executor (blocking subprocess). yt-dlp uses the
        Android VR client which bypasses YouTube's PO token requirement.
        Falls back through known binary locations.
        """
        loop = asyncio.get_running_loop()
        for ytdlp in _YTDLP_BINS:
            try:
                result = await loop.run_in_executor(
                    None,
                    lambda b=ytdlp: subprocess.run(
                        [b, "--get-url",
                         "-f", "best[ext=mp4][height<=720]/best[height<=720]",
                         "--no-playlist", "--quiet", "--no-warnings",
                         "--", video_id],
                        capture_output=True, text=True, timeout=30,
                    ),
                )
                if result.returncode == 0:
                    url = result.stdout.strip().split("\n")[0]
                    if url:
                        log.info("yt-dlp stream resolved for youtube/%s", video_id)
                        return url
                log.debug("yt-dlp %s exit %d: %s", ytdlp, result.returncode,
                          result.stderr.strip()[:120])
            except FileNotFoundError:
                continue  # try next path
            except Exception as e:
                log.debug("yt-dlp %s failed: %s", ytdlp, e)
                continue

        log.info("yt-dlp not available or failed for youtube/%s", video_id)
        return None
