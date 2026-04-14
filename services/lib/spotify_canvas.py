"""
Spotify Canvas video URL fetcher.

Self-contained module that fetches Canvas (looping video) URLs for Spotify tracks.
Uses the undocumented canvaz-cache protobuf API with sp_dc cookie authentication.

Usage:
    from lib.spotify_canvas import SpotifyCanvasClient

    client = SpotifyCanvasClient()  # reads SPOTIFY_SP_DC from env
    url = await client.get_canvas_url("spotify:track:7eGuPhpdS8sBjPJNuAShUX")
"""

import asyncio
import base64
import collections
import hashlib
import hmac
import json
import logging
import os
import re
import struct
import time
import urllib.parse
import urllib.request
from email.utils import parsedate_to_datetime


# ── URI normalization ────────────────────────────────────────────────

# Matches a Spotify track id inside any URI format we've seen: the
# canonical ``spotify:track:<id>``, the Sonos-wrapped
# ``x-sonos-spotify:spotify%3atrack%3a<id>?sid=…``, and slash variants
# like ``spotify/track/<id>`` from web/share URLs.
_SPOTIFY_TRACK_RE = re.compile(
    r"(?:spotify[:/]|open\.spotify\.com/)track[:/]([a-zA-Z0-9]{22})")


def extract_spotify_track_id(raw):
    """Return the bare spotify track id from any URI format, or None.

    Accepts:
      * ``spotify:track:<id>``
      * ``x-sonos-spotify:spotify%3atrack%3a<id>?sid=…`` (Sonos)
      * ``https://open.spotify.com/track/<id>?…``
      * a bare 22-char id

    Returns the 22-char id, or None if nothing matches. URL-encoded
    inputs are decoded first.
    """
    if not raw:
        return None
    decoded = urllib.parse.unquote(str(raw))
    m = _SPOTIFY_TRACK_RE.search(decoded)
    if m:
        return m.group(1)
    # Bare id: 22 chars, base62. Reject anything that contains a colon
    # or slash so we don't accidentally accept a malformed URI.
    if re.fullmatch(r"[a-zA-Z0-9]{22}", decoded):
        return decoded
    return None


def normalize_spotify_track_uri(raw):
    """Return ``spotify:track:<id>`` for any input, or None.

    Thin wrapper over ``extract_spotify_track_id`` for callers that
    need the full URI (e.g. ``SpotifyCanvasClient.get_canvas_url``,
    which validates the prefix)."""
    track_id = extract_spotify_track_id(raw)
    return f"spotify:track:{track_id}" if track_id else None

log = logging.getLogger("spotify-canvas")

# Protobuf import (compiled from canvas.proto, same directory)
import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from canvas_pb2 import EntityCanvazRequest, EntityCanvazResponse

# ── TOTP secrets management ─────────────────────────────────────────

SECRETS_URLS = [
    "https://github.com/xyloflake/spot-secrets-go/blob/main/secrets/secretDict.json?raw=true",
    "https://github.com/tomballgithub/spot-secrets-go/blob/main/secrets/secretDict.json?raw=true",
]
SECRETS_CACHE_TTL = 86400  # 24 hours


def _fetch_totp_secrets():
    """Download latest TOTP cipher secrets from GitHub."""
    all_secrets = {}
    for url in SECRETS_URLS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                all_secrets.update(data)
        except Exception as e:
            log.warning("Failed to fetch TOTP secrets from %s: %s",
                        url.split("/")[4], e)
    if all_secrets:
        log.info("Fetched TOTP secrets: versions %s", sorted(all_secrets.keys(),
                 key=lambda v: int(v)))
    return all_secrets


def _generate_totp_secret(cipher_bytes):
    """Convert Spotify cipher bytes to a TOTP base32 secret."""
    transformed = [e ^ ((t % 33) + 9) for t, e in enumerate(cipher_bytes)]
    joined = "".join(str(num) for num in transformed)
    hex_str = joined.encode().hex()
    return base64.b32encode(bytes.fromhex(hex_str)).decode().rstrip("=")


def _totp_code(secret_b32, timestamp):
    """Compute 6-digit TOTP code (RFC 6238, 30s interval)."""
    padding = 8 - (len(secret_b32) % 8)
    if padding != 8:
        secret_b32 += "=" * padding
    key = base64.b32decode(secret_b32, casefold=True)
    counter = struct.pack(">Q", int(timestamp) // 30)
    mac = hmac.new(key, counter, hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    code = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % 10**6).zfill(6)


def _get_server_time():
    """Get Spotify's server time from HTTP Date header."""
    try:
        req = urllib.request.Request("https://open.spotify.com", method="HEAD",
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            date_str = resp.headers.get("Date")
            if date_str:
                return int(parsedate_to_datetime(date_str).timestamp())
    except Exception:
        pass
    return int(time.time())


# ── Web player token exchange ────────────────────────────────────────

def _get_web_token(sp_dc, secrets):
    """Exchange sp_dc cookie for a Spotify web player access token."""
    server_time = _get_server_time()
    versions = sorted(secrets.keys(), key=lambda v: int(v), reverse=True)

    for ver in versions:
        cipher_bytes = secrets[ver]
        secret_b32 = _generate_totp_secret(cipher_bytes)
        code = _totp_code(secret_b32, server_time)

        for reason in ("transport", "init"):
            url = (
                f"https://open.spotify.com/api/token?"
                f"reason={reason}&productType=web-player"
                f"&totp={code}&totpVer={ver}"
            )
            req = urllib.request.Request(url, headers={
                "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/131.0.0.0 Safari/537.36"),
                "Cookie": f"sp_dc={sp_dc}",
                "App-Platform": "WebPlayer",
            })
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read())
                    if data.get("accessToken"):
                        log.info("Web token obtained (TOTP v%s, reason=%s)", ver, reason)
                        return data["accessToken"], data.get("accessTokenExpirationTimestampMs", 0)
                    if data.get("isAnonymous"):
                        log.error("sp_dc cookie invalid or expired — got anonymous token")
                        return None, 0
            except urllib.error.HTTPError as e:
                log.debug("Token request failed (v%s, %s): HTTP %d", ver, reason, e.code)
            except Exception as e:
                log.debug("Token request error (v%s, %s): %s", ver, reason, e)

    log.error("Failed to get web token — all TOTP versions exhausted")
    return None, 0


# ── Canvas URL fetcher ───────────────────────────────────────────────

CANVAS_HOSTS = [
    "gew1-spclient.spotify.com",
    "gae2-spclient.spotify.com",
    "guc3-spclient.spotify.com",
]


_AUTH_EXPIRED = "_auth_expired"  # sentinel for 401 responses


def _fetch_canvas(token, track_uri):
    """Fetch Canvas video URL via the protobuf canvaz-cache API.
    Returns URL string, None (no canvas), or _AUTH_EXPIRED on 401."""
    req = EntityCanvazRequest()
    entity = req.entities.add()
    entity.entity_uri = track_uri

    for host in CANVAS_HOSTS:
        url = f"https://{host}/canvaz-cache/v0/canvases"
        try:
            http_req = urllib.request.Request(url, data=req.SerializeToString(), headers={
                "Content-Type": "application/x-protobuf",
                "Authorization": f"Bearer {token}",
            })
            with urllib.request.urlopen(http_req, timeout=10) as resp:
                canvas_resp = EntityCanvazResponse()
                canvas_resp.ParseFromString(resp.read())
                for c in canvas_resp.canvases:
                    if c.url:
                        log.info("Canvas found for %s: type=%d", track_uri, c.type)
                        return c.url
                return None  # no canvas for this track
        except urllib.error.HTTPError as e:
            if e.code == 401:
                log.warning("Canvas API 401 — token may be expired")
                return _AUTH_EXPIRED
            log.debug("Canvas request failed on %s: HTTP %d", host, e.code)
        except Exception as e:
            log.debug("Canvas request error on %s: %s", host, e)

    return None


# ── Public async client ──────────────────────────────────────────────

class SpotifyCanvasClient:
    """Async client for fetching Spotify Canvas video URLs.

    Manages TOTP secrets, web player token lifecycle, and canvas URL caching.
    Reads SPOTIFY_SP_DC from environment on init.
    """

    def __init__(self, sp_dc=None):
        self._sp_dc = sp_dc or os.environ.get("SPOTIFY_SP_DC", "")
        self._secrets = {}
        self._secrets_fetched = 0
        self._web_token = None
        self._token_expiry = 0
        self._cache = collections.OrderedDict()  # track_uri → canvas_url (or "" for "no canvas")
        self._cache_max = 500
        self._lock = asyncio.Lock()

    @property
    def configured(self):
        return bool(self._sp_dc)

    def get_cached(self, track_uri):
        """Return cached canvas URL for a track, or None if not cached."""
        cached = self._cache.get(track_uri)
        return cached if cached else None

    def _ensure_secrets(self):
        """Fetch TOTP secrets if not cached or expired."""
        if self._secrets and (time.time() - self._secrets_fetched) < SECRETS_CACHE_TTL:
            return
        self._secrets = _fetch_totp_secrets()
        self._secrets_fetched = time.time()

    def _ensure_token(self):
        """Get or refresh the web player token."""
        if self._web_token and time.time() * 1000 < self._token_expiry - 60000:
            return
        self._ensure_secrets()
        if not self._secrets:
            log.error("No TOTP secrets available")
            return
        token, expiry_ms = _get_web_token(self._sp_dc, self._secrets)
        if token:
            self._web_token = token
            self._token_expiry = expiry_ms

    async def warmup(self):
        """Pre-warm TOTP secrets and web token so first canvas fetch is fast.

        Returns True if token was obtained, False otherwise."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._ensure_token)
        return self._web_token is not None

    async def get_canvas_url(self, track_uri):
        """Get Canvas video URL for a Spotify track URI. Returns URL or None.

        Thread-safe (uses asyncio lock). Results are cached per track."""
        if not self._sp_dc:
            return None
        if not track_uri or not track_uri.startswith("spotify:track:"):
            return None

        # Check cache
        if track_uri in self._cache:
            cached = self._cache[track_uri]
            return cached if cached else None

        async with self._lock:
            # Re-check after lock
            if track_uri in self._cache:
                cached = self._cache[track_uri]
                return cached if cached else None

            loop = asyncio.get_running_loop()

            # Ensure we have a valid token
            await loop.run_in_executor(None, self._ensure_token)
            if not self._web_token:
                return None

            # Fetch canvas (retry once on 401 with a fresh token)
            result = await loop.run_in_executor(None, _fetch_canvas,
                                                self._web_token, track_uri)
            if result == _AUTH_EXPIRED:
                self._web_token = None
                self._token_expiry = 0
                await loop.run_in_executor(None, self._ensure_token)
                if self._web_token:
                    result = await loop.run_in_executor(None, _fetch_canvas,
                                                        self._web_token, track_uri)
            url = result if result and result != _AUTH_EXPIRED else None

            # Cache result (empty string = no canvas, avoids re-fetching)
            self._cache[track_uri] = url or ""
            if len(self._cache) > self._cache_max:
                self._cache.popitem(last=False)

            if url:
                log.info("Canvas URL cached for %s", track_uri)
            else:
                log.debug("No canvas for %s", track_uri)

            return url
