#!/usr/bin/env python3
"""
Kodi / LibreELEC source service for BeoSound 5c.

Connects to Kodi's JSON-RPC HTTP API, caches the full library hierarchy
to disk, and exposes it via HTTP for the kodi.html frontend.

Library structure served at /playlists:
  [
    { id: "movies",  name: "Movies",   tracks: [ <movie-leaf>,      ... ] },
    { id: "tvshows", name: "TV Shows", tracks: [ <show-folder>,     ... ] },
    { id: "livetv",  name: "Live TV",  tracks: [ <group-folder>,    ... ] },
  ]

Node contracts (same as MASS service):
  FOLDER node â†’ has `tracks`, has `play_url` (kodi:// URI), NO `url`.
  LEAF   node â†’ has `url` (kodi:// URI), NO `tracks`.

kodi:// URI format: kodi://<type>/<id>
  Types: movie, episode, tvshow, season, channel
"""

import asyncio
import datetime
import hashlib
import html
import json
import logging
import os
import re
import sys
import time
import urllib.parse
from aiohttp import web, BasicAuth, ClientSession, ClientTimeout

# â”€â”€ Path bootstrap â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
current_dir = os.path.dirname(os.path.abspath(__file__))
while current_dir != '/' and 'lib' not in os.listdir(current_dir):
    current_dir = os.path.dirname(current_dir)
if 'lib' in os.listdir(current_dir):
    sys.path.insert(0, current_dir)

from lib.config import cfg
from lib.playback_targets import get_video_targets
from lib.source_base import SourceBase

# â”€â”€ CONFIG â€” edit these for your Kodi installation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
KODI_HOST = (os.getenv("KODI_HOST") or cfg("kodi", "host", default="localhost")).strip()
KODI_PORT = int(os.getenv("KODI_PORT") or cfg("kodi", "port", default=8080))
KODI_USER = (os.getenv("KODI_USER") or cfg("kodi", "user", default="")).strip()
KODI_PASS = (
    os.getenv("KODI_PASSWORD") or os.getenv("KODI_PASS") or cfg("kodi", "password", default="")
).strip()
CACHE_FILE    = "/media/local/cache/kodi_library.json"
LEGACY_CACHE_FILE = "/home/thomas/beosound5c/web/json/kodi_library.json"
ART_CACHE_DIR = "/media/local/cache/kodi_art"
ART_ROUTE_PREFIX = "/art"
ART_FETCH_CONCURRENCY = 6

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("beo-source-kodi")

# Kodi playlist IDs
PLAYLIST_AUDIO = 0
PLAYLIST_VIDEO = 1


class KodiSource(SourceBase):
    id   = "kodi"
    name = "Kodi"
    port = 8782
    manages_queue = True
    action_map = {
        "play": "transport_toggle",
        "pause": "transport_toggle",
        "go": "transport_toggle",
        "next": "transport_next",
        "prev": "transport_previous",
        "right": "transport_next",
        "left": "transport_previous",
        "stop": "transport_stop",
    }

    def __init__(self):
        super().__init__()
        self._library_data = []
        self._is_syncing   = False
        self._cache_requires_resync = False
        self._session: ClientSession | None = None
        self._watched_status_cache = {"ts": 0.0, "value": {"movies": None, "tvshows": None, "episodes": None}}
        self._preferred_player_id = ""
        self.has_cache     = self._load_local_cache()

    # â”€â”€ Cache â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _load_local_cache(self):
        try:
            cache_path = CACHE_FILE if os.path.exists(CACHE_FILE) else LEGACY_CACHE_FILE
            if os.path.exists(cache_path):
                with open(cache_path, 'r') as f:
                    self._library_data = json.load(f)
                self._cache_requires_resync = not self._normalize_library_tree(self._library_data)
                self._save_cache(self._library_data)
                if self._cache_requires_resync:
                    logger.info("Kodi cache missing playlist root; scheduling an immediate refresh.")
                logger.info("Kodi local library cache loaded from %s.", cache_path)
                return True
            logger.info("No Kodi cache â€” initial sync needed.")
            return False
        except Exception as e:
            logger.error(f"Failed to load Kodi cache: {e}")
            return False

    def _save_cache(self, data):
        try:
            payload = json.dumps(data, separators=(',', ':'))
            for path in (CACHE_FILE, LEGACY_CACHE_FILE):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                tmp_path = path + ".tmp"
                with open(tmp_path, 'w') as handle:
                    handle.write(payload)
                os.replace(tmp_path, path)
            logger.info(f"Kodi library saved to {CACHE_FILE}")
        except Exception as e:
            logger.error(f"Kodi save failed: {e}")

    # â”€â”€ Lifecycle â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    async def on_start(self):
        logger.info("Kodi Source Starting...")
        if not os.getenv("KODI_HOST"):
            logger.warning("KODI_HOST not set; defaulting to localhost.")
        auth = BasicAuth(KODI_USER, KODI_PASS) if KODI_USER else None
        self._session = ClientSession(
            auth=auth,
            timeout=ClientTimeout(total=30),
        )
        await self.register("available")
        self._spawn(self._schedule_sync_loop(), name="kodi_sync_loop")

    async def on_stop(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _schedule_sync_loop(self):
        if not self.has_cache or self._cache_requires_resync:
            # Wait for Kodi to be reachable
            for _ in range(30):
                if await self._ping():
                    break
                await asyncio.sleep(5)
            reason = "initial" if not self.has_cache else "refresh"
            logger.info("Running %s Kodi library sync.", reason)
            await self.update_library_cache()

        while True:
            now       = datetime.datetime.now()
            next_sync = now.replace(hour=3, minute=0, second=0, microsecond=0)
            if now >= next_sync:
                next_sync += datetime.timedelta(days=1)
            wait_s = (next_sync - now).total_seconds()
            logger.info(f"Next Kodi sync in {wait_s / 3600:.1f}h.")
            await asyncio.sleep(wait_s)
            logger.info("Triggering scheduled Kodi library sync...")
            await self.update_library_cache()

    # â”€â”€ Kodi JSON-RPC â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    @property
    def _rpc_url(self):
        return f"http://{KODI_HOST}:{KODI_PORT}/jsonrpc"

    async def _rpc(self, method, params=None):
        """Call a Kodi JSON-RPC method. Returns the `result` field or None."""
        payload = {
            "jsonrpc": "2.0",
            "method":  method,
            "params":  params or {},
            "id":      1,
        }
        try:
            async with self._session.post(self._rpc_url, json=payload) as resp:
                if resp.status != 200:
                    logger.warning(f"Kodi RPC {method} HTTP {resp.status}")
                    return None
                data = await resp.json(content_type=None)
                if "error" in data:
                    logger.warning(f"Kodi RPC error [{method}]: {data['error']}")
                    return None
                return data.get("result")
        except Exception as e:
            logger.warning(f"Kodi RPC failed [{method}]: {e}")
            return None

    async def _ping(self):
        result = await self._rpc("JSONRPC.Ping")
        return result == "pong"

    async def _rpc_paginated(self, method, result_key, params=None, page_size=500):
        """Fetch all items from a Kodi list method using limit/offset pagination."""
        params    = dict(params or {})
        all_items = []
        start     = 0
        while True:
            params["limits"] = {"start": start, "end": start + page_size}
            result = await self._rpc(method, params)
            if not result:
                break
            batch = result.get(result_key, [])
            if not batch:
                break
            all_items.extend(batch)
            total = result.get("limits", {}).get("total", 0)
            start += len(batch)
            if start >= total or len(batch) < page_size:
                break
        return all_items

    # â”€â”€ Image helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _img(self, art, *keys):
        """
        Extract and proxy an image from a Kodi `art` dict.
        Tries keys in order; returns Kodi image proxy URL or empty string.
        """
        for key in keys:
            path = art.get(key, "") if isinstance(art, dict) else ""
            if path:
                path = str(path).strip()
                if path.startswith("http://") or path.startswith("https://"):
                    return path
                encoded = urllib.parse.quote(path, safe='')
                return f"http://{KODI_HOST}:{KODI_PORT}/image/{encoded}"
        return ""

    @staticmethod
    def _art_cache_basename(image_url):
        return hashlib.sha1(image_url.encode("utf-8")).hexdigest()[:20]

    def _art_cache_name(self, image_url, content_type=""):
        parsed = urllib.parse.urlparse(image_url)
        ext = os.path.splitext(parsed.path)[1].lower()
        if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
            if "png" in content_type:
                ext = ".png"
            elif "webp" in content_type:
                ext = ".webp"
            elif "gif" in content_type:
                ext = ".gif"
            else:
                ext = ".jpg"
        return f"{self._art_cache_basename(image_url)}{ext}"

    def _find_cached_art_name(self, image_url):
        basename = self._art_cache_basename(image_url)
        for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            candidate = f"{basename}{ext}"
            if os.path.exists(os.path.join(ART_CACHE_DIR, candidate)):
                return candidate
        return ""

    async def _cache_image_locally(self, image_url):
        if not image_url or not image_url.startswith("http") or not self._session:
            return image_url

        cached_name = self._find_cached_art_name(image_url)
        if cached_name:
            return f"{ART_ROUTE_PREFIX}/{cached_name}"

        try:
            os.makedirs(ART_CACHE_DIR, exist_ok=True)
            async with self._session.get(image_url) as response:
                if response.status != 200:
                    return image_url
                payload = await response.read()
                content_type = response.headers.get("Content-Type", "")

            cache_name = self._art_cache_name(image_url, content_type)
            cache_path = os.path.join(ART_CACHE_DIR, cache_name)
            if not os.path.exists(cache_path):
                tmp_path = cache_path + ".tmp"
                with open(tmp_path, "wb") as handle:
                    handle.write(payload)
                os.replace(tmp_path, cache_path)
            return f"{ART_ROUTE_PREFIX}/{cache_name}"
        except Exception as exc:
            logger.debug("Failed to cache Kodi artwork %s: %s", image_url, exc)
            return image_url

    async def _localize_tree_images(self, data):
        url_nodes = {}

        def collect(node):
            image_url = node.get("image", "")
            if isinstance(image_url, str) and image_url.startswith("http"):
                url_nodes.setdefault(image_url, []).append(node)
            for child in node.get("tracks") or []:
                collect(child)

        for root in data:
            collect(root)

        if not url_nodes:
            return

        semaphore = asyncio.Semaphore(ART_FETCH_CONCURRENCY)
        replacements = {}

        async def fetch_one(image_url):
            async with semaphore:
                replacements[image_url] = await self._cache_image_locally(image_url)

        await asyncio.gather(*(fetch_one(image_url) for image_url in url_nodes))

        for image_url, nodes in url_nodes.items():
            localized = replacements.get(image_url, image_url)
            for node in nodes:
                node["image"] = localized

    def _placeholder_art(self, title, subtitle=""):
        safe_title = html.escape((title or "Unknown")[:22])
        safe_subtitle = html.escape((subtitle or "")[:26])
        svg = f"""
        <svg xmlns="http://www.w3.org/2000/svg" width="320" height="320" viewBox="0 0 320 320">
          <defs>
            <linearGradient id="g" x1="0" x2="1" y1="0" y2="1">
              <stop offset="0%" stop-color="#21304a"/>
              <stop offset="100%" stop-color="#111111"/>
            </linearGradient>
          </defs>
          <rect width="320" height="320" rx="24" fill="url(#g)"/>
          <circle cx="78" cy="78" r="34" fill="rgba(255,255,255,0.12)"/>
          <text x="160" y="164" fill="#f4f7fb" font-family="Arial,sans-serif" font-size="28" text-anchor="middle">{safe_title}</text>
          <text x="160" y="202" fill="#9fb0c8" font-family="Arial,sans-serif" font-size="16" text-anchor="middle">{safe_subtitle}</text>
        </svg>
        """.strip()
        return "data:image/svg+xml;utf8," + urllib.parse.quote(svg)

    def _category_art(self, kind, label):
        accent_map = {
            "movies": "#4f8cff",
            "tvshows": "#2dd4bf",
            "livetv": "#fb923c",
        }
        icon_map = {
            "movies": """
                <rect x="84" y="78" width="152" height="164" rx="24" fill="rgba(255,255,255,0.05)" stroke="#eef2ff" stroke-width="12"/>
                <rect x="120" y="114" width="80" height="92" rx="12" fill="{accent}" opacity="0.24"/>
                <rect x="98" y="100" width="16" height="16" rx="4" fill="#eef2ff"/>
                <rect x="98" y="136" width="16" height="16" rx="4" fill="#eef2ff"/>
                <rect x="98" y="172" width="16" height="16" rx="4" fill="#eef2ff"/>
                <rect x="98" y="208" width="16" height="16" rx="4" fill="#eef2ff"/>
                <rect x="206" y="100" width="16" height="16" rx="4" fill="#eef2ff"/>
                <rect x="206" y="136" width="16" height="16" rx="4" fill="#eef2ff"/>
                <rect x="206" y="172" width="16" height="16" rx="4" fill="#eef2ff"/>
                <rect x="206" y="208" width="16" height="16" rx="4" fill="#eef2ff"/>
            """,
            "tvshows": """
                <path d="M124 72L160 100L196 72" fill="none" stroke="#eef2ff" stroke-linecap="round" stroke-linejoin="round" stroke-width="10"/>
                <rect x="74" y="92" width="172" height="118" rx="20" fill="rgba(255,255,255,0.05)" stroke="#eef2ff" stroke-width="12"/>
                <rect x="104" y="120" width="112" height="62" rx="12" fill="{accent}" opacity="0.24"/>
                <line x1="160" y1="210" x2="160" y2="242" stroke="#eef2ff" stroke-linecap="round" stroke-width="10"/>
                <line x1="118" y1="246" x2="202" y2="246" stroke="#eef2ff" stroke-linecap="round" stroke-width="10"/>
            """,
            "livetv": """
                <circle cx="160" cy="160" r="18" fill="{accent}" opacity="0.24" stroke="#eef2ff" stroke-width="10"/>
                <path d="M124 126C140 110 180 110 196 126" fill="none" stroke="#eef2ff" stroke-linecap="round" stroke-width="10"/>
                <path d="M100 100C126 74 194 74 220 100" fill="none" stroke="#eef2ff" stroke-linecap="round" stroke-width="10"/>
                <path d="M124 194C140 210 180 210 196 194" fill="none" stroke="#eef2ff" stroke-linecap="round" stroke-width="10"/>
                <path d="M100 220C126 246 194 246 220 220" fill="none" stroke="#eef2ff" stroke-linecap="round" stroke-width="10"/>
            """,
        }
        accent = accent_map.get(kind, "#4f8cff")
        icon_svg = icon_map.get(kind, icon_map["movies"]).format(accent=accent)
        safe_label = html.escape(label or "")
        svg = f"""
        <svg xmlns="http://www.w3.org/2000/svg" width="320" height="320" viewBox="0 0 320 320">
          <defs>
            <linearGradient id="g" x1="0" x2="1" y1="0" y2="1">
              <stop offset="0%" stop-color="#141922"/>
              <stop offset="100%" stop-color="#090b0f"/>
            </linearGradient>
          </defs>
          <rect width="320" height="320" rx="28" fill="url(#g)"/>
          <rect x="20" y="20" width="280" height="280" rx="24" fill="none" stroke="{accent}" stroke-opacity="0.28" stroke-width="2"/>
          {icon_svg}
          <text x="160" y="284" fill="#b7c4d8" font-family="Arial,sans-serif" font-size="20" letter-spacing="3" text-anchor="middle">{safe_label}</text>
        </svg>
        """.strip()
        return "data:image/svg+xml;utf8," + urllib.parse.quote(svg)

    @staticmethod
    def _join_values(value):
        if isinstance(value, (list, tuple)):
            return ", ".join(str(item).strip() for item in value if str(item).strip())
        if value is None:
            return ""
        return str(value).strip()

    @staticmethod
    def _format_runtime(runtime):
        try:
            runtime_value = int(runtime or 0)
        except (TypeError, ValueError):
            return ""
        if runtime_value <= 0:
            return ""
        total_minutes = runtime_value // 60 if runtime_value > 600 else runtime_value
        hours, minutes = divmod(total_minutes, 60)
        if hours and minutes:
            return f"{hours}h {minutes}m"
        if hours:
            return f"{hours}h"
        return f"{minutes}m"

    def _cast_text(self, cast_list, limit=8):
        if isinstance(cast_list, str):
            return cast_list.strip()
        entries = []
        for member in cast_list or []:
            if not isinstance(member, dict):
                continue
            name = (member.get("name") or "").strip()
            role = (member.get("role") or "").strip()
            if not name:
                continue
            entries.append(f"{name} ({role})" if role else name)
            if len(entries) >= limit:
                break
        return ", ".join(entries)

    async def _finalize_detail_payload(self, *, kind, item_id, title, image, subtitle, meta_pairs,
                                       plot, cast_list=None, tagline="", play_uri=""):
        if image:
            image = await self._cache_image_locally(image)
        if not image:
            image = self._placeholder_art(title, subtitle)

        body_parts = [
            f'<img src="{html.escape(image, quote=True)}" alt="{html.escape(title, quote=True)}">'
        ]

        safe_tagline = (tagline or "").strip()
        if safe_tagline:
            body_parts.append(f"<blockquote>{html.escape(safe_tagline)}</blockquote>")

        for label, value in meta_pairs:
            if value:
                body_parts.append(
                    f"<p><strong>{html.escape(label)}:</strong> {html.escape(str(value))}</p>"
                )

        safe_plot = (plot or "No synopsis available.").strip()
        paragraphs = [part.strip() for part in safe_plot.replace("\r", "").split("\n\n") if part.strip()]
        if not paragraphs:
            paragraphs = [safe_plot]
        for paragraph in paragraphs:
            safe_paragraph = html.escape(paragraph).replace("\n", "<br>")
            body_parts.append(f"<p>{safe_paragraph}</p>")

        cast_text = self._cast_text(cast_list)
        if cast_text:
            body_parts.append(f"<p><strong>Cast:</strong> {html.escape(cast_text)}</p>")

        return {
            "kind": kind,
            "id": item_id,
            "title": title,
            "image": image,
            "body": "".join(body_parts),
            "play_uri": play_uri,
        }

    async def _movie_page_payload(self, movie_id, details):
        title = details.get("title") or "Unknown Movie"
        image = self._img(
            details.get("art", {}),
            "fanart",
            "poster",
            "landscape",
            "thumb",
            "banner",
            "clearlogo",
            "clearart",
            "keyart",
        )
        rating = details.get("rating")
        rating_text = f"{float(rating):.1f}/10" if isinstance(rating, (int, float)) and rating else ""
        meta_pairs = [
            ("Year", str(details.get("year", "")) if details.get("year") else ""),
            ("Premiered", details.get("premiered", "")),
            ("Runtime", self._format_runtime(details.get("runtime"))),
            ("Genre", self._join_values(details.get("genre"))),
            ("Rating", rating_text),
            ("Certificate", details.get("mpaa", "")),
            ("Director", self._join_values(details.get("director"))),
            ("Writer", self._join_values(details.get("writer"))),
            ("Studio", self._join_values(details.get("studio"))),
            ("Country", self._join_values(details.get("country"))),
        ]
        return await self._finalize_detail_payload(
            kind="movie",
            item_id=movie_id,
            title=title,
            image=image,
            subtitle=str(details.get("year", "")) if details.get("year") else "Movie",
            meta_pairs=meta_pairs,
            plot=details.get("plot") or details.get("plotoutline") or "No synopsis available.",
            cast_list=details.get("cast"),
            tagline=details.get("tagline", ""),
            play_uri=self._kodi_uri("movie", movie_id),
        )

    async def _episode_page_payload(self, episode_id, details):
        title = details.get("title") or "Unknown Episode"
        show_title = details.get("showtitle") or details.get("tvshowtitle") or "TV Show"
        season_num = details.get("season")
        episode_num = details.get("episode")
        episode_label = ""
        if season_num is not None and episode_num is not None:
            episode_label = f"S{int(season_num):02d}E{int(episode_num):02d}"
        image = self._img(
            details.get("art", {}),
            "thumb",
            "landscape",
            "fanart",
            "tvshow.poster",
            "season.poster",
            "poster",
            "banner",
        )
        if not image and isinstance(details.get("show_art"), dict):
            image = self._img(
                details.get("show_art", {}),
                "poster",
                "tvshow.poster",
                "landscape",
                "fanart",
                "banner",
                "thumb",
            )
        rating = details.get("rating")
        rating_text = f"{float(rating):.1f}/10" if isinstance(rating, (int, float)) and rating else ""
        meta_pairs = [
            ("Series", show_title),
            ("Episode", episode_label),
            ("Aired", details.get("firstaired", "")),
            ("Runtime", self._format_runtime(details.get("runtime"))),
            ("Genre", self._join_values(details.get("genre"))),
            ("Rating", rating_text),
            ("Director", self._join_values(details.get("director"))),
            ("Writer", self._join_values(details.get("writer"))),
            ("Studio", self._join_values(details.get("studio"))),
        ]
        return await self._finalize_detail_payload(
            kind="episode",
            item_id=episode_id,
            title=title,
            image=image,
            subtitle=show_title,
            meta_pairs=meta_pairs,
            plot=details.get("plot") or "No synopsis available.",
            cast_list=details.get("cast"),
            tagline="",
            play_uri=self._kodi_uri("episode", episode_id),
        )

    async def _channel_page_payload(self, channel_id, details, broadcast_details=None):
        title = details.get("label") or details.get("channel") or "Live TV"
        current_broadcast = details.get("broadcastnow", {}) if isinstance(details, dict) else {}
        next_broadcast = details.get("broadcastnext", {}) if isinstance(details, dict) else {}
        broadcast_details = broadcast_details or {}
        broadcast_title = (
            broadcast_details.get("title")
            or current_broadcast.get("label")
            or current_broadcast.get("title")
            or ""
        )
        image = self._img(
            {
                "thumb": details.get("thumbnail", ""),
                "icon": details.get("icon", ""),
                "banner": broadcast_details.get("thumbnail", ""),
            },
            "thumb",
            "icon",
            "banner",
        )
        meta_pairs = [
            ("Channel", title),
            ("Number", details.get("channelnumber", "")),
            ("Now", broadcast_title),
            ("Next", next_broadcast.get("label") or next_broadcast.get("title") or ""),
            ("Genre", self._join_values(broadcast_details.get("genre"))),
            ("Started", broadcast_details.get("starttime") or current_broadcast.get("starttime") or ""),
            ("Ends", broadcast_details.get("endtime") or current_broadcast.get("endtime") or ""),
        ]
        return await self._finalize_detail_payload(
            kind="channel",
            item_id=channel_id,
            title=title,
            image=image,
            subtitle=broadcast_title or "Live TV",
            meta_pairs=meta_pairs,
            plot=broadcast_details.get("plot") or broadcast_details.get("plotoutline") or "No programme synopsis available.",
            cast_list=broadcast_details.get("cast"),
            tagline="",
            play_uri=self._kodi_uri("channel", channel_id),
        )

    # â”€â”€ Node constructors â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _folder(self, id_, name, play_url="", artist="", image=""):
        """Folder node: `play_url` for playback, `tracks` populated by caller."""
        node = {"id": id_, "name": name, "tracks": []}
        if play_url: node["play_url"] = play_url
        if artist:   node["artist"]   = artist
        if image:    node["image"]    = image
        return node

    def _leaf(self, id_, name, url, artist="", image=""):
        """Leaf node: `url` for playback, no `tracks`."""
        node = {"id": id_, "name": name, "url": url}
        if artist: node["artist"] = artist
        if image:  node["image"]  = image
        return node

    @staticmethod
    def _kodi_uri(item_type, item_id):
        """Build a kodi:// URI for use as playback handle."""
        return f"kodi://{item_type}/{item_id}"

    @staticmethod
    def _sort_name_key(value):
        text = " ".join(str(value or "").split()).strip()
        if not text:
            return ("", "")
        folded = re.sub(r"^[^0-9a-z]+", "", text.casefold())
        folded = re.sub(r"^(the|an|a)\s+", "", folded)
        return (folded or text.casefold(), text.casefold())

    def _sorted_nodes(self, nodes):
        items = list(nodes or [])
        return sorted(
            items,
            key=lambda node: self._sort_name_key(node.get("name") if isinstance(node, dict) else ""),
        )

    def _sort_playlist_children(self, node):
        children = node.get("tracks")
        if not isinstance(children, list):
            return
        for child in children:
            if isinstance(child, dict) and isinstance(child.get("tracks"), list):
                self._sort_playlist_children(child)
        node["tracks"] = self._sorted_nodes(children)

    def _normalize_library_tree(self, tree):
        if not isinstance(tree, list):
            return False

        roots = {
            str(node.get("id") or ""): node
            for node in tree
            if isinstance(node, dict)
        }

        movies_root = roots.get("movies")
        if isinstance(movies_root, dict):
            movies_root["tracks"] = self._sorted_nodes(movies_root.get("tracks"))

        shows_root = roots.get("tvshows")
        if isinstance(shows_root, dict):
            shows_root["tracks"] = self._sorted_nodes(shows_root.get("tracks"))

        live_root = roots.get("livetv")
        if isinstance(live_root, dict):
            for group in live_root.get("tracks") or []:
                if isinstance(group, dict):
                    group["tracks"] = self._sorted_nodes(group.get("tracks"))
            live_root["tracks"] = self._sorted_nodes(live_root.get("tracks"))

        playlists_root = roots.get("playlists")
        if not isinstance(playlists_root, dict):
            tree.append(self._folder("playlists", "Playlists"))
            return False

        self._sort_playlist_children(playlists_root)
        return True

    @staticmethod
    def _playlist_display_name(file_path, label, is_directory=False):
        name = str(label or "").strip()
        if not name:
            name = os.path.basename(str(file_path or "").rstrip("/\\"))
        if not is_directory:
            name = os.path.splitext(name)[0]
        return name or ("Playlists" if is_directory else "Playlist")

    async def _load_playlist_nodes(self, directory, media_label):
        result = await self._rpc(
            "Files.GetDirectory",
            {
                "directory": directory,
                "media": "files",
                "sort": {"method": "label", "order": "ascending"},
                "properties": ["thumbnail", "mimetype"],
            },
        ) or {}
        entries = result.get("files", []) if isinstance(result, dict) else []
        nodes = []

        for entry in entries:
            if not isinstance(entry, dict):
                continue

            file_path = str(entry.get("file") or "").strip()
            if not file_path:
                continue

            file_type = str(entry.get("filetype") or "").strip().lower()
            is_directory = file_type == "directory"
            name = self._playlist_display_name(file_path, entry.get("label"), is_directory=is_directory)

            if is_directory:
                child_nodes = await self._load_playlist_nodes(file_path, media_label)
                if not child_nodes:
                    continue
                folder_node = self._folder(
                    id_=f"playlist_dir_{hashlib.sha1(file_path.encode('utf-8')).hexdigest()[:12]}",
                    name=name,
                )
                folder_node["tracks"] = child_nodes
                nodes.append(folder_node)
                continue

            image = self._placeholder_art(name, media_label)
            nodes.append(
                self._leaf(
                    id_=f"playlist_{hashlib.sha1(file_path.encode('utf-8')).hexdigest()[:12]}",
                    name=name,
                    url=self._kodi_uri("playlist", urllib.parse.quote(file_path, safe="")),
                    artist=media_label,
                    image=image,
                )
            )

        return self._sorted_nodes(nodes)

    # â”€â”€ Library sync â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    async def update_library_cache(self):
        if self._is_syncing:
            return
        self._is_syncing = True
        logger.info("--- Starting Kodi Library Sync ---")

        movies_root = self._folder("movies", "Movies")
        shows_root = self._folder("tvshows", "TV Shows")
        live_root = self._folder("livetv", "Live TV")
        playlists_root = self._folder("playlists", "Playlists")
        tree = [movies_root, shows_root, live_root, playlists_root]

        try:
            # â”€â”€ 1. MOVIES (flat) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            logger.info("Syncing movies...")
            movies = await self._rpc_paginated(
                "VideoLibrary.GetMovies",
                "movies",
                params={"properties": ["title", "file", "art", "year", "genre"]},
            )
            for m in movies:
                year  = str(m.get("year", "")) if m.get("year") else ""
                image = self._img(
                    m.get("art", {}),
                    "poster",
                    "thumb",
                    "landscape",
                    "fanart",
                    "banner",
                    "clearlogo",
                    "clearart",
                    "keyart",
                )
                movies_root["tracks"].append(
                    self._leaf(
                        id_=f"movie_{m['movieid']}",
                        name=m.get("title", "Unknown Movie"),
                        url=self._kodi_uri("movie", m["movieid"]),
                        artist=year,
                        image=image,
                    )
                )
            logger.info(f"  Movies: {len(movies_root['tracks'])}")

            # TV shows: Show -> Season -> Episode (or direct episodes)
            logger.info("Syncing TV shows...")
            shows = await self._rpc_paginated(
                "VideoLibrary.GetTVShows",
                "tvshows",
                params={"properties": ["title", "art", "year", "genre"]},
            )
            for show in shows:
                show_title = show.get("title", "Unknown Show")
                image = self._img(
                    show.get("art", {}),
                    "poster",
                    "landscape",
                    "banner",
                    "fanart",
                    "clearlogo",
                    "clearart",
                    "keyart",
                    "thumb",
                )
                show_node = self._folder(
                    id_=f"tvshow_{show['tvshowid']}",
                    name=show_title,
                    play_url=self._kodi_uri("tvshow", show["tvshowid"]),
                    image=image,
                )

                seasons = await self._rpc_paginated(
                    "VideoLibrary.GetSeasons",
                    "seasons",
                    params={
                        "tvshowid": show["tvshowid"],
                        "properties": ["season", "art"],
                        "sort": {"method": "label", "order": "ascending"},
                    },
                )
                for season in seasons:
                    season_num = season.get("season")
                    if season_num is None:
                        continue

                    season_image = self._img(
                        season.get("art", {}),
                        "poster",
                        "season.poster",
                        "landscape",
                        "season.landscape",
                        "banner",
                        "fanart",
                        "thumb",
                        "tvshow.poster",
                    )
                    if not season_image:
                        season_image = image
                    season_node = self._folder(
                        id_=f"season_{show['tvshowid']}_{season_num}",
                        name=f"Season {season_num}",
                        play_url=self._kodi_uri("season", f"{show['tvshowid']}_{season_num}"),
                        image=season_image,
                    )

                    episodes = await self._rpc_paginated(
                        "VideoLibrary.GetEpisodes",
                        "episodes",
                        params={
                            "tvshowid": show["tvshowid"],
                            "season": season_num,
                            "properties": ["title", "season", "episode", "file", "art"],
                            "sort": {"method": "episode", "order": "ascending"},
                        },
                    )
                    for ep in episodes:
                        s = ep.get("season", 0)
                        e = ep.get("episode", 0)
                        ep_title = ep.get("title", "")
                        ep_image = self._img(
                            ep.get("art", {}),
                            "thumb",
                            "landscape",
                            "fanart",
                            "tvshow.poster",
                            "season.poster",
                        )
                        if not ep_image:
                            ep_image = season_image or image
                        season_node["tracks"].append(
                            self._leaf(
                                id_=f"episode_{ep['episodeid']}",
                                name=f"S{s:02d}E{e:02d} {ep_title}",
                                url=self._kodi_uri("episode", ep["episodeid"]),
                                image=ep_image,
                            )
                        )

                    if season_node["tracks"]:
                        show_node["tracks"].append(season_node)

                if not show_node["tracks"]:
                    episodes = await self._rpc_paginated(
                        "VideoLibrary.GetEpisodes",
                        "episodes",
                        params={
                            "tvshowid": show["tvshowid"],
                            "properties": ["title", "season", "episode", "file", "art"],
                            "sort": {"method": "episode", "order": "ascending"},
                        },
                    )
                    for ep in episodes:
                        s = ep.get("season", 0)
                        e = ep.get("episode", 0)
                        ep_title = ep.get("title", "")
                        ep_image = self._img(
                            ep.get("art", {}),
                            "thumb",
                            "landscape",
                            "fanart",
                            "tvshow.poster",
                            "season.poster",
                        )
                        if not ep_image:
                            ep_image = image
                        show_node["tracks"].append(
                            self._leaf(
                                id_=f"episode_{ep['episodeid']}",
                                name=f"S{s:02d}E{e:02d} {ep_title}",
                                url=self._kodi_uri("episode", ep["episodeid"]),
                                image=ep_image,
                            )
                        )

                if not show_node.get("image") and show_node["tracks"]:
                    show_node["image"] = show_node["tracks"][0].get("image", "")

                if show_node["tracks"]:
                    shows_root["tracks"].append(show_node)
            logger.info(f"  TV Shows: {len(shows_root['tracks'])}")

            # Live TV: Group -> Channel
            logger.info("Syncing Live TV...")
            groups_result = await self._rpc("PVR.GetChannelGroups", {"channeltype": "tv"}) or {}
            channel_groups = groups_result.get("channelgroups", []) if isinstance(groups_result, dict) else []
            for group in channel_groups:
                group_name = group.get("label") or group.get("channelgroup") or "Live TV"
                group_node = self._folder(
                    id_=f"channelgroup_{group.get('channelgroupid', '0')}",
                    name=group_name,
                )

                channels_result = await self._rpc(
                    "PVR.GetChannels",
                    {
                        "channelgroupid": group.get("channelgroupid"),
                        "properties": ["channel", "thumbnail", "icon", "channelnumber", "broadcastnow", "broadcastnext"],
                        "sort": {"method": "label", "order": "ascending"},
                    },
                ) or {}
                channels = channels_result.get("channels", []) if isinstance(channels_result, dict) else []
                for channel in channels:
                    if channel.get("hidden"):
                        continue

                    channel_name = channel.get("label") or channel.get("channel") or "Channel"
                    now_playing = channel.get("broadcastnow", {}) if isinstance(channel, dict) else {}
                    broadcast_title = ""
                    if isinstance(now_playing, dict):
                        broadcast_title = now_playing.get("label") or now_playing.get("title") or ""

                    image = self._img(
                        {
                            "thumb": channel.get("thumbnail", ""),
                            "icon": channel.get("icon", ""),
                        },
                        "thumb",
                        "icon",
                        "logo",
                        "banner",
                    )
                    if not image:
                        image = self._placeholder_art(channel_name, broadcast_title or group_name)

                    group_node["tracks"].append(
                        self._leaf(
                            id_=f"channel_{channel['channelid']}",
                            name=channel_name,
                            url=self._kodi_uri("channel", channel["channelid"]),
                            artist=group_name,
                            image=image,
                        )
                    )

                if group_node["tracks"]:
                    live_root["tracks"].append(group_node)

            logger.info(f"  Live TV groups: {len(live_root['tracks'])}")

            logger.info("Syncing playlists...")
            playlists_root["tracks"].extend(
                await self._load_playlist_nodes("special://videoplaylists", "Video Playlist")
            )
            playlists_root["tracks"].extend(
                await self._load_playlist_nodes("special://musicplaylists", "Music Playlist")
            )
            logger.info(f"  Playlists: {len(playlists_root['tracks'])}")

            self._normalize_library_tree(tree)
            await self._localize_tree_images(tree)
            self._library_data = tree
            self._save_cache(tree)
            self._cache_requires_resync = False
            logger.info("Kodi Library Sync Complete.")

        except Exception as e:
            logger.error(f"Kodi sync failed: {e}")
        finally:
            self._is_syncing = False

    # â”€â”€ HTTP routes â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def add_routes(self, app):
        async def _handle_playlists(request):
            if self._is_syncing and not self._library_data:
                return web.json_response({"loading": True}, headers=self._cors_headers())
            return web.json_response(self._library_data, headers=self._cors_headers())

        async def _handle_art(request):
            filename = os.path.basename(request.match_info.get("filename", ""))
            art_path = os.path.join(ART_CACHE_DIR, filename)
            if not filename or not os.path.exists(art_path):
                raise web.HTTPNotFound()
            response = web.FileResponse(art_path)
            response.headers.update(self._cors_headers())
            return response

        async def _handle_sync(request):
            """Manual sync trigger â€” POST /sync"""
            if not self._is_syncing:
                self._spawn(self.update_library_cache(), name="kodi_manual_sync")
            return web.json_response({"status": "syncing"}, headers=self._cors_headers())

        async def _handle_movie_details(request):
            raw_movie_id = request.match_info.get("movie_id", "")
            if raw_movie_id.startswith("movie_"):
                raw_movie_id = raw_movie_id.split("_", 1)[1]
            try:
                movie_id = int(raw_movie_id)
            except (TypeError, ValueError):
                return web.json_response(
                    {"state": "error", "reason": "invalid_movie_id"},
                    status=400,
                    headers=self._cors_headers(),
                )

            details_result = await self._rpc(
                "VideoLibrary.GetMovieDetails",
                {
                    "movieid": movie_id,
                    "properties": [
                        "title",
                        "plot",
                        "plotoutline",
                        "tagline",
                        "year",
                        "genre",
                        "runtime",
                        "rating",
                        "director",
                        "writer",
                        "cast",
                        "studio",
                        "country",
                        "premiered",
                        "mpaa",
                        "art",
                    ],
                },
            ) or {}
            movie = details_result.get("moviedetails") if isinstance(details_result, dict) else None
            if not movie:
                return web.json_response(
                    {"state": "error", "reason": "movie_not_found", "movieid": movie_id},
                    status=404,
                    headers=self._cors_headers(),
                )

            return web.json_response(
                await self._movie_page_payload(movie_id, movie),
                headers=self._cors_headers(),
            )

        async def _handle_episode_details(request):
            raw_episode_id = request.match_info.get("episode_id", "")
            if raw_episode_id.startswith("episode_"):
                raw_episode_id = raw_episode_id.split("_", 1)[1]
            try:
                episode_id = int(raw_episode_id)
            except (TypeError, ValueError):
                return web.json_response(
                    {"state": "error", "reason": "invalid_episode_id"},
                    status=400,
                    headers=self._cors_headers(),
                )

            details_result = await self._rpc(
                "VideoLibrary.GetEpisodeDetails",
                {
                    "episodeid": episode_id,
                    "properties": [
                        "title",
                        "plot",
                        "firstaired",
                        "runtime",
                        "rating",
                        "director",
                        "writer",
                        "cast",
                        "showtitle",
                        "season",
                        "episode",
                        "art",
                        "tvshowid",
                    ],
                },
            ) or {}
            episode = details_result.get("episodedetails") if isinstance(details_result, dict) else None
            if not episode:
                return web.json_response(
                    {"state": "error", "reason": "episode_not_found", "episodeid": episode_id},
                    status=404,
                    headers=self._cors_headers(),
                )

            tvshow_id = episode.get("tvshowid")
            if tvshow_id:
                show_result = await self._rpc(
                    "VideoLibrary.GetTVShowDetails",
                    {
                        "tvshowid": int(tvshow_id),
                        "properties": ["genre", "studio", "art"],
                    },
                ) or {}
                show_details = show_result.get("tvshowdetails") if isinstance(show_result, dict) else None
                if isinstance(show_details, dict):
                    if show_details.get("genre"):
                        episode["genre"] = show_details.get("genre")
                    if show_details.get("studio"):
                        episode["studio"] = show_details.get("studio")
                    if isinstance(show_details.get("art"), dict):
                        episode["show_art"] = show_details.get("art")
                        if not isinstance(episode.get("art"), dict) or not episode.get("art"):
                            episode["art"] = show_details.get("art")

            return web.json_response(
                await self._episode_page_payload(episode_id, episode),
                headers=self._cors_headers(),
            )

        async def _handle_channel_details(request):
            raw_channel_id = request.match_info.get("channel_id", "")
            if raw_channel_id.startswith("channel_"):
                raw_channel_id = raw_channel_id.split("_", 1)[1]
            try:
                channel_id = int(raw_channel_id)
            except (TypeError, ValueError):
                return web.json_response(
                    {"state": "error", "reason": "invalid_channel_id"},
                    status=400,
                    headers=self._cors_headers(),
                )

            details_result = await self._rpc(
                "PVR.GetChannelDetails",
                {
                    "channelid": channel_id,
                    "properties": [
                        "channel",
                        "thumbnail",
                        "icon",
                        "channelnumber",
                        "broadcastnow",
                        "broadcastnext",
                    ],
                },
            ) or {}
            channel = details_result.get("channeldetails") if isinstance(details_result, dict) else None
            if not channel:
                return web.json_response(
                    {"state": "error", "reason": "channel_not_found", "channelid": channel_id},
                    status=404,
                    headers=self._cors_headers(),
                )

            broadcast_details = None
            broadcast_now = channel.get("broadcastnow", {}) if isinstance(channel, dict) else {}
            broadcast_id = broadcast_now.get("broadcastid") if isinstance(broadcast_now, dict) else None
            if broadcast_id:
                broadcast_result = await self._rpc(
                    "PVR.GetBroadcastDetails",
                    {
                        "broadcastid": int(broadcast_id),
                        "properties": [
                            "title",
                            "plot",
                            "plotoutline",
                            "starttime",
                            "endtime",
                            "genre",
                            "thumbnail",
                            "cast",
                        ],
                    },
                ) or {}
                broadcast_details = (
                    broadcast_result.get("broadcastdetails")
                    if isinstance(broadcast_result, dict)
                    else None
                )

            return web.json_response(
                await self._channel_page_payload(channel_id, channel, broadcast_details),
                headers=self._cors_headers(),
            )

        app.router.add_get('/playlists', _handle_playlists)
        app.router.add_get('/art/{filename}', _handle_art)
        app.router.add_post('/sync', _handle_sync)
        app.router.add_get('/movie-details/{movie_id}', _handle_movie_details)
        app.router.add_get('/episode-details/{episode_id}', _handle_episode_details)
        app.router.add_get('/channel-details/{channel_id}', _handle_channel_details)

    # â”€â”€ Playback â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _library_root(self, root_id):
        for node in self._library_data or []:
            if isinstance(node, dict) and str(node.get("id") or "").strip() == str(root_id):
                return node
        return {}

    def _count_leaf_items(self, nodes):
        total = 0
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            children = node.get("tracks")
            if isinstance(children, list) and children:
                total += self._count_leaf_items(children)
            else:
                total += 1
        return total

    def _build_library_status(self):
        movies = self._library_root("movies").get("tracks") or []
        shows = self._library_root("tvshows").get("tracks") or []
        live_groups = self._library_root("livetv").get("tracks") or []
        return {
            "movies": len(movies),
            "tvshows": len(shows),
            "episodes": self._count_leaf_items(shows),
            "channel_groups": len(live_groups),
            "channels": self._count_leaf_items(live_groups),
        }

    @staticmethod
    def _configured_transfer_targets():
        return [
            {"id": target["id"], "name": target.get("name") or target["id"]}
            for target in get_video_targets()
        ]

    async def _build_watched_status(self, connected=None):
        watched = {"movies": None, "tvshows": None, "episodes": None}
        if not self._session:
            return watched
        if connected is None:
            connected = await self._ping()
        if not connected:
            return watched
        now = time.monotonic()
        cached = self._watched_status_cache or {}
        if now - float(cached.get("ts") or 0.0) < 60:
            return dict(cached.get("value") or watched)

        try:
            movies = await self._rpc_paginated(
                "VideoLibrary.GetMovies",
                "movies",
                params={"properties": ["playcount"]},
            )
            watched["movies"] = sum(1 for item in movies if int(item.get("playcount") or 0) > 0)
        except Exception:
            pass

        try:
            shows = await self._rpc_paginated(
                "VideoLibrary.GetTVShows",
                "tvshows",
                params={"properties": ["watchedepisodes", "playcount"]},
            )
            watched["tvshows"] = sum(
                1
                for item in shows
                if int(item.get("watchedepisodes") or item.get("playcount") or 0) > 0
            )
        except Exception:
            pass

        try:
            episodes = await self._rpc_paginated(
                "VideoLibrary.GetEpisodes",
                "episodes",
                params={"properties": ["playcount"]},
            )
            watched["episodes"] = sum(1 for item in episodes if int(item.get("playcount") or 0) > 0)
        except Exception:
            pass

        self._watched_status_cache = {"ts": now, "value": dict(watched)}
        return watched

    async def handle_status(self):
        status = await super().handle_status()
        connected = await self._ping() if self._session else False
        status.update(
            {
                "connected": connected,
                "syncing": self._is_syncing,
                "has_cache": bool(self._library_data),
                "cache_file": CACHE_FILE,
                "art_cache_dir": ART_CACHE_DIR,
                "library": self._build_library_status(),
                "watched": await self._build_watched_status(connected),
                "transfer_targets": self._configured_transfer_targets(),
                "player_id": self._preferred_player_id,
            }
        )
        return status

    def _walk_library(self):
        stack = [(node, []) for node in reversed(self._library_data or [])]
        while stack:
            node, parents = stack.pop()
            if not isinstance(node, dict):
                continue
            yield node, parents
            children = node.get("tracks")
            if isinstance(children, list):
                for child in reversed(children):
                    stack.append((child, parents + [node]))

    def _find_node_by_uri(self, uri):
        target = str(uri or "").strip()
        if not target:
            return None, []
        for node, parents in self._walk_library():
            if str(node.get("url") or "").strip() == target or str(node.get("play_url") or "").strip() == target:
                return node, parents
        return None, []

    @staticmethod
    def _format_clock(value):
        if not isinstance(value, dict):
            return ""
        hours = int(value.get("hours") or 0)
        minutes = int(value.get("minutes") or 0)
        seconds = int(value.get("seconds") or 0)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"

    async def _build_cached_media_payload(self, uri, state="playing"):
        node, parents = self._find_node_by_uri(uri)
        if not isinstance(node, dict):
            return None
        image = str(node.get("image") or "").strip()
        if image.startswith("http"):
            image = await self._cache_image_locally(image)
        album = ""
        if parents:
            album = str(parents[-1].get("name") or "").strip()
        artist = str(node.get("artist") or "").strip()
        if not artist and len(parents) > 1:
            artist = str(parents[-2].get("name") or "").strip()
        return {
            "title": str(node.get("name") or "").strip() or "Kodi",
            "artist": artist,
            "album": album,
            "artwork": image,
            "state": str(state or "playing").strip().lower() or "playing",
            "uri": str(uri or "").strip(),
        }

    async def _build_active_media_payload(self):
        if not self._session:
            return None
        active_players = await self._get_active_player_ids()
        if not active_players:
            return None

        player_id = active_players[0]
        properties = await self._rpc(
            "Player.GetProperties",
            {
                "playerid": player_id,
                "properties": ["speed", "time", "totaltime"],
            },
        ) or {}
        item_result = await self._rpc(
            "Player.GetItem",
            {
                "playerid": player_id,
                "properties": ["title", "album", "artist", "showtitle", "thumbnail", "art", "file"],
            },
        ) or {}
        item = item_result.get("item") if isinstance(item_result, dict) else {}
        if not isinstance(item, dict) or not item:
            return None

        item_type = str(item.get("type") or "").strip().lower()
        item_id = (
            item.get("movieid")
            or item.get("episodeid")
            or item.get("channelid")
        )
        uri = ""
        if item_type in {"movie", "episode", "channel"} and item_id is not None:
            uri = self._kodi_uri(item_type, item_id)

        node, parents = self._find_node_by_uri(uri)
        title = ""
        if isinstance(node, dict):
            title = str(node.get("name") or "").strip()
        if not title:
            title = str(item.get("title") or item.get("label") or "Kodi").strip()

        artist = self._join_values(item.get("artist"))
        if not artist and isinstance(node, dict):
            artist = str(node.get("artist") or "").strip()
        if not artist:
            artist = str(item.get("showtitle") or "").strip()
        if not artist and len(parents) > 1:
            artist = str(parents[-2].get("name") or "").strip()

        album = str(item.get("album") or "").strip()
        if not album and parents:
            album = str(parents[-1].get("name") or "").strip()
        if not album:
            album = str(item.get("showtitle") or "").strip()

        image = self._img(
            item.get("art", {}),
            "poster",
            "thumb",
            "landscape",
            "fanart",
            "banner",
            "clearlogo",
            "clearart",
            "keyart",
            "icon",
        )
        if not image:
            image = self._img({"thumb": item.get("thumbnail", "")}, "thumb")
        if not image and isinstance(node, dict):
            image = str(node.get("image") or "").strip()
        if image.startswith("http"):
            image = await self._cache_image_locally(image)

        speed = int(properties.get("speed") or 0)
        state = "paused" if speed == 0 else "playing"
        return {
            "title": title,
            "artist": artist,
            "album": album,
            "artwork": image,
            "state": state,
            "position": self._format_clock(properties.get("time")),
            "duration": self._format_clock(properties.get("totaltime")),
            "uri": uri,
        }

    async def _post_media_snapshot(self, payload, *, reason="track_change", force_state=""):
        if not isinstance(payload, dict):
            return False
        state = str(force_state or payload.get("state") or "").strip().lower()
        if state not in {"playing", "paused"}:
            await self.register("available")
            return False
        await self.register(state, auto_power=(state == "playing"))
        await self.post_media_update(
            title=str(payload.get("title") or "").strip(),
            artist=str(payload.get("artist") or "").strip(),
            album=str(payload.get("album") or "").strip(),
            artwork=str(payload.get("artwork") or "").strip(),
            state=state,
            duration=payload.get("duration", 0),
            position=payload.get("position", 0),
            reason=reason,
            track_uri=str(payload.get("uri") or "").strip(),
        )
        return True

    async def handle_resync(self) -> dict:
        payload = await self._build_active_media_payload()
        if payload:
            await self._post_media_snapshot(payload, reason="resync", force_state=payload.get("state", "playing"))
            return {"status": "ok", "resynced": True, "state": payload.get("state", "playing")}
        await self.register("available")
        return {"status": "ok", "resynced": False}

    async def _play_kodi_uri(self, uri):
        """
        Resolve a kodi:// URI and issue the appropriate Kodi API call.

        Single items use Player.Open directly.
        Folder items (tvshow, season) build a playlist first.
        """
        if not uri.startswith("kodi://"):
            logger.warning(f"Unknown URI format: {uri}")
            return False

        parts = uri[7:].split("/", 1)
        item_type = parts[0]
        item_id_raw = parts[1] if len(parts) > 1 else "0"
        try:
            item_id = item_id_raw if item_type in {"season", "playlist"} else int(item_id_raw)
        except ValueError:
            logger.warning(f"Invalid kodi:// URI: {uri}")
            return False

        try:
            started = False

            if item_type == "movie":
                started = await self._rpc("Player.Open", {"item": {"movieid": item_id}}) is not None

            elif item_type == "episode":
                started = await self._rpc("Player.Open", {"item": {"episodeid": item_id}}) is not None

            elif item_type == "channel":
                started = await self._rpc("Player.Open", {"item": {"channelid": item_id}}) is not None

            elif item_type == "tvshow":
                episodes = await self._rpc_paginated(
                    "VideoLibrary.GetEpisodes",
                    "episodes",
                    params={
                        "tvshowid": item_id,
                        "properties": [],
                        "sort": {"method": "episode", "order": "ascending"},
                    },
                )
                if not episodes:
                    return False
                if await self._rpc("Playlist.Clear", {"playlistid": PLAYLIST_VIDEO}) is None:
                    return False
                added = 0
                for ep in episodes:
                    if await self._rpc(
                        "Playlist.Add",
                        {"playlistid": PLAYLIST_VIDEO, "item": {"episodeid": ep["episodeid"]}},
                    ) is not None:
                        added += 1
                if not added:
                    return False
                started = await self._rpc("Player.Open", {"item": {"playlistid": PLAYLIST_VIDEO}}) is not None

            elif item_type == "season":
                show_id, season_num = map(int, str(item_id).split("_", 1)) if "_" in str(item_id) else (item_id, 0)
                episodes = await self._rpc_paginated(
                    "VideoLibrary.GetEpisodes",
                    "episodes",
                    params={
                        "tvshowid": show_id,
                        "season": season_num,
                        "properties": [],
                        "sort": {"method": "episode", "order": "ascending"},
                    },
                )
                if not episodes:
                    return False
                if await self._rpc("Playlist.Clear", {"playlistid": PLAYLIST_VIDEO}) is None:
                    return False
                added = 0
                for ep in episodes:
                    if await self._rpc(
                        "Playlist.Add",
                        {"playlistid": PLAYLIST_VIDEO, "item": {"episodeid": ep["episodeid"]}},
                    ) is not None:
                        added += 1
                if not added:
                    return False
                started = await self._rpc("Player.Open", {"item": {"playlistid": PLAYLIST_VIDEO}}) is not None

            elif item_type == "playlist":
                playlist_file = urllib.parse.unquote(str(item_id or "")).strip()
                if not playlist_file:
                    return False
                started = await self._rpc("Player.Open", {"item": {"file": playlist_file}}) is not None

            else:
                logger.warning(f"Unknown kodi:// type: {item_type}")
                return False

            if started:
                logger.info(f"Kodi playback started: {uri}")
                return True

            logger.warning(f"Kodi playback was not acknowledged: {uri}")
            return False

        except Exception as e:
            logger.error(f"Kodi playback error for {uri}: {e}")
            return False

    def _apply_playback_target_from_data(self, data):
        if not isinstance(data, dict):
            return ""
        target_player_id = str(
            data.get("target_player_id")
            or data.get("video_target_id")
            or (data.get("playback") or {}).get("video_target_id")
            or ""
        ).strip()
        if target_player_id:
            self._preferred_player_id = target_player_id
        return target_player_id

    async def _get_active_player_ids(self):
        result = await self._rpc("Player.GetActivePlayers") or []
        if isinstance(result, dict):
            result = result.get("players") or result.get("items") or []
        active_ids = [
            int(player.get("playerid"))
            for player in result
            if isinstance(player, dict) and str(player.get("playerid", "")).strip().isdigit()
        ]
        preferred = str(self._preferred_player_id or "").strip()
        if preferred.isdigit():
            preferred_id = int(preferred)
            active_ids = [preferred_id] + [player_id for player_id in active_ids if player_id != preferred_id]
        return active_ids

    async def _handle_transport_command(self, cmd) -> dict:
        active_players = await self._get_active_player_ids()
        if not active_players:
            return {"state": "error", "reason": "no_active_player", "command": cmd}

        if cmd == "transport_toggle":
            method = "Player.PlayPause"
            extra = {}
        elif cmd == "transport_stop":
            method = "Player.Stop"
            extra = {}
        elif cmd == "transport_next":
            method = "Player.GoTo"
            extra = {"to": "next"}
        elif cmd == "transport_previous":
            method = "Player.GoTo"
            extra = {"to": "previous"}
        else:
            return {"state": "error", "reason": "unsupported_transport_command", "command": cmd}

        for player_id in active_players:
            payload = {"playerid": player_id}
            payload.update(extra)
            result = await self._rpc(method, payload)
            if result is not None:
                if cmd == "transport_stop":
                    await self.register("available")
                    return {"state": "available", "player_id": player_id, "command": cmd}

                await asyncio.sleep(0.35)
                media_payload = await self._build_active_media_payload()
                if media_payload:
                    await self._post_media_snapshot(
                        media_payload,
                        reason=cmd,
                        force_state=media_payload.get("state", "playing"),
                    )
                    return {
                        "state": media_payload.get("state", "available"),
                        "player_id": player_id,
                        "command": cmd,
                        "uri": media_payload.get("uri", ""),
                    }

                if self._last_media:
                    next_state = "paused" if cmd == "transport_toggle" and self._registered_state == "playing" else "playing"
                    await self.register(next_state, auto_power=(next_state == "playing"))
                    await self.post_media_update(
                        title=self._last_media.get("title", ""),
                        artist=self._last_media.get("artist", ""),
                        album=self._last_media.get("album", ""),
                        artwork=self._last_media.get("artwork", ""),
                        state=next_state,
                        reason=cmd,
                        track_uri=self._last_media.get("track_uri", ""),
                    )
                    return {
                        "state": next_state,
                        "player_id": player_id,
                        "command": cmd,
                        "uri": self._last_media.get("track_uri", ""),
                    }
                return {"state": "available", "player_id": player_id, "command": cmd}

        return {"state": "error", "reason": "transport_command_failed", "command": cmd}

    async def _active_playlist_context(self):
        active_players = await self._get_active_player_ids()
        if not active_players:
            return -1, -1, -1
        player_id = active_players[0]
        properties = await self._rpc(
            "Player.GetProperties",
            {"playerid": player_id, "properties": ["playlistid", "position"]},
        ) or {}
        playlist_id = properties.get("playlistid")
        position = properties.get("position")
        try:
            playlist_id = int(playlist_id)
        except (TypeError, ValueError):
            playlist_id = PLAYLIST_VIDEO
        try:
            position = int(position)
        except (TypeError, ValueError):
            position = -1
        return player_id, playlist_id, position

    @staticmethod
    def _playlist_item_ref(item):
        if not isinstance(item, dict):
            return {}
        item_type = str(item.get("type") or "").lower()
        if item_type == "movie" and item.get("movieid") is not None:
            return {"movieid": item.get("movieid")}
        if item_type == "episode" and item.get("episodeid") is not None:
            return {"episodeid": item.get("episodeid")}
        if item_type == "song" and item.get("songid") is not None:
            return {"songid": item.get("songid")}
        if item_type == "musicvideo" and item.get("musicvideoid") is not None:
            return {"musicvideoid": item.get("musicvideoid")}
        file_path = str(item.get("file") or "").strip()
        return {"file": file_path} if file_path else {}

    def _art_from_item(self, item):
        if not isinstance(item, dict):
            return ""
        image = self._img(
            item.get("art", {}),
            "poster",
            "thumb",
            "landscape",
            "fanart",
            "banner",
            "clearlogo",
            "clearart",
            "keyart",
            "icon",
        )
        if not image:
            image = self._img({"thumb": item.get("thumbnail", "")}, "thumb")
        return image

    async def _playlist_items(self, playlist_id):
        result = await self._rpc(
            "Playlist.GetItems",
            {
                "playlistid": playlist_id,
                "properties": [
                    "title", "album", "artist", "showtitle", "thumbnail",
                    "art", "file", "duration",
                ],
            },
        ) or {}
        items = result.get("items") if isinstance(result, dict) else result
        return items if isinstance(items, list) else []

    async def get_queue(self, start=0, max_items=50):
        _player_id, playlist_id, current_index = await self._active_playlist_context()
        if playlist_id < 0:
            return {"tracks": [], "current_index": -1, "total": 0}
        items = await self._playlist_items(playlist_id)
        try:
            start = max(0, int(start))
        except (TypeError, ValueError):
            start = 0
        try:
            max_items = max(1, int(max_items))
        except (TypeError, ValueError):
            max_items = 50
        tracks = []
        for index, item in enumerate(items[start:start + max_items], start=start):
            title = str(item.get("title") or item.get("label") or item.get("file") or f"Queue Item {index + 1}")
            artists = item.get("artist")
            artist = ", ".join(artists) if isinstance(artists, list) else str(artists or item.get("showtitle") or "")
            artwork = self._art_from_item(item)
            if artwork.startswith("http"):
                artwork = await self._cache_image_locally(artwork)
            tracks.append({
                "id": f"kodi:{playlist_id}:{index}",
                "title": title,
                "artist": artist,
                "album": str(item.get("album") or ""),
                "artwork": artwork,
                "uri": str(item.get("file") or ""),
                "index": index,
                "current": index == current_index,
            })
        return {
            "tracks": tracks,
            "current_index": current_index,
            "total": len(items),
            "queue_id": str(playlist_id),
        }

    async def _handle_queue_remove_command(self, data):
        _player_id, playlist_id, _current_index = await self._active_playlist_context()
        try:
            position = int(data.get("index"))
        except (TypeError, ValueError):
            return {"state": "error", "reason": "missing_index"}
        result = await self._rpc("Playlist.Remove", {"playlistid": playlist_id, "position": position})
        if result is None:
            return {"state": "error", "reason": "remove_failed", "index": position}
        return {"state": "removed", "queue_id": str(playlist_id), "index": position}

    async def _handle_queue_play_next_command(self, data):
        _player_id, playlist_id, current_index = await self._active_playlist_context()
        try:
            position = int(data.get("index"))
        except (TypeError, ValueError):
            return {"state": "error", "reason": "missing_index"}
        items = await self._playlist_items(playlist_id)
        if position < 0 or position >= len(items):
            return {"state": "error", "reason": "queue_item_not_found", "index": position}
        item_ref = self._playlist_item_ref(items[position])
        if not item_ref:
            return {"state": "error", "reason": "queue_item_unaddressable", "index": position}
        target_index = max(0, current_index + 1) if current_index >= 0 else 0
        remove_result = await self._rpc("Playlist.Remove", {"playlistid": playlist_id, "position": position})
        if remove_result is None:
            return {"state": "error", "reason": "remove_failed", "index": position}
        if position < target_index:
            target_index -= 1
        insert_result = await self._rpc(
            "Playlist.Insert",
            {"playlistid": playlist_id, "position": target_index, "item": item_ref},
        )
        if insert_result is None:
            await self._rpc("Playlist.Add", {"playlistid": playlist_id, "item": item_ref})
            return {"state": "error", "reason": "insert_failed", "index": position}
        return {
            "state": "moved",
            "queue_id": str(playlist_id),
            "index": position,
            "target_index": target_index,
        }

    async def _handle_queue_play_index_command(self, data):
        player_id, _playlist_id, _current_index = await self._active_playlist_context()
        if player_id < 0:
            return {"state": "error", "reason": "no_active_player"}
        try:
            position = int(data.get("index", data.get("position")))
        except (TypeError, ValueError):
            return {"state": "error", "reason": "missing_index"}
        result = await self._rpc("Player.GoTo", {"playerid": player_id, "to": position})
        if result is None:
            return {"state": "error", "reason": "goto_failed", "index": position}
        await asyncio.sleep(0.2)
        payload = await self._build_active_media_payload()
        if payload:
            await self._post_media_snapshot(payload, reason="queue_play", force_state="playing")
        return {"state": "playing", "index": position, "player_id": player_id}

    async def handle_command(self, cmd, data) -> dict:
        self._apply_playback_target_from_data(data)
        if cmd in {"transport_toggle", "transport_stop", "transport_next", "transport_previous"}:
            return await self._handle_transport_command(cmd)
        if cmd == "queue_remove":
            return await self._handle_queue_remove_command(data)
        if cmd == "queue_play_next":
            return await self._handle_queue_play_next_command(data)
        if cmd == "play_index":
            return await self._handle_queue_play_index_command(data)

        uri = data.get("url", "") or data.get("play_url", "")
        if not uri:
            logger.warning("Kodi: no URI in command payload: %s", data)
            return {"state": "error", "reason": "unresolved_uri"}

        logger.info("Kodi playback cmd=%s uri=%s", cmd, uri)
        ok = await self._play_kodi_uri(uri)
        if not ok:
            return {"state": "error", "reason": "playback_rejected", "uri": uri}
        await asyncio.sleep(0.2)
        payload = await self._build_active_media_payload()
        if not payload:
            payload = await self._build_cached_media_payload(uri, state="playing")
        if payload:
            await self._post_media_snapshot(payload, reason="track_change", force_state="playing")
        else:
            await self.register("playing", auto_power=True)
        return {"state": "playing", "uri": uri}

if __name__ == '__main__':
    asyncio.run(KodiSource().run())
