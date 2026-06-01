#!/usr/bin/env python3
"""
Music Assistant (MASS) source service for BeoSound 5c.

Connects to MASS via WebSocket, caches the full library hierarchy to disk,
and exposes it via HTTP for the mass.html frontend.

Library structure served at /playlists:
  [
    { id: "artists",   name: "Artists",   tracks: [ <artist-folder>, ... ] },
    { id: "albums",    name: "Albums",    tracks: [ <album-folder>,  ... ] },
    { id: "songs",     name: "Songs",     tracks: [ <track-leaf>,    ... ] },
    { id: "playlists",       name: "Playlists", tracks: [ <playlist-folder>, ... ] },
    { id: "playlist_mixes",  name: "Mixes",     tracks: [ <track-leaf>, ... ] },
    { id: "podcasts",        name: "Podcasts",  tracks: [ <podcast-folder>, ... ] },
    { id: "mixes",           name: "Radio",     tracks: [ <radio-leaf>, ... ] },
  ]

Node contracts (enforced at construction + _finalize_node):
  FOLDER node → has `tracks` list, has `play_url` (MA URI to play whole folder),
                NO `url` key (url presence tells ArcList to auto-play, not navigate).
  LEAF   node → has `url` (playback URI), NO `tracks` key.
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
import urllib.parse
import websockets
from email.utils import parsedate_to_datetime
from aiohttp import web, ClientSession, ClientTimeout

# ── Path bootstrap ────────────────────────────────────────────────────────────
current_dir = os.path.dirname(os.path.abspath(__file__))
while current_dir != '/' and 'lib' not in os.listdir(current_dir):
    current_dir = os.path.dirname(current_dir)
if 'lib' in os.listdir(current_dir):
    sys.path.insert(0, current_dir)

from lib.config import cfg
from lib.mass_playback import (
    get_configured_mass_playback_mode,
    mass_local_backend_ready,
    mass_prefers_local_playback,
    mass_runtime_playback_path,
)
from lib.playback_targets import get_audio_targets
from lib.source_base import SourceBase

# ── CONFIG ────────────────────────────────────────────────────────────────────
def _mass_ws_url():
    configured = (os.getenv("MASS_WS_URL") or os.getenv("BS5C_MASS_WS_URL") or "").strip()
    if configured:
        return configured
    host = (os.getenv("PLAYER_IP") or cfg("player", "ip", default="") or "").strip()
    if host:
        if host.startswith("ws://") or host.startswith("wss://"):
            return host
        return f"ws://{host}:8095/ws"
    return "ws://localhost:8095/ws"


MASS_URI = _mass_ws_url()
MASS_TOKEN = os.getenv("MASS_TOKEN", "").strip()
TARGET_QUEUE_ID = (
    os.getenv("MASS_QUEUE_ID")
    or os.getenv("BS5C_MASS_TARGET_QUEUE_ID")
    or cfg("mass", "queue_id", default="")
    or cfg("mass", "target_queue_id", default="")
    or ""
).strip()
TARGET_PLAYER_ID = (
    os.getenv("MASS_PLAYER_ID")
    or os.getenv("BS5C_MASS_TARGET_PLAYER_ID")
    or cfg("mass", "player_id", default="")
    or cfg("mass", "target_player_id", default="")
    or ""
).strip()
CACHE_FILE       = "/media/local/cache/mass_playlists.json"
LEGACY_CACHE_FILE = "/home/thomas/beosound5c/web/json/mass_playlists.json"
ART_CACHE_DIR    = "/media/local/cache/mass_art"
ART_ROUTE_PREFIX = "/art"
ART_FETCH_CONCURRENCY = 6
PLAYLIST_CHILD_FETCH_CONCURRENCY = 8
PODCAST_CHILD_FETCH_CONCURRENCY = 4
PLAYBACK_PRE_KICK_ATTEMPTS = 1
PLAYBACK_PRE_KICK_DELAY = 0.12
PLAYBACK_POST_KICK_ATTEMPTS = 6
PLAYBACK_POST_KICK_DELAY = 0.35
ACTIVE_PLAYBACK_STATES = {"playing", "paused", "buffering"}
MASS_MIXES_PLAYLIST_ID = "98"
MASS_MIXES_PLAYLIST_PROVIDER = "library"
MASS_MIXES_PLAYLIST_ROOT_ID = "playlist_mixes"
MASS_MIXES_PLAYLIST_TITLE = "Mixes"
MASS_PODCASTS_ROOT_ID = "podcasts"
MASS_PODCASTS_ROOT_TITLE = "Podcasts"
MASS_RADIO_ROOT_ID = "mixes"
MASS_RADIO_ROOT_TITLE = "Radio"
ROOT_CATEGORY_IDS = {
    "queue",
    "artists",
    "albums",
    "songs",
    "playlists",
    MASS_MIXES_PLAYLIST_ROOT_ID,
    MASS_PODCASTS_ROOT_ID,
    MASS_RADIO_ROOT_ID,
}
LOCAL_QUEUE_ITEM_PREFIX = "local_queue_item_"
LOCAL_QUEUE_POLL_INTERVAL = 0.5
LOCAL_QUEUE_TRANSITION_GRACE = 2.5
LOCAL_STREAM_HINT_KEYS = {
    "stream_url",
    "path",
    "content_url",
    "audio_url",
    "file_url",
    "stream_source",
    "streamsource",
}
LOCAL_STREAM_CONTAINER_HINTS = {
    "details",
    "media_item",
    "provider_mappings",
    "stream",
    "streams",
    "playback",
    "audio",
    "source",
    "sources",
    "variant",
    "variants",
}
LOCAL_STREAMISH_MARKERS = {
    "content_type",
    "mime_type",
    "audio_format",
    "codec",
    "sample_rate",
    "bit_depth",
    "bitrate",
    "expires",
}
LOCAL_ARTWORK_HINTS = (
    "image",
    "artwork",
    "cover",
    "thumb",
    "icon",
    "logo",
    "poster",
    "fanart",
    "backdrop",
)
LOCAL_IMAGE_SUFFIXES = (
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".bmp",
    ".svg",
    ".ico",
    ".avif",
    ".tiff",
)

FALLBACK_IMAGE = (
    "data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iNjQiIGhlaWdodD0iNjQiIHZpZXdCb3g9IjAgMCA2NCA2NCIg"
    "ZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHJlY3Qgd2lkdGg9IjY0IiBoZWln"
    "aHQ9IjY0IiBmaWxsPSIjMzMzMzMzIi8+Cjx0ZXh0IHg9IjMyIiB5PSI0MCIgZm9udC1mYW1pbHk9IkFyaWFsLCBzYW5z"
    "LXNlcmlmIiBmb250LXNpemU9IjI0IiBmaWxsPSIjZmZmZmZmIiB0ZXh0LWFuY2hvcj0ibWlkZGxlIj7imqo8L3RleHQ+"
    "Cjwvc3ZnPgo="
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("beo-source-mass")


class MassSource(SourceBase):
    id   = "mass"
    name = "Music Assistant"
    port = 8783
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
        self._detect_player()
        self.websocket    = None
        self._connected   = False
        self._futures     = {}
        self._library_data = []
        self._library_node_by_id = {}
        self._library_node_by_uri = {}
        self._is_syncing  = False
        self._http_session = None
        self._preferred_player_id = ""
        self._local_queue_entries = []
        self._local_queue_index = -1
        self._local_queue_active = False
        self._local_stream_cache = {}
        self._local_queue_monitor_task = None
        self._local_queue_transition_deadline = 0.0
        self._local_queue_last_player_state = "stopped"
        self.has_cache    = self._load_local_cache()

    def _detect_player(self):
        self.player = self._runtime_playback_path()

    def _configured_playback_mode(self):
        return get_configured_mass_playback_mode()

    def _prefers_local_playback(self):
        return mass_prefers_local_playback()

    def _local_player_ready(self):
        return mass_local_backend_ready()

    def _runtime_playback_path(self):
        return mass_runtime_playback_path()

    def _forced_local_playback(self):
        return self._configured_playback_mode() == "local"

    def _should_try_local_playback(self):
        return self._runtime_playback_path() == "local"

    # ── Cache ─────────────────────────────────────────────────────────────────

    def _load_local_cache(self):
        try:
            cache_path = CACHE_FILE if os.path.exists(CACHE_FILE) else LEGACY_CACHE_FILE
            if os.path.exists(cache_path):
                with open(cache_path, 'r') as f:
                    self._library_data = json.load(f)
                self._normalize_library_tree(self._library_data)
                self._upgrade_cached_library_identities(self._library_data)
                self._rebuild_library_indexes()
                self._write_json_file(CACHE_FILE, self._library_data)
                self._write_json_file(LEGACY_CACHE_FILE, self._library_data)
                logger.info("Local library cache loaded from %s.", cache_path)
                return True
            logger.info("No local cache found — initial sync needed.")
            return False
        except Exception as e:
            logger.error(f"Failed to load local cache: {e}")
            return False

    @staticmethod
    def _write_json_file(path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as handle:
            json.dump(data, handle, separators=(',', ':'))

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def on_start(self):
        logger.info("MASS Source Starting...")
        if not MASS_TOKEN:
            logger.warning("MASS source missing MASS_TOKEN; login/bootstrap actions will be required.")
        self._http_session = ClientSession(timeout=ClientTimeout(total=20))
        await self.register("available")
        self._spawn(self._maintain_connection(), name="mass_connection")
        self._spawn(self._schedule_sync_loop(), name="mass_sync_loop")

    async def on_stop(self):
        await self._stop_local_queue_monitor()
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()
        self._http_session = None

    async def _schedule_sync_loop(self):
        if not self.has_cache:
            while not self._connected:
                await asyncio.sleep(2)
            logger.info("No cache — running initial library sync.")
            await self.update_library_cache()

        while True:
            now       = datetime.datetime.now()
            next_sync = now.replace(hour=2, minute=0, second=0, microsecond=0)
            if now >= next_sync:
                next_sync += datetime.timedelta(days=1)
            next_sync += datetime.timedelta(days=1)
            wait_s = (next_sync - now).total_seconds()
            logger.info(f"Next sync in {wait_s / 3600:.1f}h.")
            await asyncio.sleep(wait_s)
            while not self._connected:
                await asyncio.sleep(10)
            logger.info("Triggering scheduled 48h library sync...")
            await self.update_library_cache()

    # ── WebSocket ─────────────────────────────────────────────────────────────

    async def _maintain_connection(self):
        while True:
            if not self._connected:
                await self.connect()
            await asyncio.sleep(10)

    async def connect(self):
        try:
            self.websocket = await websockets.connect(MASS_URI, max_size=20_000_000)
            await self.websocket.recv()  # server hello

            msg_id = f"auth-{os.urandom(2).hex()}"
            await self.websocket.send(json.dumps({
                "message_id": msg_id,
                "command":    "auth",
                "args":       {"token": MASS_TOKEN},
            }))

            auth_res = json.loads(await self.websocket.recv())
            if auth_res.get("result", {}).get("authenticated"):
                self._connected = True
                logger.info("MASS authenticated.")
                self._spawn(self._listen_loop(), name="mass_listener")
            else:
                logger.error("MASS authentication failed.")
                await self.websocket.close()
        except Exception as e:
            logger.warning(f"MASS connection failed: {e}")
            self._connected = False

    async def _send_command_response(self, command, **kwargs):
        for attempt in range(2):
            if not self.websocket or not self._connected:
                await asyncio.sleep(1)
                continue

            msg_id = f"beo-{os.urandom(2).hex()}"
            fut    = asyncio.get_running_loop().create_future()
            self._futures[msg_id] = fut
            try:
                await self.websocket.send(json.dumps({
                    "message_id": msg_id,
                    "command":    command,
                    "args":       kwargs,
                }))
                response = await asyncio.wait_for(fut, timeout=25)
                if isinstance(response, dict) and "error" in response:
                    logger.error(f"API Error [{command}]: {response['error']}")
                    return None
                return response
            except asyncio.TimeoutError:
                continue
            except Exception:
                continue
            finally:
                self._futures.pop(msg_id, None)
        return None

    async def send_command(self, command, **kwargs):
        response = await self._send_command_response(command, **kwargs)
        if not isinstance(response, dict):
            return None
        return response.get("result")

    async def _listen_loop(self):
        try:
            async for msg in self.websocket:
                data = json.loads(msg)
                mid  = data.get("message_id")
                if mid in self._futures and not self._futures[mid].done():
                    self._futures[mid].set_result(data)
        except Exception:
            self._connected = False

    # ── Image helpers ─────────────────────────────────────────────────────────

    def _get_img(self, item, base):
        if not item:
            return ""
        images = item.get("metadata", {}).get("images", [])
        if not images:
            return ""

        best = next(
            (img for img in images if isinstance(img, dict)
             and img.get("type") in ("thumb", "landscape", "poster")),
            images[0],
        )

        if isinstance(best, dict):
            path     = best.get("path", "")
            provider = best.get("provider", "library")
            if path:
                clean = urllib.parse.unquote(path)
                if "tidal" in provider.lower() and not clean.endswith(".jpg"):
                    clean = (
                        clean + "x750.jpg" if clean.endswith("750")
                        else clean.rstrip("/") + "/750x750.jpg"
                    )
                encoded = (
                    urllib.parse.quote(urllib.parse.quote(clean, safe=''), safe='')
                    if clean.startswith("http")
                    else urllib.parse.quote(clean, safe='')
                )
                return f"{base}/imageproxy?path={encoded}&provider={provider}&size=256"
        return ""

    def _get_artist_name(self, item, default="Various"):
        artists = item.get("artists", [])
        if isinstance(artists, list) and artists and isinstance(artists[0], dict):
            return artists[0].get("name", default)
        return default

    def _get_track_artist_for_album(self, album_artist, track):
        track_artist = self._get_artist_name(track, "")
        if not track_artist:
            return ""
        if not album_artist or album_artist == "Various" or track_artist != album_artist:
            return track_artist
        return ""

    def _get_album_name(self, item, default=""):
        album = item.get("album") if isinstance(item, dict) else None
        if isinstance(album, dict):
            return str(album.get("name") or default or "").strip()
        if isinstance(album, str):
            return str(album or default or "").strip()
        return str(default or "").strip()

    @staticmethod
    def _normalize_lookup_text(value):
        return " ".join(str(value or "").split()).strip().casefold()

    @staticmethod
    def _track_album_item_id(item):
        album = item.get("album") if isinstance(item, dict) else None
        if isinstance(album, dict):
            return str(album.get("item_id") or "").strip()
        return ""

    @staticmethod
    def _item_artist_refs(item):
        artists = item.get("artists") if isinstance(item, dict) else None
        refs = []
        if not isinstance(artists, list):
            return refs
        for artist in artists:
            if not isinstance(artist, dict):
                continue
            item_id = str(artist.get("item_id") or "").strip()
            name = str(artist.get("name") or "").strip()
            uri = str(artist.get("uri") or "").strip()
            if item_id or name or uri:
                refs.append({
                    "item_id": item_id,
                    "name": name,
                    "uri": uri,
                })
        return refs

    @staticmethod
    def _album_track_sort_key(item):
        if not isinstance(item, dict):
            return (0, 0, "", "")

        def as_int(value):
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        return (
            as_int(item.get("disc_number")),
            as_int(item.get("track_number")),
            str(item.get("sort_name") or item.get("name") or "").casefold(),
            str(item.get("item_id") or "").strip(),
        )

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

    @staticmethod
    def _typed_media_node_id(media_type, item_id):
        media = str(media_type or "").strip().lower()
        raw_item_id = str(item_id or "").strip()
        if not media or not raw_item_id:
            return raw_item_id
        return f"{media}:{raw_item_id}"

    def _apply_media_identity(self, node, *, media_type="", item_id="", provider="", provider_item_id=""):
        if not isinstance(node, dict):
            return node
        raw_item_id = str(item_id or "").strip()
        normalized_type = str(media_type or "").strip().lower()
        current_id = str(node.get("id") or "").strip()
        if raw_item_id:
            node["item_id"] = raw_item_id
        if normalized_type:
            node["media_type"] = normalized_type
        if provider:
            node["provider"] = str(provider).strip()
        if provider_item_id:
            node["provider_item_id"] = str(provider_item_id).strip()
        if raw_item_id and normalized_type and (not current_id or current_id == raw_item_id):
            node["id"] = self._typed_media_node_id(normalized_type, raw_item_id)
        return node

    def _normalize_library_tree(self, tree):
        if not isinstance(tree, list):
            return

        roots = {
            str(node.get("id") or ""): node
            for node in tree
            if isinstance(node, dict)
        }

        artists_root = roots.get("artists")
        if isinstance(artists_root, dict):
            for artist_node in artists_root.get("tracks") or []:
                if isinstance(artist_node, dict):
                    artist_node["tracks"] = self._sorted_nodes(artist_node.get("tracks"))
            artists_root["tracks"] = self._sorted_nodes(artists_root.get("tracks"))

        for root_id in (
            "albums",
            "songs",
            "playlists",
            MASS_MIXES_PLAYLIST_ROOT_ID,
            MASS_PODCASTS_ROOT_ID,
            MASS_RADIO_ROOT_ID,
        ):
            root_node = roots.get(root_id)
            if isinstance(root_node, dict):
                if root_id == MASS_PODCASTS_ROOT_ID:
                    root_node["tracks"] = list(root_node.get("tracks") or [])
                else:
                    root_node["tracks"] = self._sorted_nodes(root_node.get("tracks"))
                if root_id == MASS_RADIO_ROOT_ID:
                    root_node["name"] = MASS_RADIO_ROOT_TITLE
                elif root_id == MASS_MIXES_PLAYLIST_ROOT_ID:
                    root_node["name"] = MASS_MIXES_PLAYLIST_TITLE
                elif root_id == MASS_PODCASTS_ROOT_ID:
                    root_node["name"] = MASS_PODCASTS_ROOT_TITLE

    def _upgrade_cached_library_identities(self, tree):
        if not isinstance(tree, list):
            return

        def legacy_raw_id(node):
            if not isinstance(node, dict):
                return ""
            existing_item_id = str(node.get("item_id") or "").strip()
            if existing_item_id:
                return existing_item_id
            node_id = str(node.get("id") or "").strip()
            if not node_id:
                return ""
            if node_id in ROOT_CATEGORY_IDS or self._is_local_queue_item_id(node_id):
                return ""
            if ":" in node_id:
                prefix, _, remainder = node_id.partition(":")
                if prefix and remainder:
                    return remainder
            return node_id

        def walk(node, root_id="", depth=0):
            if not isinstance(node, dict):
                return
            node_id = str(node.get("id") or "").strip()
            next_root_id = root_id or node_id
            children = node.get("tracks")
            has_children = isinstance(children, list) and bool(children)
            media_type = ""
            if next_root_id == "artists":
                media_type = "artist" if depth == 1 else ("album" if depth == 2 and has_children else "track")
            elif next_root_id == "albums":
                media_type = "album" if depth == 1 and has_children else "track"
            elif next_root_id == "songs":
                media_type = "track"
            elif next_root_id == "playlists":
                media_type = "playlist" if depth == 1 and has_children else "track"
            elif next_root_id == MASS_MIXES_PLAYLIST_ROOT_ID:
                media_type = "playlist" if depth == 0 else "track"
            elif next_root_id == MASS_PODCASTS_ROOT_ID:
                media_type = "podcast" if depth == 1 and has_children else "podcast_episode"
            elif next_root_id == MASS_RADIO_ROOT_ID:
                media_type = "radio"

            raw_item_id = legacy_raw_id(node)
            if media_type and raw_item_id:
                self._apply_media_identity(
                    node,
                    media_type=media_type,
                    item_id=raw_item_id,
                    provider=node.get("provider", ""),
                    provider_item_id=node.get("provider_item_id", ""),
                )

            if isinstance(children, list):
                for child in children:
                    walk(child, next_root_id, depth + 1)

        for root in tree:
            walk(root)

    def _rebuild_library_indexes(self):
        node_by_id = {}
        node_by_uri = {}
        stack = list(self._library_data or [])
        while stack:
            node = stack.pop()
            if not isinstance(node, dict):
                continue
            node_id = str(node.get("id") or "").strip()
            if node_id and node_id not in node_by_id:
                node_by_id[node_id] = node
            uri = str(node.get("url") or node.get("play_url") or "").strip()
            if uri and uri not in node_by_uri:
                node_by_uri[uri] = node
            children = node.get("tracks")
            if isinstance(children, list):
                stack.extend(reversed(children))
        self._library_node_by_id = node_by_id
        self._library_node_by_uri = node_by_uri

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
        if not image_url or not image_url.startswith("http"):
            return image_url

        cached_name = self._find_cached_art_name(image_url)
        if cached_name:
            return f"{ART_ROUTE_PREFIX}/{cached_name}"

        if not self._http_session:
            return image_url

        try:
            os.makedirs(ART_CACHE_DIR, exist_ok=True)
            async with self._http_session.get(image_url) as response:
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
        except Exception as e:
            logger.debug("Failed to cache MASS artwork %s: %s", image_url, e)
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
            local_url = replacements.get(image_url, image_url)
            for node in nodes:
                node["image"] = local_url

    # ── Fetch helpers ─────────────────────────────────────────────────────────

    async def fetch_list(self, cmd, **kwargs):
        res = await self.send_command(cmd, **kwargs)
        if not res:
            return []
        return res.get('items', []) if isinstance(res, dict) else (res if isinstance(res, list) else [])

    async def fetch_paginated(self, cmd, **kwargs):
        all_items, limit, offset = [], 500, 0
        while True:
            kwargs['limit'], kwargs['offset'] = limit, offset
            res = await self.send_command(cmd, **kwargs)
            if not res:
                break
            batch = (res.get('items', []) if isinstance(res, dict)
                     else (res if isinstance(res, list) else []))
            if not batch:
                break
            all_items.extend(batch)
            if len(batch) < limit:
                break
            offset += limit
            await asyncio.sleep(0.05)
        return all_items

    # ── Node constructors ─────────────────────────────────────────────────────
    #
    # FOLDER: has `tracks` + `play_url` (the MA URI to play the whole folder).
    #         NO `url` key — ArcList treats `url` presence as "this is playable".
    #         `play_url` is kept by _finalize_node and used by handle_command.
    # LEAF:   has `url` (direct playback URI), NO `tracks`.

    def _make_folder_node(self, id_, name, artist="", image="", url="", album=""):
        """Navigable folder node. Stores the MA URI as `url`; _finalize_node
        renames it to `play_url` so ArcList does not auto-play on navigation."""
        node = {
            "id":     id_,
            "name":   name,
            "tracks": [],
        }
        if artist: node["artist"] = artist
        if album:  node["album"]  = album
        if image:  node["image"]  = image
        if url:    node["url"]    = url   # renamed to play_url by _finalize_node
        return node

    def _make_leaf_node(self, id_, name, artist="", url="", image="", album=""):
        """Playable leaf. Has `url`, no `tracks`."""
        node = {
            "id":   id_,
            "name": name,
            "url":  url,
        }
        if artist: node["artist"] = artist
        if album:  node["album"]  = album
        if image:  node["image"]  = image
        return node

    @staticmethod
    def _playlist_uri(item_id, provider=MASS_MIXES_PLAYLIST_PROVIDER):
        item = str(item_id or "").strip()
        source = str(provider or MASS_MIXES_PLAYLIST_PROVIDER).strip() or MASS_MIXES_PLAYLIST_PROVIDER
        return f"playlist://{source}/{item}" if item else ""

    @staticmethod
    def _is_mixes_playlist(item):
        if not isinstance(item, dict):
            return False
        item_id = str(item.get("item_id") or item.get("id") or "").strip()
        provider = str(item.get("provider") or MASS_MIXES_PLAYLIST_PROVIDER).strip().lower()
        uri = str(item.get("uri") or item.get("path") or "").strip().lower()
        return (
            item_id == MASS_MIXES_PLAYLIST_ID
            and (
                provider == MASS_MIXES_PLAYLIST_PROVIDER
                or uri.endswith(f"/{MASS_MIXES_PLAYLIST_PROVIDER}/{MASS_MIXES_PLAYLIST_ID}")
                or uri.endswith(f"://{MASS_MIXES_PLAYLIST_PROVIDER}/{MASS_MIXES_PLAYLIST_ID}")
            )
        )

    @staticmethod
    def _provider_item_lookup_key(item_id="", provider="library"):
        item_value = str(item_id or "").strip()
        if not item_value:
            return ""
        provider_value = str(provider or "library").strip() or "library"
        return f"{provider_value}::{item_value}"

    @classmethod
    def _item_provider_lookup_key(cls, item, default_provider="library"):
        if not isinstance(item, dict):
            return ""
        return cls._provider_item_lookup_key(
            item.get("item_id", ""),
            item.get("provider", default_provider),
        )

    def _build_playlist_folder_node(self, playlist, tracks, base, *, root_id="", root_name=""):
        playlist = playlist if isinstance(playlist, dict) else {}
        provider = str(playlist.get("provider") or MASS_MIXES_PLAYLIST_PROVIDER).strip() or MASS_MIXES_PLAYLIST_PROVIDER
        item_id = str(playlist.get("item_id") or "").strip()
        folder = self._make_folder_node(
            id_=str(root_id or item_id),
            name=str(root_name or playlist.get("name") or "Unknown Playlist"),
            image=self._get_img(playlist, base),
            url=str(playlist.get("uri") or self._playlist_uri(item_id, provider)),
        )
        self._apply_media_identity(
            folder,
            media_type="playlist",
            item_id=item_id,
            provider=provider,
            provider_item_id=playlist.get("provider_item_id") or item_id,
        )
        folder["tracks"] = [
            self._apply_media_identity(
                self._make_leaf_node(
                    id_=track.get("item_id", ""),
                    name=track.get("name", "Unknown Track"),
                    artist=self._get_artist_name(track, ""),
                    album=self._get_album_name(track, ""),
                    url=track.get("uri", ""),
                    image=self._get_img(track, base),
                ),
                media_type="track",
                item_id=track.get("item_id", ""),
                provider=track.get("provider", "library"),
                provider_item_id=track.get("provider_item_id") or track.get("uri") or track.get("item_id"),
            )
            for track in (tracks or [])
        ]
        return folder

    def _build_podcast_folder_node(self, podcast, episodes, base):
        podcast = podcast if isinstance(podcast, dict) else {}
        podcast_name = str(podcast.get("name") or "Unknown Podcast")
        podcast_image = self._get_img(podcast, base)
        publisher = str(
            podcast.get("publisher")
            or podcast.get("author")
            or podcast.get("owner")
            or ""
        ).strip()
        folder = self._make_folder_node(
            id_=podcast.get("item_id", ""),
            name=podcast_name,
            artist=publisher,
            image=podcast_image,
            url=podcast.get("uri", ""),
        )
        self._apply_media_identity(
            folder,
            media_type="podcast",
            item_id=podcast.get("item_id", ""),
            provider=podcast.get("provider", "library"),
            provider_item_id=podcast.get("provider_item_id") or podcast.get("uri") or podcast.get("item_id"),
        )
        sorted_episodes = self._sort_podcast_episodes(episodes)
        folder["tracks"] = [
            self._apply_media_identity(
                self._make_leaf_node(
                    id_=episode.get("item_id", ""),
                    name=episode.get("name", "Unknown Episode"),
                    artist=self._get_artist_name(episode, podcast_name) or podcast_name,
                    url=episode.get("uri", ""),
                    image=self._get_img(episode, base) or podcast_image,
                ),
                media_type="podcast_episode",
                item_id=episode.get("item_id", ""),
                provider=episode.get("provider", podcast.get("provider", "library")),
                provider_item_id=episode.get("provider_item_id") or episode.get("uri") or episode.get("item_id"),
            )
            for episode in sorted_episodes
        ]
        latest_episode_dt = self._extract_item_datetime(sorted_episodes[0]) if sorted_episodes else None
        latest_episode_ts = latest_episode_dt.timestamp() if latest_episode_dt else float("-inf")
        return folder, latest_episode_ts

    async def _fetch_child_item_lists(
        self,
        *,
        items,
        command,
        media_label,
        provider_default="library",
        concurrency=4,
    ):
        keyed_items = {}
        for item in items or []:
            lookup_key = self._item_provider_lookup_key(item, provider_default)
            if lookup_key and lookup_key not in keyed_items:
                keyed_items[lookup_key] = item

        if not keyed_items:
            return {}

        semaphore = asyncio.Semaphore(concurrency)
        child_lists = {}

        async def fetch_one(lookup_key, item):
            item_id = str(item.get("item_id") or "").strip()
            provider = str(item.get("provider") or provider_default).strip() or provider_default
            try:
                async with semaphore:
                    child_lists[lookup_key] = await self.fetch_list(
                        command,
                        item_id=item_id,
                        provider_instance_id_or_domain=provider,
                    )
            except Exception as e:
                logger.error(
                    "Error fetching MASS %s children for %s/%s via %s: %s",
                    media_label,
                    provider,
                    item_id,
                    command,
                    e,
                )
                child_lists[lookup_key] = []

        await asyncio.gather(
            *(fetch_one(lookup_key, item) for lookup_key, item in keyed_items.items())
        )
        logger.info(
            "Fetched MASS %s child payloads: parents=%d child_lists=%d",
            media_label,
            len(items or []),
            len(child_lists),
        )
        return child_lists

    async def _fetch_bulk_music_library_items(self):
        artists = await self.fetch_paginated("music/artists/library_items")
        albums = await self.fetch_paginated("music/albums/library_items")
        songs = await self.fetch_paginated("music/tracks/library_items")
        logger.info(
            "Fetched MASS bulk library payloads: artists=%d albums=%d tracks=%d",
            len(artists),
            len(albums),
            len(songs),
        )
        return artists, albums, songs

    async def _fetch_bulk_playlist_library_items(self):
        playlists = await self.fetch_paginated("music/playlists/library_items")
        logger.info("Fetched MASS bulk playlist payloads: playlists=%d", len(playlists))
        return playlists

    async def _fetch_bulk_playlist_track_lists(self, playlists):
        return await self._fetch_child_item_lists(
            items=playlists,
            command="music/playlists/playlist_tracks",
            media_label="playlist",
            provider_default="library",
            concurrency=PLAYLIST_CHILD_FETCH_CONCURRENCY,
        )

    async def _fetch_bulk_podcast_library_items(self):
        podcasts = await self.fetch_paginated("music/podcasts/library_items")
        logger.info("Fetched MASS bulk podcast payloads: podcasts=%d", len(podcasts))
        return podcasts

    async def _fetch_bulk_podcast_episode_lists(self, podcasts):
        return await self._fetch_child_item_lists(
            items=podcasts,
            command="music/podcasts/podcast_episodes",
            media_label="podcast",
            provider_default="library",
            concurrency=PODCAST_CHILD_FETCH_CONCURRENCY,
        )

    def _build_bulk_music_roots(self, *, artists, albums, songs, base):
        tracks_by_album_id = {}
        for track in songs or []:
            album_id = self._track_album_item_id(track)
            if album_id:
                tracks_by_album_id.setdefault(album_id, []).append(track)
        for album_id in list(tracks_by_album_id.keys()):
            tracks_by_album_id[album_id].sort(key=self._album_track_sort_key)

        def build_track_leaf(track, *, artist_name="", album_name="", collapse_album_artist=False):
            resolved_artist = (
                self._get_track_artist_for_album(artist_name, track)
                if collapse_album_artist
                else self._get_artist_name(track, artist_name)
            )
            return self._apply_media_identity(
                self._make_leaf_node(
                    id_=track.get("item_id", ""),
                    name=track.get("name", "Unknown Track"),
                    artist=resolved_artist,
                    album=self._get_album_name(track, album_name),
                    url=track.get("uri", ""),
                ),
                media_type="track",
                item_id=track.get("item_id", ""),
                provider=track.get("provider", "library"),
                provider_item_id=track.get("provider_item_id") or track.get("uri") or track.get("item_id"),
            )

        songs_nodes = [build_track_leaf(track) for track in (songs or [])]

        albums_nodes = []
        albums_by_artist_id = {}
        albums_by_artist_name = {}
        for album in albums or []:
            album_id = str(album.get("item_id") or "").strip()
            album_name = str(album.get("name") or "Unknown Album").strip() or "Unknown Album"
            album_artist = self._get_artist_name(album, "Various")
            album_tracks = tracks_by_album_id.get(album_id) or []
            if not album_tracks:
                continue

            album_node = self._make_folder_node(
                id_=album.get("item_id", ""),
                name=album_name,
                artist=album_artist,
                image=self._get_img(album, base),
                url=album.get("uri", ""),
            )
            self._apply_media_identity(
                album_node,
                media_type="album",
                item_id=album.get("item_id", ""),
                provider=album.get("provider", "library"),
                provider_item_id=album.get("provider_item_id") or album.get("uri") or album.get("item_id"),
            )
            album_node["tracks"] = [
                build_track_leaf(
                    track,
                    artist_name=album_artist,
                    album_name=album_name,
                    collapse_album_artist=True,
                )
                for track in album_tracks
            ]
            if album_node["tracks"]:
                albums_nodes.append(album_node)

            for artist_ref in self._item_artist_refs(album):
                artist_ref_id = str(artist_ref.get("item_id") or "").strip()
                artist_ref_name = str(artist_ref.get("name") or "").strip()
                if artist_ref_id:
                    albums_by_artist_id.setdefault(artist_ref_id, []).append(album)
                if artist_ref_name:
                    albums_by_artist_name.setdefault(
                        self._normalize_lookup_text(artist_ref_name),
                        []
                    ).append(album)

        artist_nodes = []
        for artist in artists or []:
            artist_id = str(artist.get("item_id") or "").strip()
            artist_name = str(artist.get("name") or "Unknown Artist").strip() or "Unknown Artist"
            grouped_albums = list(albums_by_artist_id.get(artist_id) or [])
            if not grouped_albums:
                grouped_albums = list(
                    albums_by_artist_name.get(self._normalize_lookup_text(artist_name)) or []
                )
            if not grouped_albums:
                continue

            artist_node = self._make_folder_node(
                id_=artist.get("item_id", ""),
                name=artist_name,
                image=self._get_img(artist, base),
                url=artist.get("uri", ""),
            )
            self._apply_media_identity(
                artist_node,
                media_type="artist",
                item_id=artist.get("item_id", ""),
                provider=artist.get("provider", "library"),
                provider_item_id=artist.get("provider_item_id") or artist.get("uri") or artist.get("item_id"),
            )

            seen_album_ids = set()
            for album in grouped_albums:
                album_id = str(album.get("item_id") or "").strip()
                if not album_id or album_id in seen_album_ids:
                    continue
                seen_album_ids.add(album_id)
                album_name = str(album.get("name") or "Unknown Album").strip() or "Unknown Album"
                album_tracks = tracks_by_album_id.get(album_id) or []
                if not album_tracks:
                    continue

                album_node = self._make_folder_node(
                    id_=album.get("item_id", ""),
                    name=album_name,
                    artist=artist_name,
                    image=self._get_img(album, base),
                    url=album.get("uri", ""),
                )
                self._apply_media_identity(
                    album_node,
                    media_type="album",
                    item_id=album.get("item_id", ""),
                    provider=album.get("provider", "library"),
                    provider_item_id=album.get("provider_item_id") or album.get("uri") or album.get("item_id"),
                )
                album_node["tracks"] = [
                    build_track_leaf(
                        track,
                        artist_name=artist_name,
                        album_name=album_name,
                    )
                    for track in album_tracks
                ]
                if album_node["tracks"]:
                    artist_node["tracks"].append(album_node)

            if artist_node["tracks"]:
                artist_nodes.append(artist_node)

        return artist_nodes, albums_nodes, songs_nodes

    def _build_bulk_playlist_roots(self, *, playlists, tracks_by_playlist_key, base):
        playlist_nodes = []
        mixes_playlist_root = None

        for playlist in playlists or []:
            lookup_key = self._item_provider_lookup_key(
                playlist,
                MASS_MIXES_PLAYLIST_PROVIDER,
            )
            tracks = tracks_by_playlist_key.get(lookup_key) or []
            playlist_node = self._build_playlist_folder_node(playlist, tracks, base)
            if not playlist_node["tracks"]:
                continue
            playlist_nodes.append(playlist_node)
            if self._is_mixes_playlist(playlist):
                mixes_playlist_root = self._build_playlist_folder_node(
                    playlist,
                    tracks,
                    base,
                    root_id=MASS_MIXES_PLAYLIST_ROOT_ID,
                    root_name=MASS_MIXES_PLAYLIST_TITLE,
                )

        return playlist_nodes, mixes_playlist_root

    def _build_bulk_podcast_roots(self, *, podcasts, episodes_by_podcast_key, base):
        podcast_entries = []
        for podcast in podcasts or []:
            lookup_key = self._item_provider_lookup_key(podcast, "library")
            episodes = episodes_by_podcast_key.get(lookup_key) or []
            podcast_node, latest_episode_ts = self._build_podcast_folder_node(
                podcast,
                episodes,
                base,
            )
            if podcast_node["tracks"]:
                podcast_entries.append((latest_episode_ts, podcast_node))

        podcast_entries.sort(key=lambda entry: str(entry[1].get("name") or "").lower())
        podcast_entries.sort(key=lambda entry: entry[0], reverse=True)
        return [podcast_node for _, podcast_node in podcast_entries]

    def _finalize_node(self, node):
        """
        Recursive post-processing:
          - Renames `url` → `play_url` for folder nodes (keeps URI for playback
            while removing the `url` key that ArcList uses to detect playability).
          - Strips empty/fallback images to avoid sending noise to the frontend.
          - Bubbles a representative image up to category header nodes.
          - Re-encodes any image proxy URL whose path= value is un-encoded.
        Returns the node's resolved image for parent bubble-up.
        """
        last_child_image = ""
        own_image        = node.get("image", "")

        if "tracks" in node:
            # FOLDER: rename url → play_url, recurse into children
            if "url" in node:
                node["play_url"] = node.pop("url")

            for child in node["tracks"]:
                img = self._finalize_node(child)
                if img:
                    last_child_image = img

            # Bubble up a representative image to folder nodes that lack one
            if not node.get("image") or node["image"] == FALLBACK_IMAGE:
                if last_child_image:
                    node["image"] = last_child_image
                else:
                    node.pop("image", None)
        else:
            # LEAF: strip fallback/empty images
            if not node.get("image") or node["image"] == FALLBACK_IMAGE:
                node.pop("image", None)

        # Fix un-encoded image proxy URLs (path= value must be percent-encoded)
        img = node.get("image", "")
        if "path=" in img and "%" not in img:
            parts       = img.split("path=", 1)
            node["image"] = f"{parts[0]}path={urllib.parse.quote(parts[1], safe='')}"

        return node.get("image") or last_child_image or own_image

    # ── Library sync ──────────────────────────────────────────────────────────

    async def update_library_cache(self):
        if self._is_syncing:
            return
        self._is_syncing = True
        logger.info("--- Starting Deep Library Sync ---")
        base = MASS_URI.replace("ws://", "http://").replace("/ws", "")

        artists_root = {"id": "artists",   "name": "Artists",   "tracks": []}
        albums_root = {"id": "albums",    "name": "Albums",     "tracks": []}
        songs_root = {"id": "songs",     "name": "Songs",      "tracks": []}
        playlists_root = {"id": "playlists", "name": "Playlists",  "tracks": []}
        podcasts_root = {"id": MASS_PODCASTS_ROOT_ID, "name": MASS_PODCASTS_ROOT_TITLE, "tracks": []}
        radio_root = {"id": MASS_RADIO_ROOT_ID, "name": MASS_RADIO_ROOT_TITLE, "tracks": []}
        mixes_playlist_root = None

        try:
            # ── 1. ARTISTS: Artists → Artist → Album → Track ─────────────────
            artists, albums, songs = await self._fetch_bulk_music_library_items()
            (
                artists_root["tracks"],
                albums_root["tracks"],
                songs_root["tracks"],
            ) = self._build_bulk_music_roots(
                artists=artists,
                albums=albums,
                songs=songs,
                base=base,
            )
            artists = []
            for a in artists:
                try:
                    a_node = self._make_folder_node(
                        id_=a.get('item_id', ''),
                        name=a.get('name', 'Unknown Artist'),
                        image=self._get_img(a, base),
                        url=a.get('uri', ''),
                    )
                    self._apply_media_identity(
                        a_node,
                        media_type="artist",
                        item_id=a.get("item_id", ""),
                        provider=a.get("provider", "library"),
                        provider_item_id=a.get("provider_item_id") or a.get("uri") or a.get("item_id"),
                    )
                    albs = await self.fetch_list(
                        "music/artists/artist_albums",
                        item_id=a.get('item_id'),
                        provider_instance_id_or_domain=a.get("provider", "library"),
                    )
                    for alb in albs:
                        alb_node = self._make_folder_node(
                            id_=alb.get('item_id', ''),
                            name=alb.get('name', 'Unknown Album'),
                            artist=a.get('name', ''),
                            image=self._get_img(alb, base),
                            url=alb.get('uri', ''),
                        )
                        self._apply_media_identity(
                            alb_node,
                            media_type="album",
                            item_id=alb.get("item_id", ""),
                            provider=alb.get("provider", "library"),
                            provider_item_id=alb.get("provider_item_id") or alb.get("uri") or alb.get("item_id"),
                        )
                        trks = await self.fetch_list(
                            "music/albums/album_tracks",
                            item_id=alb.get('item_id'),
                            provider_instance_id_or_domain=alb.get("provider", "library"),
                        )
                        alb_node["tracks"] = [
                            self._apply_media_identity(
                                self._make_leaf_node(
                                    id_=t.get('item_id', ''),
                                    name=t.get('name', 'Unknown Track'),
                                    artist=self._get_artist_name(t, a.get('name', '')),
                                    album=self._get_album_name(t, alb.get('name', '')),
                                    url=t.get('uri', ''),
                                ),
                                media_type="track",
                                item_id=t.get("item_id", ""),
                                provider=t.get("provider", "library"),
                                provider_item_id=t.get("provider_item_id") or t.get("uri") or t.get("item_id"),
                            )
                            for t in trks
                        ]
                        if alb_node["tracks"]:
                            a_node["tracks"].append(alb_node)
                    if a_node["tracks"]:
                        artists_root["tracks"].append(a_node)
                except Exception as e:
                    logger.error(f"Error parsing artist {a.get('name')}: {e}")

            # ── 2. ALBUMS: Albums → Album → Track ────────────────────────────
            albums = []
            for alb in albums:
                try:
                    a_name = self._get_artist_name(alb, "Various")
                    trks   = await self.fetch_list(
                        "music/albums/album_tracks",
                        item_id=alb.get('item_id'),
                        provider_instance_id_or_domain=alb.get("provider", "library"),
                    )
                    alb_node = self._make_folder_node(
                        id_=alb.get('item_id', ''),
                        name=alb.get('name', 'Unknown Album'),
                        artist=a_name,
                        image=self._get_img(alb, base),
                        url=alb.get('uri', ''),
                    )
                    self._apply_media_identity(
                        alb_node,
                        media_type="album",
                        item_id=alb.get("item_id", ""),
                        provider=alb.get("provider", "library"),
                        provider_item_id=alb.get("provider_item_id") or alb.get("uri") or alb.get("item_id"),
                    )
                    alb_node["tracks"] = [
                        self._apply_media_identity(
                            self._make_leaf_node(
                                id_=t.get('item_id', ''),
                                name=t.get('name', 'Unknown Track'),
                                artist=self._get_track_artist_for_album(a_name, t),
                                album=self._get_album_name(t, alb.get('name', '')),
                                url=t.get('uri', ''),
                            ),
                            media_type="track",
                            item_id=t.get("item_id", ""),
                            provider=t.get("provider", "library"),
                            provider_item_id=t.get("provider_item_id") or t.get("uri") or t.get("item_id"),
                        )
                        for t in trks
                    ]
                    if alb_node["tracks"]:
                        albums_root["tracks"].append(alb_node)
                except Exception as e:
                    logger.error(f"Error parsing album {alb.get('name')}: {e}")

            # ── 3. SONGS: flat track list ────────────────────────────────────
            try:
                songs = []
                songs_root["tracks"] = songs_root["tracks"] or [
                    self._apply_media_identity(
                        self._make_leaf_node(
                            id_=s.get('item_id', ''),
                            name=s.get('name', 'Unknown Track'),
                            artist=self._get_artist_name(s, ""),
                            album=self._get_album_name(s, ""),
                            url=s.get('uri', ''),
                        ),
                        media_type="track",
                        item_id=s.get("item_id", ""),
                        provider=s.get("provider", "library"),
                        provider_item_id=s.get("provider_item_id") or s.get("uri") or s.get("item_id"),
                    )
                    for s in songs
                ]
            except Exception as e:
                logger.error(f"Error parsing songs: {e}")

            # ── 4. PLAYLISTS: Playlists → Playlist → Track ───────────────────
            try:
                playlists = await self._fetch_bulk_playlist_library_items()
                playlist_tracks_by_key = await self._fetch_bulk_playlist_track_lists(playlists)
                (
                    playlists_root["tracks"],
                    mixes_playlist_root,
                ) = self._build_bulk_playlist_roots(
                    playlists=playlists,
                    tracks_by_playlist_key=playlist_tracks_by_key,
                    base=base,
                )
            except Exception as e:
                logger.error(f"Error parsing playlists: {e}")

            # ── 5. MIXES / RADIO: flat list ──────────────────────────────────
            if not mixes_playlist_root:
                try:
                    mixes_tracks = await self.fetch_list(
                        "music/playlists/playlist_tracks",
                        item_id=MASS_MIXES_PLAYLIST_ID,
                        provider_instance_id_or_domain=MASS_MIXES_PLAYLIST_PROVIDER,
                    )
                    if mixes_tracks:
                        mixes_playlist_root = self._build_playlist_folder_node(
                            {
                                "item_id": MASS_MIXES_PLAYLIST_ID,
                                "provider": MASS_MIXES_PLAYLIST_PROVIDER,
                                "name": MASS_MIXES_PLAYLIST_TITLE,
                                "uri": self._playlist_uri(
                                    MASS_MIXES_PLAYLIST_ID,
                                    MASS_MIXES_PLAYLIST_PROVIDER,
                                ),
                            },
                            mixes_tracks,
                            base,
                            root_id=MASS_MIXES_PLAYLIST_ROOT_ID,
                            root_name=MASS_MIXES_PLAYLIST_TITLE,
                        )
                except Exception as e:
                    logger.error(f"Error building mixes playlist root: {e}")

            # Podcasts -> Podcast -> Episode
            try:
                podcasts = await self._fetch_bulk_podcast_library_items()
                podcast_episodes_by_key = await self._fetch_bulk_podcast_episode_lists(podcasts)
                podcasts_root["tracks"] = self._build_bulk_podcast_roots(
                    podcasts=podcasts,
                    episodes_by_podcast_key=podcast_episodes_by_key,
                    base=base,
                )
            except Exception as e:
                logger.error(f"Error parsing podcasts: {e}")

            # Radio stations
            try:
                radios = await self.fetch_paginated("music/radios/library_items")
                for r in radios:
                    radio_root["tracks"].append(
                        self._apply_media_identity(
                            self._make_leaf_node(
                                id_=r.get('item_id', ''),
                                name=r.get('name', 'Unknown Radio'),
                                url=r.get('uri', ''),
                                image=self._get_img(r, base),
                            ),
                            media_type="radio",
                            item_id=r.get("item_id", ""),
                            provider=r.get("provider", "library"),
                            provider_item_id=r.get("provider_item_id") or r.get("uri") or r.get("item_id"),
                        )
                    )
            except Exception as e:
                logger.error(f"Error parsing mixes: {e}")

            # Finalize + save before exposing to endpoint
            tree = [artists_root, albums_root, songs_root, playlists_root]
            if mixes_playlist_root:
                tree.append(mixes_playlist_root)
            tree.extend([podcasts_root, radio_root])
            self._normalize_library_tree(tree)
            await self._incremental_save(tree)
            self._library_data = tree
            self._rebuild_library_indexes()
            logger.info("Hierarchy Sync Complete.")

        except Exception as e:
            logger.error(f"Sync failed: {e}")
        finally:
            self._is_syncing = False

    async def _incremental_save(self, data):
        try:
            for root_category in data:
                self._finalize_node(root_category)
            await self._localize_tree_images(data)
            self._write_json_file(CACHE_FILE, data)
            # Mirror a static copy into web/json so the frontend can cold-boot
            # instantly even if the service itself is still starting.
            self._write_json_file(LEGACY_CACHE_FILE, data)
            logger.info(f"Library saved to {CACHE_FILE}")
        except Exception as e:
            logger.error(f"Save failed: {e}")

    # ── HTTP routes ───────────────────────────────────────────────────────────

    def add_routes(self, app):
        async def _handle_playlists(request):
            if self._is_syncing and not self._library_data:
                return web.json_response({"loading": True}, headers=self._cors_headers())
            return web.json_response(self._library_data, headers=self._cors_headers())

        async def _handle_artist_bio(request):
            for queue_id in await self._resolve_queue_candidates():
                artist_info = await self._build_current_artist_info(queue_id)
                if artist_info:
                    return web.json_response(artist_info, headers=self._cors_headers())
            return web.json_response(
                {"state": "error", "error": "artist_bio_unavailable"},
                headers=self._cors_headers(),
            )

        async def _handle_now_playing(request):
            for queue_id in await self._resolve_queue_candidates():
                payload = await self._build_now_playing_payload(queue_id)
                if payload:
                    return web.json_response(payload, headers=self._cors_headers())
            return web.json_response(
                {"state": "empty"},
                headers=self._cors_headers(),
            )

        async def _handle_item_info(request):
            uri = str(request.query.get("uri") or "").strip()
            payload = await self._build_item_info_payload(uri)
            status = 200 if payload.get("state") != "error" else 404
            return web.json_response(payload, status=status, headers=self._cors_headers())

        async def _handle_art(request):
            filename = os.path.basename(request.match_info.get("filename", ""))
            art_path = os.path.join(ART_CACHE_DIR, filename)
            if not filename or not os.path.exists(art_path):
                raise web.HTTPNotFound()
            response = web.FileResponse(art_path)
            response.headers.update(self._cors_headers())
            return response

        app.router.add_get('/playlists', _handle_playlists)
        app.router.add_get('/artist_bio', _handle_artist_bio)
        app.router.add_get('/now_playing', _handle_now_playing)
        app.router.add_get('/item_info', _handle_item_info)
        app.router.add_get('/art/{filename}', _handle_art)

    def _library_root(self, root_id):
        for node in self._library_data or []:
            if isinstance(node, dict) and str(node.get("id") or "").strip() == str(root_id):
                return node
        return {}

    def _build_library_status(self):
        return {
            "artists": len(self._library_root("artists").get("tracks") or []),
            "albums": len(self._library_root("albums").get("tracks") or []),
            "songs": len(self._library_root("songs").get("tracks") or []),
            "playlists": len(self._library_root("playlists").get("tracks") or []),
            "mixes": len(self._library_root(MASS_MIXES_PLAYLIST_ROOT_ID).get("tracks") or []),
            "podcasts": len(self._library_root(MASS_PODCASTS_ROOT_ID).get("tracks") or []),
            "radio": len(self._library_root(MASS_RADIO_ROOT_ID).get("tracks") or []),
        }

    @staticmethod
    def _configured_transfer_targets():
        return [
            {"id": target["id"], "name": target.get("name") or target["id"]}
            for target in get_audio_targets()
        ]

    async def _build_queue_status(self):
        for queue_id in await self._resolve_queue_candidates():
            snapshot = await self._get_queue_snapshot(queue_id)
            if not isinstance(snapshot, dict):
                continue
            current_item = self._extract_current_queue_item(snapshot)
            return {
                "queue_id": str(snapshot.get("resolved_queue_id") or queue_id).strip(),
                "state": self._extract_playback_state(snapshot) or "idle",
                "items": max(self._extract_queue_size(snapshot), len(self._extract_queue_items(snapshot))),
                "current_title": self._extract_queue_name(current_item, ""),
                "current_artist": self._extract_queue_artist(current_item),
                "current_album": self._extract_queue_album(current_item),
            }
        return {
            "queue_id": "",
            "state": "idle",
            "items": 0,
            "current_title": "",
            "current_artist": "",
            "current_album": "",
        }

    async def handle_status(self):
        status = await super().handle_status()
        status.update(
            {
                "connected": self._connected,
                "syncing": self._is_syncing,
                "has_cache": bool(self._library_data),
                "cache_file": CACHE_FILE,
                "art_cache_dir": ART_CACHE_DIR,
                "player_id": str(TARGET_PLAYER_ID or "").strip(),
                "queue_id": str(TARGET_QUEUE_ID or "").strip(),
                "playback_mode": {
                    "configured": self._configured_playback_mode(),
                    "prefers_local": self._prefers_local_playback(),
                    "local_backend_ready": self._local_player_ready(),
                    "runtime_path": self._runtime_playback_path(),
                    "local_queue_active": self._local_queue_active,
                },
                "library": self._build_library_status(),
                "queue": await self._build_queue_status(),
                "transfer_targets": self._configured_transfer_targets(),
            }
        )
        return status

    def _base_http_url(self):
        return MASS_URI.replace("/ws", "").replace("ws://", "http://").replace("wss://", "https://")

    @staticmethod
    def _is_local_queue_item_id(value):
        return str(value or "").strip().startswith(LOCAL_QUEUE_ITEM_PREFIX)

    @staticmethod
    def _parse_local_queue_index(value):
        text = str(value or "").strip()
        if not text.startswith(LOCAL_QUEUE_ITEM_PREFIX):
            return -1
        try:
            return int(text[len(LOCAL_QUEUE_ITEM_PREFIX):])
        except ValueError:
            return -1

    def _reset_local_queue(self):
        task = self._local_queue_monitor_task
        if task and not task.done():
            task.cancel()
        self._local_queue_entries = []
        self._local_queue_index = -1
        self._local_queue_active = False
        self._local_queue_monitor_task = None
        self._local_queue_transition_deadline = 0.0
        self._local_queue_last_player_state = "stopped"

    def _find_node_by_uri(self, uri):
        target = str(uri or "").strip()
        if not target:
            return None
        indexed = self._library_node_by_uri.get(target)
        if isinstance(indexed, dict):
            return indexed
        self._rebuild_library_indexes()
        return self._library_node_by_uri.get(target)

    def _find_command_node(self, data, uri=""):
        node = self._find_node_by_id(self._command_node_id(data))
        if isinstance(node, dict):
            return node
        return self._find_node_by_uri(uri)

    def _build_fallback_local_entry(self, data, uri):
        payload = self._build_command_media_payload(data, uri, state="playing") or {}
        return {
            "id": self._command_node_id(data) or uri,
            "source_uri": str(uri or "").strip(),
            "title": str(payload.get("title") or "Music Assistant").strip(),
            "artist": str(payload.get("artist") or "").strip(),
            "album": str(payload.get("album") or "").strip(),
            "artwork": str(payload.get("artwork") or "").strip(),
            "radio": self._uri_looks_like_radio(uri),
        }

    @staticmethod
    def _uri_looks_like_radio(uri):
        uri_text = str(uri or "").strip().lower()
        return any(token in uri_text for token in ("radio://", "/radio/", "radios/"))

    @staticmethod
    def _item_looks_like_radio(item, fallback_uri=""):
        if MassSource._uri_looks_like_radio(fallback_uri):
            return True
        if not isinstance(item, dict):
            return False
        media_type = str(item.get("media_type") or item.get("type") or "").strip().lower()
        return "radio" in media_type

    def _collect_local_entries(self, node, *, album="", artwork="", root_id=""):
        if not isinstance(node, dict):
            return []
        node_id = str(node.get("id") or "").strip()
        node_name = str(node.get("name") or "").strip()
        node_artwork = str(node.get("image") or artwork or "").strip()
        children = node.get("tracks")
        effective_root = root_id or node_id
        if isinstance(children, list) and children:
            next_album = album
            contains_leaves = any(
                isinstance(child, dict) and not (isinstance(child.get("tracks"), list) and child.get("tracks"))
                for child in children
            )
            if node_id not in ROOT_CATEGORY_IDS and contains_leaves:
                next_album = node_name or album
            entries = []
            for child in children:
                entries.extend(
                    self._collect_local_entries(
                        child,
                        album=next_album,
                        artwork=node_artwork,
                        root_id=effective_root,
                    )
                )
            return entries

        uri = str(node.get("url") or node.get("play_url") or "").strip()
        if not uri:
            return []
        return [{
            "id": str(node.get("id") or uri).strip(),
            "source_uri": uri,
            "title": node_name or "Music Assistant",
            "artist": str(node.get("artist") or "").strip(),
            "album": str(node.get("album") or album or "").strip(),
            "artwork": node_artwork,
            "radio": effective_root == MASS_RADIO_ROOT_ID or self._uri_looks_like_radio(uri),
        }]

    def _build_local_entries_for_request(self, data, uri):
        node = self._find_command_node(data, uri)
        if isinstance(node, dict):
            entries = self._collect_local_entries(node, root_id=str(node.get("id") or "").strip())
            if entries:
                return entries
        return [self._build_fallback_local_entry(data, uri)]

    @staticmethod
    def _normalize_local_stream_url(value):
        text = str(value or "").strip()
        if not text:
            return ""
        lower = text.lower()
        if lower.startswith(("http://", "https://", "file://")):
            return text
        if os.path.isabs(text) and os.path.exists(text):
            return text
        return ""

    @staticmethod
    def _load_embedded_json(value):
        if not isinstance(value, str):
            return None
        text = value.strip()
        if not text or text[0] not in "{[":
            return None
        try:
            return json.loads(text)
        except Exception:
            return None

    @staticmethod
    def _hint_looks_like_artwork(value):
        hint = str(value or "").strip().lower()
        if not hint:
            return False
        return any(token in hint for token in LOCAL_ARTWORK_HINTS)

    @staticmethod
    def _url_looks_like_image(value):
        text = str(value or "").strip()
        if not text:
            return False
        parsed = urllib.parse.urlparse(text)
        path = str(parsed.path or "").strip().lower()
        return any(path.endswith(suffix) for suffix in LOCAL_IMAGE_SUFFIXES)

    @staticmethod
    def _mapping_looks_streamish(mapping):
        if not isinstance(mapping, dict):
            return False
        keys = {str(key or "").strip().lower() for key in mapping.keys()}
        return bool(
            keys.intersection(LOCAL_STREAM_HINT_KEYS)
            or keys.intersection(LOCAL_STREAMISH_MARKERS)
        )

    def _extract_local_stream_candidates(self, item):
        candidates = []

        def add(value, *, hint=""):
            if self._hint_looks_like_artwork(hint):
                return
            normalized = self._normalize_local_stream_url(value)
            if normalized and not self._url_looks_like_image(normalized) and normalized not in candidates:
                candidates.append(normalized)

        def scan_value(value, *, hint="", allow_generic_url=False):
            if self._hint_looks_like_artwork(hint):
                return
            parsed = self._load_embedded_json(value)
            if parsed is not None:
                scan_value(parsed, hint=hint, allow_generic_url=allow_generic_url)
                return
            if isinstance(value, list):
                for entry in value:
                    scan_value(entry, hint=hint, allow_generic_url=allow_generic_url)
                return
            if not isinstance(value, dict):
                return

            streamish = allow_generic_url or self._mapping_looks_streamish(value)
            if streamish:
                add(value.get("url"), hint="url")
            for key in LOCAL_STREAM_HINT_KEYS:
                add(value.get(key), hint=key)
            add(value.get("external_url"), hint="external_url")

            for key, nested in value.items():
                next_hint = str(key or "").strip().lower()
                next_allow = streamish or next_hint in LOCAL_STREAM_CONTAINER_HINTS
                scan_value(nested, hint=next_hint, allow_generic_url=next_allow)

        if not isinstance(item, dict):
            return candidates

        for key in ("stream_url", "url", "path", "content_url", "audio_url"):
            add(item.get(key), hint=key)

        metadata = item.get("metadata")
        if isinstance(metadata, dict):
            for key in ("stream_url", "url", "path", "content_url", "audio_url", "external_url"):
                add(metadata.get(key), hint=key)
            scan_value(metadata, hint="metadata", allow_generic_url=False)

        provider_mappings = item.get("provider_mappings")
        if isinstance(provider_mappings, list):
            for mapping in provider_mappings:
                if not isinstance(mapping, dict):
                    continue
                for key in ("stream_url", "url", "path", "content_url", "audio_url"):
                    add(mapping.get(key), hint=key)
                details = mapping.get("details")
                if isinstance(details, dict):
                    for key in ("stream_url", "url", "path", "content_url", "audio_url"):
                        add(details.get(key), hint=key)
                elif isinstance(details, str):
                    parsed = self._load_embedded_json(details)
                    if parsed is not None:
                        scan_value(parsed, hint="details", allow_generic_url=True)
                scan_value(mapping, hint="provider_mappings", allow_generic_url=True)

        media_item = item.get("media_item")
        if isinstance(media_item, dict):
            for key in ("stream_url", "url", "path", "content_url", "audio_url"):
                add(media_item.get(key), hint=key)
            scan_value(media_item, hint="media_item", allow_generic_url=True)

        scan_value(item, hint="item", allow_generic_url=False)

        return candidates

    def _extract_item_title(self, item, fallback="Music Assistant"):
        if not isinstance(item, dict):
            return fallback
        for key in ("name", "sort_name"):
            value = item.get(key)
            if value:
                return str(value)
        media_item = item.get("media_item")
        if isinstance(media_item, dict):
            for key in ("name", "sort_name"):
                value = media_item.get(key)
                if value:
                    return str(value)
        return fallback

    def _extract_item_artist(self, item, fallback=""):
        if not isinstance(item, dict):
            return fallback
        artist = item.get("artist_str")
        if artist:
            return str(artist)
        artist = self._get_artist_name(item, "")
        if artist:
            return str(artist)
        media_item = item.get("media_item")
        if isinstance(media_item, dict):
            artist = media_item.get("artist_str")
            if artist:
                return str(artist)
            artist = self._get_artist_name(media_item, "")
            if artist:
                return str(artist)
        return fallback

    def _extract_item_album(self, item, fallback=""):
        if not isinstance(item, dict):
            return fallback
        for key in ("album_name",):
            value = item.get(key)
            if value:
                return str(value)
        album = item.get("album")
        if isinstance(album, dict):
            name = album.get("name")
            if name:
                return str(name)
        media_item = item.get("media_item")
        if isinstance(media_item, dict):
            album = media_item.get("album")
            if isinstance(album, dict):
                name = album.get("name")
                if name:
                    return str(name)
            album_name = media_item.get("album_name")
            if album_name:
                return str(album_name)
        return fallback

    async def _extract_item_artwork(self, item, fallback=""):
        if not isinstance(item, dict):
            return fallback
        artwork = self._get_img(item, self._base_http_url()) or ""
        if not artwork:
            album = item.get("album")
            if isinstance(album, dict):
                artwork = self._get_img(album, self._base_http_url()) or ""
        if not artwork:
            media_item = item.get("media_item")
            if isinstance(media_item, dict):
                artwork = self._get_img(media_item, self._base_http_url()) or ""
        if artwork:
            artwork = await self._cache_image_locally(artwork)
        return artwork or fallback

    async def _resolve_local_entry(self, entry):
        if not isinstance(entry, dict):
            return None
        source_uri = str(entry.get("source_uri") or "").strip()
        if not source_uri:
            return None
        cached = self._local_stream_cache.get(source_uri)
        if isinstance(cached, dict) and cached.get("stream_url"):
            merged = dict(entry)
            merged.update(cached)
            return merged

        item = await self.send_command("music/item_by_uri", uri=source_uri)
        if not isinstance(item, dict):
            return None

        candidates = self._extract_local_stream_candidates(item)
        if not candidates:
            return None

        resolved = dict(entry)
        resolved["stream_url"] = candidates[0]
        resolved["title"] = self._extract_item_title(item, resolved.get("title", "Music Assistant"))
        resolved["artist"] = self._extract_item_artist(item, resolved.get("artist", ""))
        resolved["album"] = self._extract_item_album(item, resolved.get("album", ""))
        resolved["artwork"] = await self._extract_item_artwork(item, resolved.get("artwork", ""))
        resolved["radio"] = bool(resolved.get("radio")) or self._item_looks_like_radio(item, source_uri)
        self._local_stream_cache[source_uri] = dict(resolved)
        return resolved

    async def _resolve_local_queue_entry(self, index):
        if index < 0 or index >= len(self._local_queue_entries):
            return None
        entry = self._local_queue_entries[index]
        if entry.get("stream_url"):
            return entry
        resolved = await self._resolve_local_entry(entry)
        if resolved:
            self._local_queue_entries[index] = resolved
        return resolved

    async def _publish_local_entry(self, entry, *, state="playing", reason="track_change"):
        register_state = "paused" if str(state or "").strip().lower() == "paused" else "playing"
        source_uri = str(entry.get("source_uri") or entry.get("uri") or "").strip()
        if register_state == "paused":
            await self.register("paused")
        else:
            await self.register("playing", auto_power=True)
        await self.post_media_update(
            title=str(entry.get("title") or "Music Assistant").strip(),
            artist=str(entry.get("artist") or "").strip(),
            album=str(entry.get("album") or "").strip(),
            artwork=str(entry.get("artwork") or "").strip(),
            state=register_state,
            reason=reason,
            track_uri=source_uri,
        )
        return {
            "state": register_state,
            "queue_id": "local",
            "player_id": "local",
            "title": str(entry.get("title") or "Music Assistant").strip(),
            "artist": str(entry.get("artist") or "").strip(),
            "album": str(entry.get("album") or "").strip(),
            "artwork": str(entry.get("artwork") or "").strip(),
            "uri": source_uri,
        }

    def _mark_local_queue_transition(self, seconds=LOCAL_QUEUE_TRANSITION_GRACE):
        try:
            now = asyncio.get_running_loop().time()
        except RuntimeError:
            return
        self._local_queue_transition_deadline = max(
            self._local_queue_transition_deadline,
            now + max(0.5, float(seconds or LOCAL_QUEUE_TRANSITION_GRACE)),
        )

    def _local_queue_transition_pending(self):
        try:
            now = asyncio.get_running_loop().time()
        except RuntimeError:
            return False
        return now < self._local_queue_transition_deadline

    def _ensure_local_queue_monitor(self):
        task = self._local_queue_monitor_task
        if task and not task.done():
            return
        self._local_queue_monitor_task = self._spawn(
            self._watch_local_queue_playback(),
            name="mass_local_queue",
        )

    async def _stop_local_queue_monitor(self):
        task = self._local_queue_monitor_task
        self._local_queue_monitor_task = None
        if not task or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.debug("Local MASS queue monitor shutdown raised: %s", exc)

    async def _advance_local_queue_after_track_end(self):
        payload = await self._play_local_queue_from(
            self._local_queue_index + 1,
            step=1,
            reason="track_change",
        )
        if payload:
            self._local_queue_last_player_state = "playing"
            return payload
        self._local_queue_active = False
        self._local_queue_last_player_state = "stopped"
        await self.register("available")
        return None

    async def _watch_local_queue_playback(self):
        try:
            self._local_queue_last_player_state = await self.player_state()
            while self._local_queue_active and self._local_queue_entries:
                state = str(await self.player_state() or "unknown").strip().lower()
                if state == "playing":
                    self._local_queue_last_player_state = "playing"
                elif state == "paused":
                    self._local_queue_last_player_state = "paused"
                elif state == "stopped":
                    if (
                        self._local_queue_last_player_state == "playing"
                        and not self._local_queue_transition_pending()
                    ):
                        advanced = await self._advance_local_queue_after_track_end()
                        if advanced:
                            await asyncio.sleep(LOCAL_QUEUE_POLL_INTERVAL)
                            continue
                        break
                    self._local_queue_last_player_state = "stopped"
                elif state and state != "unknown":
                    self._local_queue_last_player_state = state
                await asyncio.sleep(LOCAL_QUEUE_POLL_INTERVAL)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Local MASS queue monitor failed: %s", exc)
        finally:
            self._local_queue_monitor_task = None

    async def _play_local_queue_index(self, index, *, reason="track_change"):
        entry = await self._resolve_local_queue_entry(index)
        if not isinstance(entry, dict):
            return None
        self._mark_local_queue_transition()
        ok = await self.player_play(
            url=str(entry.get("stream_url") or "").strip(),
            radio=bool(entry.get("radio")),
        )
        if not ok:
            return None
        self._local_queue_entries[index] = entry
        self._local_queue_index = index
        self._local_queue_active = True
        self._local_queue_last_player_state = "playing"
        self._mark_local_queue_transition()
        self._ensure_local_queue_monitor()
        return await self._publish_local_entry(entry, state="playing", reason=reason)

    async def _play_local_queue_from(self, start_index, *, step=1, reason="track_change"):
        if not self._local_queue_entries:
            return None
        if step == 0:
            step = 1
        index = int(start_index)
        while 0 <= index < len(self._local_queue_entries):
            payload = await self._play_local_queue_index(index, reason=reason)
            if payload:
                return payload
            index += step
        return None

    async def _handle_local_play_selection(self, cmd, data, uri):
        local_queue_item_index = self._parse_local_queue_index(self._command_node_id(data))
        if local_queue_item_index >= 0 and cmd in {"play_now", "play_item"} and self._local_queue_entries:
            payload = await self._play_local_queue_index(local_queue_item_index, reason="queue_play")
            if payload:
                return {
                    "state": payload["state"],
                    "queue_id": "local",
                    "index": local_queue_item_index,
                    "uri": payload["uri"],
                    "player_id": "local",
                }
            return None

        if cmd == "play_radio":
            return {
                "state": "error",
                "reason": "radio_mode_unavailable_local",
                "uri": uri,
            }

        entries = self._build_local_entries_for_request(data, uri)
        if not entries:
            return None

        if cmd == "play_next":
            if self._local_queue_entries:
                insert_at = self._local_queue_index + 1 if self._local_queue_index >= 0 else 0
                for offset, entry in enumerate(entries):
                    self._local_queue_entries.insert(insert_at + offset, entry)
            else:
                self._local_queue_entries = list(entries)
                self._local_queue_index = -1
                self._local_queue_active = False
            return {
                "state": "queued",
                "queue_id": "local",
                "uri": uri,
                "queued": len(entries),
                "total": len(self._local_queue_entries),
            }

        if cmd == "queue_item":
            if self._local_queue_entries:
                self._local_queue_entries.extend(entries)
            else:
                self._local_queue_entries = list(entries)
                self._local_queue_index = -1
                self._local_queue_active = False
            return {
                "state": "queued",
                "queue_id": "local",
                "uri": uri,
                "queued": len(entries),
                "total": len(self._local_queue_entries),
            }

        if cmd != "play_now" and self._local_queue_active and self._local_queue_entries:
            self._local_queue_entries.extend(entries)
            return {
                "state": "queued",
                "queue_id": "local",
                "uri": uri,
                "queued": len(entries),
                "total": len(self._local_queue_entries),
            }

        self._local_queue_entries = entries
        self._local_queue_index = -1
        self._local_queue_active = True
        payload = await self._play_local_queue_from(0, reason="track_change")
        if payload:
            return {
                "state": payload["state"],
                "queue_id": "local",
                "uri": payload["uri"],
                "player_id": "local",
            }
        self._local_queue_active = False
        self._local_queue_last_player_state = "stopped"
        return None

    async def _handle_local_transport_command(self, cmd):
        if cmd == "transport_stop":
            self._mark_local_queue_transition()
            await self.player_stop()
            self._local_queue_active = False
            self._local_queue_last_player_state = "stopped"
            await self._stop_local_queue_monitor()
            await self.register("available")
            return {"state": "available", "player_id": "local", "command": cmd}

        if cmd == "transport_toggle":
            if self._registered_state == "playing":
                ok = await self.player_pause()
                if not ok:
                    return {"state": "error", "reason": "transport_command_failed", "command": cmd}
                self._local_queue_last_player_state = "paused"
                payload = await self._build_local_now_playing_payload()
                if payload:
                    await self._publish_local_entry(payload, state="paused", reason=cmd)
                else:
                    await self.register("paused")
                return {"state": "paused", "player_id": "local", "command": cmd}
            ok = await self.player_resume()
            if not ok:
                return {"state": "error", "reason": "transport_command_failed", "command": cmd}
            self._local_queue_last_player_state = "playing"
            payload = await self._build_local_now_playing_payload()
            if payload:
                await self._publish_local_entry(payload, state="playing", reason=cmd)
            else:
                await self.register("playing", auto_power=True)
            return {"state": "playing", "player_id": "local", "command": cmd}

        if not self._local_queue_entries:
            return {"state": "error", "reason": "transport_command_failed", "command": cmd}

        if cmd == "transport_next":
            start_index = self._local_queue_index + 1
            payload = await self._play_local_queue_from(start_index, step=1, reason=cmd)
        elif cmd == "transport_previous":
            start_index = (self._local_queue_index - 1) if self._local_queue_index >= 0 else 0
            payload = await self._play_local_queue_from(start_index, step=-1, reason=cmd)
        else:
            payload = None
        if not payload:
            return {"state": "error", "reason": "transport_command_failed", "command": cmd}
        return {
            "state": payload["state"],
            "player_id": "local",
            "command": cmd,
            "uri": payload["uri"],
            "index": self._local_queue_index,
        }

    async def _build_local_now_playing_payload(self):
        if not self._local_queue_entries:
            return None
        if self._local_queue_index < 0 or self._local_queue_index >= len(self._local_queue_entries):
            return None
        entry = await self._resolve_local_queue_entry(self._local_queue_index)
        if not isinstance(entry, dict):
            return None
        return {
            "state": self._registered_state if self._registered_state in {"playing", "paused"} else "playing",
            "queue_id": "local",
            "player_id": "local",
            "title": str(entry.get("title") or "Music Assistant").strip(),
            "artist": str(entry.get("artist") or "").strip(),
            "album": str(entry.get("album") or "").strip(),
            "artwork": str(entry.get("artwork") or "").strip(),
            "uri": str(entry.get("source_uri") or "").strip(),
            "source_uri": str(entry.get("source_uri") or "").strip(),
        }

    def _build_local_queue_root(self):
        if not self._local_queue_entries:
            return None

        queue_node = self._make_folder_node(
            id_="queue",
            name="Queue",
            artist="Now Playing",
            image="",
        )
        for index, entry in enumerate(self._local_queue_entries):
            artist = str(entry.get("artist") or "").strip()
            if index == self._local_queue_index:
                artist = "Now Playing" if not artist else f"Now Playing - {artist}"
            queue_node["tracks"].append(
                self._make_leaf_node(
                    id_=f"{LOCAL_QUEUE_ITEM_PREFIX}{index}",
                    name=str(entry.get("title") or f"Queue Item {index + 1}"),
                    artist=artist,
                    url=str(entry.get("source_uri") or "").strip(),
                    image=str(entry.get("artwork") or "").strip(),
                )
            )
        if not queue_node["tracks"]:
            return None
        self._finalize_node(queue_node)
        queue_node["queue_id"] = "local"
        queue_node["current_index"] = self._local_queue_index
        return queue_node

    def _command_node_id(self, data):
        if not isinstance(data, dict):
            return ""
        return str(
            data.get("playlist_id")
            or data.get("item_id")
            or data.get("id")
            or ""
        ).strip()

    def _build_command_media_payload(self, data, uri, state="playing"):
        node = self._find_node_by_id(self._command_node_id(data))
        if not isinstance(node, dict):
            return None
        artwork = str(node.get("image") or "").strip()
        return {
            "state": str(state or "playing").strip().lower() or "playing",
            "title": str(node.get("name") or "").strip() or "Music Assistant",
            "artist": str(node.get("artist") or "").strip(),
            "album": str(node.get("album") or "").strip(),
            "artwork": artwork,
            "uri": str(uri or node.get("url") or node.get("play_url") or "").strip(),
        }

    async def _publish_now_playing(self, queue_id, *, data=None, requested_uri="", reason="track_change", force_state=""):
        if self._local_queue_active:
            payload = await self._build_local_now_playing_payload()
            if payload:
                await self._publish_local_entry(
                    payload,
                    state=force_state or payload.get("state") or "playing",
                    reason=reason,
                )
                return payload
        payload = await self._build_now_playing_payload(queue_id)
        requested = str(requested_uri or "").strip()
        payload_uri = str(payload.get("uri") or "").strip() if isinstance(payload, dict) else ""
        if not payload or (requested and payload_uri and payload_uri != requested):
            fallback = self._build_command_media_payload(data or {}, requested, state=force_state or "playing")
            if fallback:
                if isinstance(payload, dict):
                    payload.update({k: v for k, v in fallback.items() if v or not payload.get(k)})
                else:
                    payload = fallback
        if not isinstance(payload, dict):
            return None

        register_state = str(force_state or payload.get("state") or "").strip().lower()
        if register_state == "paused":
            await self.register("paused")
        else:
            register_state = "playing"
            await self.register("playing", auto_power=True)

        await self.post_media_update(
            title=str(payload.get("title") or "").strip(),
            artist=str(payload.get("artist") or "").strip(),
            album=str(payload.get("album") or "").strip(),
            artwork=str(payload.get("artwork") or "").strip(),
            state=register_state,
            reason=reason,
            track_uri=str(payload.get("uri") or "").strip(),
        )
        payload["state"] = register_state
        return payload

    async def handle_resync(self) -> dict:
        if self._local_queue_active:
            payload = await self._build_local_now_playing_payload()
            if payload:
                await self._publish_local_entry(
                    payload,
                    state=payload.get("state") or "playing",
                    reason="resync",
                )
                return {"status": "ok", "resynced": True, "state": payload.get("state", "playing")}
            await self.register("available")
            return {"status": "ok", "resynced": False}
        for queue_id in await self._resolve_queue_candidates():
            payload = await self._build_now_playing_payload(queue_id)
            if not isinstance(payload, dict):
                continue
            raw_state = self._extract_playback_state(payload)
            register_state = "paused" if raw_state == "paused" else (
                "playing" if self._is_active_state(raw_state) else ""
            )
            if not register_state:
                continue
            await self._publish_now_playing(
                queue_id,
                requested_uri=payload.get("uri", ""),
                reason="resync",
                force_state=register_state,
            )
            return {"status": "ok", "resynced": True, "state": register_state}

        await self.register("available")
        return {"status": "ok", "resynced": False}

    # ── Playback ──────────────────────────────────────────────────────────────

    def _find_node_by_id(self, node_id):
        if not node_id:
            return None
        indexed = self._library_node_by_id.get(node_id)
        if isinstance(indexed, dict):
            return indexed
        self._rebuild_library_indexes()
        return self._library_node_by_id.get(node_id)

    def _resolve_command_url(self, data):
        """
        Resolve a playback URI from the command payload.
        Accepts: `url` (leaf), `play_url` (folder), or ID-based lookup.
        """
        # Direct URI fields (leaf `url` or folder `play_url`)
        uri = data.get('url', '') or data.get('play_url', '')
        if uri:
            return uri

        # ID-based fallback lookup
        item_id = data.get('playlist_id') or data.get('item_id') or data.get('id')
        if not item_id:
            return ''

        node = self._find_node_by_id(item_id)
        if not node:
            return ''

        track_index = data.get('track_index')
        if isinstance(track_index, int):
            tracks = node.get("tracks") or []
            if 0 <= track_index < len(tracks):
                return tracks[track_index].get("url", "")

        # Return folder's play_url or leaf's url
        return node.get("play_url", "") or node.get("url", "")

    @staticmethod
    def _playlist_db_id_from_uri(uri):
        text = str(uri or "").strip()
        if not text:
            return ""
        match = re.search(r"(?:^|//|/)(\d+)(?:/)?$", text)
        return match.group(1) if match else ""

    async def _handle_favorite_add_command(self, data, uri):
        response = await self._send_command_response("music/favorites/add_item", item=uri)
        if response is None:
            return {"state": "error", "reason": "favorite_add_failed", "uri": uri}
        return {"state": "favorited", "uri": uri}

    async def _handle_playlist_add_command(self, data, uri):
        playlist_uri = str(
            data.get("target_playlist_uri")
            or data.get("playlist_uri")
            or data.get("playlist")
            or ""
        ).strip()
        db_playlist_id = self._playlist_db_id_from_uri(playlist_uri)
        if not db_playlist_id:
            return {"state": "error", "reason": "missing_playlist", "uri": uri}
        response = await self._send_command_response(
            "music/playlists/add_playlist_tracks",
            db_playlist_id=db_playlist_id,
            uris=[uri],
        )
        if response is None:
            return {
                "state": "error",
                "reason": "playlist_add_failed",
                "uri": uri,
                "playlist_uri": playlist_uri,
            }
        return {
            "state": "playlist_added",
            "uri": uri,
            "playlist_uri": playlist_uri,
            "playlist_id": db_playlist_id,
        }

    async def _resolve_queue_candidates(self):
        preferred_player = str(self._preferred_player_id or "").strip()
        target_queue = str(TARGET_QUEUE_ID or "").strip()
        if target_queue and not preferred_player:
            return [target_queue]
        candidates = []
        available_players = []
        available_queues = []

        def add(candidate):
            value = str(candidate or "").strip()
            if value and value not in candidates:
                candidates.append(value)

        def note_player(candidate):
            value = str(candidate or "").strip()
            if value and value not in available_players:
                available_players.append(value)

        def note_queue(candidate):
            value = str(candidate or "").strip()
            if value and value not in available_queues:
                available_queues.append(value)

        if preferred_player:
            active_payload = await self.send_command(
                "player_queues/get_active_queue",
                player_id=preferred_player,
            )
            if isinstance(active_payload, dict):
                add(
                    active_payload.get("queue_id")
                    or active_payload.get("player_id")
                    or active_payload.get("id")
                )
            add(preferred_player)

        players = await self.send_command("players/all")
        player_items = players.get("items", []) if isinstance(players, dict) else (
            players if isinstance(players, list) else []
        )
        for player in player_items:
            if not isinstance(player, dict):
                continue
            player_id = player.get("player_id") or player.get("id")
            active_queue = player.get("active_queue") or player.get("queue_id")
            active_source = player.get("active_source")
            note_player(player_id)
            note_queue(active_queue)
            add(active_queue)
            add(active_source)
            if not active_queue and player_id:
                active_payload = await self.send_command(
                    "player_queues/get_active_queue",
                    player_id=player_id,
                )
                if isinstance(active_payload, dict):
                    active_queue_id = (
                        active_payload.get("queue_id")
                        or active_payload.get("player_id")
                        or active_payload.get("id")
                    )
                    note_queue(active_queue_id)
                    add(active_queue_id)

        queues = await self.send_command("player_queues/all")
        queue_items = queues.get("items", []) if isinstance(queues, dict) else (
            queues if isinstance(queues, list) else []
        )
        for queue in queue_items:
            if not isinstance(queue, dict):
                continue
            queue_id = queue.get("queue_id") or queue.get("id") or queue.get("player_id")
            note_queue(queue_id)
            add(queue_id)

        if not candidates:
            if len(available_queues) == 1:
                add(available_queues[0])
            elif len(available_players) == 1:
                # _get_queue_snapshot can resolve a player_id into its active queue.
                add(available_players[0])

        return candidates

    async def _resolve_media_candidates(self, uri):
        candidates = [[uri]]
        item = await self.send_command("music/item_by_uri", uri=uri)
        if item:
            candidates.append(item)
        return candidates

    @staticmethod
    def _option_order_for_uri(uri):
        uri_text = str(uri or "").lower()
        if any(token in uri_text for token in ("/track/", "track://")):
            return ("play", "replace")
        return ("replace", "play")

    @staticmethod
    def _extract_playback_state(payload):
        if not isinstance(payload, dict):
            return ""
        return str(
            payload.get("state")
            or payload.get("playback_state")
            or payload.get("status")
            or ""
        ).strip().lower()

    @staticmethod
    def _is_active_state(state):
        return str(state or "").strip().lower() in ACTIVE_PLAYBACK_STATES

    @staticmethod
    def _snapshot_has_loaded_media(payload):
        if not isinstance(payload, dict):
            return False
        current_item = payload.get("current_item")
        if isinstance(current_item, dict) and current_item:
            return True
        return bool(MassSource._extract_queue_items(payload))

    @staticmethod
    def _extract_progress_marker(payload):
        if not isinstance(payload, dict):
            return ""
        current_item = payload.get("current_item")
        if isinstance(current_item, dict):
            for key in ("queue_item_id", "uri", "item_id", "name"):
                value = current_item.get(key)
                if value not in (None, ""):
                    return str(value)
        for key in ("current_item_id", "current_index", "index_in_buffer", "elapsed_time"):
            value = payload.get(key)
            if value not in (None, ""):
                return str(value)
        return ""

    @staticmethod
    def _extract_queue_size(payload):
        if not isinstance(payload, dict):
            return -1
        for key in ("items", "item_count", "queue_items"):
            value = payload.get(key)
            if isinstance(value, int):
                return value
            if isinstance(value, list):
                return len(value)
        return -1

    @staticmethod
    def _extract_next_marker(payload):
        if not isinstance(payload, dict):
            return ""
        next_item = payload.get("next_item")
        if isinstance(next_item, dict):
            for key in ("queue_item_id", "uri", "item_id", "name"):
                value = next_item.get(key)
                if value not in (None, ""):
                    return str(value)
        for key in ("next_item_id", "next_index"):
            value = payload.get(key)
            if value not in (None, ""):
                return str(value)
        return ""

    async def _get_queue_snapshot(self, queue_id):
        async def _fetch_snapshot(candidate_queue_id):
            snapshot = await self.send_command("player_queues/get", queue_id=candidate_queue_id)
            result = dict(snapshot) if isinstance(snapshot, dict) else {}
            items_payload = await self.send_command(
                "player_queues/items",
                queue_id=candidate_queue_id,
                limit=500,
                offset=0,
            )
            if isinstance(items_payload, dict):
                for key, value in items_payload.items():
                    if key not in {"items", "queue_items"}:
                        result.setdefault(key, value)
            items = self._coerce_queue_items(items_payload)
            if items is not None:
                result["items"] = items
                result["queue_items"] = items
            result["resolved_queue_id"] = candidate_queue_id
            return result

        result = await _fetch_snapshot(queue_id)
        if self._extract_queue_items(result):
            return result

        active_queue = await self.send_command("player_queues/get_active_queue", player_id=queue_id)
        active_queue_id = ""
        if isinstance(active_queue, dict):
            active_queue_id = str(
                active_queue.get("queue_id")
                or active_queue.get("player_id")
                or active_queue.get("id")
                or ""
            ).strip()
        if active_queue_id and active_queue_id != str(queue_id).strip():
            active_result = await _fetch_snapshot(active_queue_id)
            if self._extract_queue_items(active_result) or self._extract_current_queue_item(active_result):
                return active_result
        return result

    @staticmethod
    def _coerce_queue_items(value):
        if isinstance(value, list):
            return value
        if not isinstance(value, dict):
            return None
        for key in ("items", "queue_items", "data", "result"):
            nested = value.get(key)
            if isinstance(nested, list):
                return nested
            if isinstance(nested, dict):
                nested_items = MassSource._coerce_queue_items(nested)
                if nested_items is not None:
                    return nested_items
        return None

    @staticmethod
    def _extract_queue_items(payload):
        if not isinstance(payload, dict):
            return []
        for key in ("items", "queue_items"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
            coerced = MassSource._coerce_queue_items(value)
            if coerced is not None:
                return coerced
        return []

    @staticmethod
    def _extract_queue_item_marker(item, fallback_index=None):
        if not isinstance(item, dict):
            return ""
        media_item = item.get("media_item") if isinstance(item.get("media_item"), dict) else {}
        for key in ("queue_item_id", "id", "item_id", "uri", "media_item_uri"):
            value = item.get(key)
            if value not in (None, ""):
                return str(value)
        for key in ("item_id", "uri", "name"):
            value = media_item.get(key)
            if value not in (None, ""):
                return str(value)
        if fallback_index is not None:
            return str(fallback_index)
        return ""

    def _extract_current_queue_item(self, payload):
        if not isinstance(payload, dict):
            return {}
        current_item = payload.get("current_item")
        if isinstance(current_item, dict) and current_item:
            return current_item

        queue_items = self._extract_queue_items(payload)
        if not queue_items:
            return {}

        current_marker = str(payload.get("current_item_id") or "").strip()
        if current_marker:
            for index, item in enumerate(queue_items):
                if self._extract_queue_item_marker(item, index) == current_marker:
                    return item

        current_index = payload.get("current_index")
        if isinstance(current_index, int) and 0 <= current_index < len(queue_items):
            return queue_items[current_index]

        if isinstance(current_index, str) and current_index.isdigit():
            index = int(current_index)
            if 0 <= index < len(queue_items):
                return queue_items[index]

        return queue_items[0] if len(queue_items) == 1 else {}

    @staticmethod
    def _extract_metadata_text(item):
        if not isinstance(item, dict):
            return ""
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        for key in ("description", "review"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for key in ("description", "review"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @staticmethod
    def _clean_detail_text(value):
        text = str(value or "").strip()
        if not text:
            return ""
        text = re.sub(r"(?i)<br\s*/?>", "\n", text)
        text = re.sub(r"(?i)</(p|div|blockquote|li|tr|h[1-6])>", "\n\n", text)
        text = re.sub(r"(?i)<li[^>]*>", "• ", text)
        text = re.sub(r"<[^>]+>", "", text)
        text = html.unescape(text)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def _iter_item_contexts(item):
        if not isinstance(item, dict):
            return
        seen = set()
        pending = [item]
        while pending:
            current = pending.pop(0)
            if not isinstance(current, dict):
                continue
            identity = id(current)
            if identity in seen:
                continue
            seen.add(identity)
            yield current
            for key in ("metadata", "media_item", "album"):
                nested = current.get(key)
                if isinstance(nested, dict):
                    pending.append(nested)

    @classmethod
    def _extract_first_scalar(cls, item, keys):
        for source in cls._iter_item_contexts(item):
            for key in keys:
                value = source.get(key)
                if isinstance(value, bool):
                    return value
                if isinstance(value, (int, float)):
                    return value
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return None

    @classmethod
    def _extract_item_year(cls, item):
        value = cls._extract_first_scalar(item, ("year", "release_year", "release_date", "date"))
        if value in (None, ""):
            return ""
        if isinstance(value, (int, float)):
            year = int(value)
            return str(year) if 1000 <= year <= 9999 else ""
        match = re.search(r"\b(19|20)\d{2}\b", str(value))
        return match.group(0) if match else ""

    @classmethod
    def _extract_item_datetime(cls, item):
        value = cls._extract_first_scalar(
            item,
            (
                "published_at",
                "publish_date",
                "published",
                "release_date",
                "date",
                "timestamp",
                "created_at",
                "added_at",
                "year",
                "release_year",
            ),
        )
        if value in (None, ""):
            return None

        if isinstance(value, (int, float)):
            numeric = float(value)
            if numeric <= 0:
                return None
            if numeric >= 1_000_000_000_000:
                numeric /= 1000.0
            if numeric >= 1_000_000_000:
                return datetime.datetime.fromtimestamp(numeric, tz=datetime.timezone.utc)
            year = int(numeric)
            if 1000 <= year <= 9999:
                return datetime.datetime(year, 1, 1, tzinfo=datetime.timezone.utc)
            return None

        text = str(value).strip()
        if not text:
            return None

        normalized = text.replace("Z", "+00:00") if text.endswith("Z") else text
        parsed = None
        try:
            parsed = datetime.datetime.fromisoformat(normalized)
        except ValueError:
            try:
                parsed = parsedate_to_datetime(text)
            except (TypeError, ValueError, IndexError):
                match = re.search(r"\b(19|20)\d{2}\b", text)
                if match:
                    parsed = datetime.datetime(int(match.group(0)), 1, 1, tzinfo=datetime.timezone.utc)

        if parsed is None:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.timezone.utc)
        return parsed.astimezone(datetime.timezone.utc)

    @classmethod
    def _sort_podcast_episodes(cls, episodes):
        entries = list(episodes or [])
        dated = []
        undated = []
        for index, episode in enumerate(entries):
            parsed = cls._extract_item_datetime(episode)
            if parsed is None:
                undated.append((index, episode))
            else:
                dated.append((parsed.timestamp(), index, episode))
        dated.sort(key=lambda entry: (entry[0], -entry[1]), reverse=True)
        return [episode for _, _, episode in dated] + [episode for _, episode in undated]

    @staticmethod
    def _coerce_duration_to_ms(value, *, assume_ms=False):
        if value in (None, ""):
            return 0
        if isinstance(value, (int, float)):
            numeric = float(value)
            if numeric <= 0:
                return 0
            if assume_ms or numeric >= 100000:
                return int(numeric)
            return int(numeric * 1000)
        text = str(value).strip()
        if not text:
            return 0
        if re.fullmatch(r"\d+:\d{2}(?::\d{2})?", text):
            parts = [int(part) for part in text.split(":")]
            if len(parts) == 2:
                minutes, seconds = parts
                return ((minutes * 60) + seconds) * 1000
            hours, minutes, seconds = parts
            return (((hours * 60) + minutes) * 60 + seconds) * 1000
        if re.fullmatch(r"\d+(?:\.\d+)?", text):
            return MassSource._coerce_duration_to_ms(float(text), assume_ms=assume_ms)
        return 0

    @classmethod
    def _extract_item_duration_ms(cls, item):
        key_specs = (
            ("duration_ms", True),
            ("duration", False),
            ("duration_seconds", False),
            ("runtime", False),
            ("length", False),
        )
        for source in cls._iter_item_contexts(item):
            for key, assume_ms in key_specs:
                if key not in source:
                    continue
                duration_ms = cls._coerce_duration_to_ms(source.get(key), assume_ms=assume_ms)
                if duration_ms > 0:
                    return duration_ms
        return 0

    @staticmethod
    def _format_duration_ms(value):
        total_ms = int(value or 0)
        if total_ms <= 0:
            return ""
        total_seconds = total_ms // 1000
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"

    @classmethod
    def _extract_item_genres(cls, item):
        genres = []
        seen = set()
        for source in cls._iter_item_contexts(item):
            for key in ("genres", "genre"):
                value = source.get(key)
                entries = value if isinstance(value, list) else [value]
                for entry in entries:
                    name = ""
                    if isinstance(entry, dict):
                        name = str(entry.get("name") or "").strip()
                    elif isinstance(entry, str):
                        name = entry.strip()
                    if not name:
                        continue
                    normalized = name.casefold()
                    if normalized in seen:
                        continue
                    seen.add(normalized)
                    genres.append(name)
        return ", ".join(genres[:3])

    @classmethod
    def _extract_item_provider(cls, item):
        seen = set()
        providers = []

        def add(value):
            name = str(value or "").strip()
            if not name:
                return
            normalized = name.casefold()
            if normalized in seen:
                return
            seen.add(normalized)
            providers.append(name)

        add(cls._extract_first_scalar(item, ("provider", "provider_domain", "provider_instance")))
        provider_mappings = item.get("provider_mappings")
        if isinstance(provider_mappings, list):
            for mapping in provider_mappings:
                if not isinstance(mapping, dict):
                    continue
                add(mapping.get("provider"))
                add(mapping.get("provider_domain"))
                add(mapping.get("provider_instance"))
                add(mapping.get("provider_instance_id"))
        return providers[0] if providers else ""

    @classmethod
    def _extract_item_track_number(cls, item):
        value = cls._extract_first_scalar(item, ("track_number", "position"))
        if isinstance(value, bool) or value in (None, ""):
            return ""
        if isinstance(value, (int, float)):
            number = int(value)
            return str(number) if number > 0 else ""
        text = str(value).strip()
        return text if text and text != "0" else ""

    @classmethod
    def _extract_item_explicit(cls, item):
        value = cls._extract_first_scalar(item, ("explicit", "is_explicit"))
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "explicit"}
        return False

    @staticmethod
    def _humanize_media_type(item):
        if not isinstance(item, dict):
            return ""
        value = str(item.get("media_type") or item.get("type") or "").strip().lower()
        if not value:
            return ""
        return value.replace("_", " ").title()

    def _extract_item_album_reference(self, item):
        if not isinstance(item, dict):
            return {"name": "", "uri": ""}
        for source in self._iter_item_contexts(item):
            album = source.get("album")
            if isinstance(album, dict):
                name = str(album.get("name") or "").strip()
                uri = str(album.get("uri") or "").strip()
                if name or uri:
                    return {"name": name, "uri": uri}
        return {"name": self._extract_item_album(item, ""), "uri": ""}

    def _extract_item_artist_reference(self, item):
        if not isinstance(item, dict):
            return {"name": "", "uri": ""}
        for source in self._iter_item_contexts(item):
            artists = source.get("artists")
            if isinstance(artists, list):
                for artist in artists:
                    if not isinstance(artist, dict):
                        continue
                    name = str(artist.get("name") or "").strip()
                    uri = str(artist.get("uri") or "").strip()
                    if name or uri:
                        return {"name": name, "uri": uri}
            artist = source.get("artist")
            if isinstance(artist, dict):
                name = str(artist.get("name") or "").strip()
                uri = str(artist.get("uri") or "").strip()
                if name or uri:
                    return {"name": name, "uri": uri}
        return {"name": self._extract_item_artist(item, ""), "uri": ""}

    @staticmethod
    def _normalize_lookup_key(value):
        return str(value or "").strip().casefold()

    def _find_artist_uri_by_name(self, artist_name):
        target = self._normalize_lookup_key(artist_name)
        if not target:
            return ""
        artists_root = next(
            (
                node for node in (self._library_data or [])
                if isinstance(node, dict) and str(node.get("id") or "") == "artists"
            ),
            None,
        )
        if not isinstance(artists_root, dict):
            return ""
        for artist_node in artists_root.get("tracks") or []:
            if not isinstance(artist_node, dict):
                continue
            if self._normalize_lookup_key(artist_node.get("name")) == target:
                return str(artist_node.get("play_url") or artist_node.get("url") or "").strip()
        return ""

    def _extract_artist_reference(self, queue_item):
        if not isinstance(queue_item, dict):
            return {"name": "", "uri": ""}
        media_item = queue_item.get("media_item") if isinstance(queue_item.get("media_item"), dict) else {}
        artists = media_item.get("artists")
        if isinstance(artists, list):
            for artist in artists:
                if not isinstance(artist, dict):
                    continue
                name = str(artist.get("name") or "").strip()
                uri = str(artist.get("uri") or "").strip()
                if name or uri:
                    return {"name": name, "uri": uri}
        return {
            "name": self._extract_queue_artist(queue_item),
            "uri": "",
        }

    async def _build_current_artist_info(self, queue_id):
        snapshot = await self._get_queue_snapshot(queue_id)
        current_item = self._extract_current_queue_item(snapshot)
        if not current_item:
            return None

        artist_ref = self._extract_artist_reference(current_item)
        artist_name = artist_ref.get("name") or ""
        artist_uri = artist_ref.get("uri") or self._find_artist_uri_by_name(artist_name)

        artist_item = None
        if artist_uri:
            artist_item = await self.send_command("music/item_by_uri", uri=artist_uri)
        if not isinstance(artist_item, dict):
            artist_item = {}

        bio = self._extract_metadata_text(artist_item)
        base = MASS_URI.replace("/ws", "").replace("ws://", "http://").replace("wss://", "https://")
        image = self._get_img(artist_item, base) or ""
        if image:
            image = await self._cache_image_locally(image)

        return {
            "state": "available" if bio else "empty",
            "name": artist_name,
            "bio": bio,
            "image": image,
        }

    async def _build_item_info_payload(self, uri):
        uri_text = str(uri or "").strip()
        if not uri_text:
            return {"state": "error", "error": "missing_uri"}

        item = await self.send_command("music/item_by_uri", uri=uri_text)
        if not isinstance(item, dict):
            return {"state": "error", "error": "item_not_found", "uri": uri_text}

        title = self._extract_item_title(item, "Music Assistant").strip()
        artist = self._extract_item_artist(item, "").strip()
        album_ref = self._extract_item_album_reference(item)
        album = str(album_ref.get("name") or "").strip()
        artist_ref = self._extract_item_artist_reference(item)
        if not artist:
            artist = str(artist_ref.get("name") or "").strip()

        artwork = await self._extract_item_artwork(item, "")
        description = self._clean_detail_text(self._extract_metadata_text(item))
        description_label = "About this item"

        if not description and album_ref.get("uri") and str(album_ref.get("uri")).strip() != uri_text:
            album_item = await self.send_command("music/item_by_uri", uri=str(album_ref.get("uri")).strip())
            if isinstance(album_item, dict):
                description = self._clean_detail_text(self._extract_metadata_text(album_item))
                if description:
                    description_label = "About the album"
                if not artwork:
                    artwork = await self._extract_item_artwork(album_item, artwork)

        if not description:
            artist_uri = str(artist_ref.get("uri") or "").strip() or self._find_artist_uri_by_name(artist)
            if artist_uri and artist_uri != uri_text:
                artist_item = await self.send_command("music/item_by_uri", uri=artist_uri)
                if isinstance(artist_item, dict):
                    description = self._clean_detail_text(self._extract_metadata_text(artist_item))
                    if description:
                        description_label = "About the artist"

        facts = []
        for label, value in (
            ("Artist", artist),
            ("Album", album),
            ("Type", self._humanize_media_type(item)),
            ("Year", self._extract_item_year(item)),
            ("Duration", self._format_duration_ms(self._extract_item_duration_ms(item))),
            ("Genre", self._extract_item_genres(item)),
            ("Provider", self._extract_item_provider(item)),
            ("Track", self._extract_item_track_number(item)),
        ):
            text = str(value or "").strip()
            if text:
                facts.append({"label": label, "value": text})

        if self._extract_item_explicit(item):
            facts.append({"label": "Explicit", "value": "Yes"})

        subtitle_parts = [part for part in (artist, album) if part]
        subtitle = " • ".join(subtitle_parts)

        return {
            "state": "available",
            "uri": uri_text,
            "play_uri": str(item.get("uri") or uri_text).strip(),
            "title": title or "Music Assistant",
            "subtitle": subtitle,
            "artist": artist,
            "album": album,
            "image": artwork,
            "description": description,
            "description_label": description_label if description else "",
            "facts": facts,
        }

    async def _get_player_state(self, player_id):
        state = await self.send_command("players/get", player_id=player_id)
        return state if isinstance(state, dict) else {}

    @staticmethod
    def _extract_player_current_media(payload):
        current_media = payload.get("current_media")
        return current_media if isinstance(current_media, dict) else {}

    @staticmethod
    def _extract_player_current_uri(payload):
        current_media = MassSource._extract_player_current_media(payload)
        if current_media:
            for key in ("uri", "media_item_uri", "url"):
                value = current_media.get(key)
                if value:
                    return str(value)
            media_item = current_media.get("media_item")
            if isinstance(media_item, dict):
                value = media_item.get("uri")
                if value:
                    return str(value)
        return ""

    @staticmethod
    def _player_matches_queue(payload, queue_id):
        if not isinstance(payload, dict):
            return False
        target = str(queue_id or "").strip()
        if not target:
            return False
        active_queue = str(payload.get("active_queue") or "").strip()
        active_source = str(payload.get("active_source") or "").strip()
        if active_queue == target or active_source == target:
            return True
        current_media = MassSource._extract_player_current_media(payload)
        source_id = str(current_media.get("source_id") or "").strip()
        return source_id == target

    def _extract_player_title(self, payload):
        current_media = self._extract_player_current_media(payload)
        media_item = current_media.get("media_item") if isinstance(current_media.get("media_item"), dict) else {}
        for container in (current_media, media_item):
            for key in ("name", "title", "sort_name"):
                value = container.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""

    def _extract_player_artist(self, payload):
        current_media = self._extract_player_current_media(payload)
        media_item = current_media.get("media_item") if isinstance(current_media.get("media_item"), dict) else {}
        for container in (current_media, media_item):
            for key in ("artist", "artist_str"):
                value = container.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        if media_item:
            return self._get_artist_name(media_item, "")
        return ""

    def _extract_player_album(self, payload):
        current_media = self._extract_player_current_media(payload)
        media_item = current_media.get("media_item") if isinstance(current_media.get("media_item"), dict) else {}
        for container in (current_media, media_item):
            for key in ("album", "album_name"):
                value = container.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
                if isinstance(value, dict):
                    name = value.get("name")
                    if isinstance(name, str) and name.strip():
                        return name.strip()
        if media_item:
            return self._extract_queue_album({"media_item": media_item})
        return ""

    async def _extract_player_artwork(self, payload, base):
        current_media = self._extract_player_current_media(payload)
        media_item = current_media.get("media_item") if isinstance(current_media.get("media_item"), dict) else {}
        artwork = (
            current_media.get("image")
            or self._get_img(current_media, base)
            or self._get_img(media_item, base)
            or ""
        )
        if artwork:
            artwork = await self._cache_image_locally(artwork)
        return artwork

    @staticmethod
    def _extract_queue_uri(item):
        if not isinstance(item, dict):
            return ""
        for key in ("uri", "media_item_uri"):
            value = item.get(key)
            if value:
                return str(value)
        media_item = item.get("media_item")
        if isinstance(media_item, dict):
            value = media_item.get("uri")
            if value:
                return str(value)
        return ""

    @staticmethod
    def _extract_queue_name(item, fallback="Queued Item"):
        if not isinstance(item, dict):
            return fallback
        for key in ("name", "sort_name"):
            value = item.get(key)
            if value:
                return str(value)
        media_item = item.get("media_item")
        if isinstance(media_item, dict):
            for key in ("name", "sort_name"):
                value = media_item.get(key)
                if value:
                    return str(value)
        return fallback

    def _extract_queue_artist(self, item):
        if not isinstance(item, dict):
            return ""
        for key in ("artist", "artist_str"):
            value = item.get(key)
            if value:
                return str(value)
        media_item = item.get("media_item")
        if isinstance(media_item, dict):
            artist = media_item.get("artist_str")
            if artist:
                return str(artist)
            return self._get_artist_name(media_item, "")
        return ""

    @staticmethod
    def _extract_queue_album(item):
        if not isinstance(item, dict):
            return ""
        for key in ("album", "album_name"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                name = value.get("name")
                if isinstance(name, str) and name.strip():
                    return name.strip()
        media_item = item.get("media_item")
        if isinstance(media_item, dict):
            album = media_item.get("album")
            if isinstance(album, dict):
                name = album.get("name")
                if isinstance(name, str) and name.strip():
                    return name.strip()
            for key in ("album", "album_name"):
                value = media_item.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""

    async def _fetch_item_artwork_by_uri(self, uri, base):
        uri_text = str(uri or "").strip()
        if not uri_text:
            return ""
        item = await self.send_command("music/item_by_uri", uri=uri_text)
        if not isinstance(item, dict):
            return ""

        artwork = self._get_img(item, base) or ""
        if not artwork:
            album = item.get("album")
            if isinstance(album, dict):
                artwork = self._get_img(album, base) or ""
                if not artwork:
                    album_uri = str(album.get("uri") or "").strip()
                    if album_uri and album_uri != uri_text:
                        album_item = await self.send_command("music/item_by_uri", uri=album_uri)
                        if isinstance(album_item, dict):
                            artwork = self._get_img(album_item, base) or ""
        if artwork:
            artwork = await self._cache_image_locally(artwork)
        return artwork

    async def _build_now_playing_payload(self, queue_id):
        if self._local_queue_active:
            return await self._build_local_now_playing_payload()
        snapshot = await self._get_queue_snapshot(queue_id)
        if not snapshot:
            return None

        resolved_queue_id = str(snapshot.get("resolved_queue_id") or queue_id).strip()
        base = MASS_URI.replace("/ws", "").replace("ws://", "http://").replace("wss://", "https://")
        for player_id in await self._resolve_player_candidates(resolved_queue_id or queue_id):
            player_state = await self._get_player_state(player_id)
            if not player_state or not self._player_matches_queue(player_state, resolved_queue_id or queue_id):
                continue
            current_media = self._extract_player_current_media(player_state)
            if current_media:
                current_uri = self._extract_player_current_uri(player_state)
                artwork = await self._extract_player_artwork(player_state, base)
                if not artwork:
                    artwork = await self._fetch_item_artwork_by_uri(current_uri, base)
                if not artwork:
                    current_item = self._extract_current_queue_item(snapshot)
                    if current_item:
                        queue_uri = self._extract_queue_uri(current_item)
                        if not current_uri or queue_uri == current_uri:
                            media_item = current_item.get("media_item") if isinstance(current_item.get("media_item"), dict) else {}
                            artwork = current_item.get("image") or self._get_img(media_item, base) or ""
                            if artwork:
                                artwork = await self._cache_image_locally(artwork)
                return {
                    "state": self._extract_playback_state(player_state) or self._extract_playback_state(snapshot) or "idle",
                    "queue_id": resolved_queue_id,
                    "player_id": str(player_id or "").strip(),
                    "title": self._extract_player_title(player_state),
                    "artist": self._extract_player_artist(player_state),
                    "album": self._extract_player_album(player_state),
                    "artwork": artwork,
                    "uri": current_uri,
                }

        current_item = self._extract_current_queue_item(snapshot)
        state = self._extract_playback_state(snapshot) or "idle"
        if not current_item:
            return {"state": state, "queue_id": resolved_queue_id}

        media_item = current_item.get("media_item") if isinstance(current_item.get("media_item"), dict) else {}
        artwork = current_item.get("image") or self._get_img(media_item, base) or ""
        if artwork:
            artwork = await self._cache_image_locally(artwork)

        return {
            "state": state,
            "queue_id": resolved_queue_id,
            "title": self._extract_queue_name(current_item, ""),
            "artist": self._extract_queue_artist(current_item),
            "album": self._extract_queue_album(current_item),
            "artwork": artwork,
            "uri": self._extract_queue_uri(current_item),
        }

    async def _build_queue_root(self):
        if self._local_queue_active or self._local_queue_entries:
            local_queue = self._build_local_queue_root()
            if local_queue:
                return local_queue
        base = MASS_URI.replace("/ws", "").replace("ws://", "http://").replace("wss://", "https://")
        queue_ids = await self._resolve_queue_candidates()
        if not queue_ids:
            return None

        snapshot = {}
        queue_id = ""
        for candidate in queue_ids:
            candidate_snapshot = await self._get_queue_snapshot(candidate)
            if candidate_snapshot:
                snapshot = candidate_snapshot
                queue_id = str(candidate_snapshot.get("resolved_queue_id") or candidate).strip()
                if self._extract_queue_items(candidate_snapshot):
                    break

        queue_items = self._extract_queue_items(snapshot)
        if not queue_items:
            current_item = self._extract_current_queue_item(snapshot)
            if current_item:
                queue_items = [current_item]
        if not queue_items:
            return None

        queue_node = self._make_folder_node(
            id_="queue",
            name="Queue",
            artist="Now Playing",
            image="",
        )

        current_marker = str(snapshot.get("current_item_id") or "").strip()
        current_item = snapshot.get("current_item")
        if isinstance(current_item, dict) and current_item:
            current_marker = current_marker or self._extract_queue_item_marker(current_item)
        current_index = snapshot.get("current_index")
        if isinstance(current_index, str) and current_index.isdigit():
            current_index = int(current_index)
        if not isinstance(current_index, int):
            current_index = -1
        resolved_current_index = -1

        for index, item in enumerate(queue_items):
            if not isinstance(item, dict):
                continue

            media_item = item.get("media_item") if isinstance(item.get("media_item"), dict) else {}
            queue_item_id = str(
                item.get("queue_item_id")
                or item.get("id")
                or media_item.get("item_id")
                or index
            )
            uri = self._extract_queue_uri(item)

            artist = self._extract_queue_artist(item)
            image = item.get("image") or self._get_img(media_item, base) or ""
            if isinstance(image, str) and image.startswith("http"):
                # Queue requests must stay fast enough for the router timeout;
                # reuse cached artwork when available but never block on a
                # fresh network fetch while assembling the queue payload.
                cached_name = self._find_cached_art_name(image)
                if cached_name:
                    image = f"{ART_ROUTE_PREFIX}/{cached_name}"

            name = self._extract_queue_name(item, f"Queue Item {index + 1}")
            marker = self._extract_queue_item_marker(item, index)
            is_current = bool(current_marker and marker and marker == current_marker)
            if not is_current and current_index == index:
                is_current = True
            if is_current:
                resolved_current_index = index
                artist = "Now Playing" if not artist else f"Now Playing - {artist}"

            queue_node["tracks"].append(
                self._make_leaf_node(
                    id_=f"queue_item_{queue_item_id}",
                    name=name,
                    artist=artist,
                    url=uri,
                    image=image,
                )
            )

        if not queue_node["tracks"]:
            return None

        self._finalize_node(queue_node)
        queue_node["queue_id"] = queue_id
        queue_node["current_index"] = resolved_current_index
        return queue_node

    async def get_queue(self, start=0, max_items=50):
        queue_node = await self._build_queue_root()
        if not queue_node:
            return {"tracks": [], "current_index": -1, "total": 0}

        try:
            start = max(0, int(start))
        except (TypeError, ValueError):
            start = 0
        try:
            max_items = max(1, int(max_items))
        except (TypeError, ValueError):
            max_items = 50

        all_tracks = queue_node.get("tracks") or []
        current_index = queue_node.get("current_index", -1)
        if not isinstance(current_index, int) or current_index < 0:
            current_index = -1
            for index, track in enumerate(all_tracks):
                artist_text = str(track.get("artist") or "").strip().lower()
                if artist_text.startswith("now playing"):
                    current_index = index
                    break

        tracks = []
        for index, track in enumerate(all_tracks[start:start + max_items], start=start):
            artist = str(track.get("artist") or "").strip()
            current = artist.lower().startswith("now playing")
            if current:
                artist = re.sub(r"^Now Playing\s*-\s*", "", artist, flags=re.I).strip()
            tracks.append({
                "id": str(track.get("id") or f"q:{index}"),
                "title": str(track.get("name") or track.get("title") or f"Queue Item {index + 1}"),
                "artist": artist,
                "album": str(track.get("album") or ""),
                "artwork": str(track.get("image") or track.get("artwork") or ""),
                "uri": str(track.get("url") or ""),
                "index": index,
                "current": current or index == current_index,
            })

        return {
            "tracks": tracks,
            "current_index": current_index,
            "total": len(all_tracks),
            "queue_id": queue_node.get("queue_id", ""),
        }

    def _queue_has_progressed(self, before, after):
        before_state = self._extract_playback_state(before)
        after_state = self._extract_playback_state(after)
        if self._is_active_state(after_state):
            return True
        before_marker = self._extract_progress_marker(before)
        after_marker = self._extract_progress_marker(after)
        return bool(after_marker and after_marker != before_marker) or (
            bool(after_state) and after_state != before_state
        )

    def _queue_has_enqueued(self, before, after):
        before_size = self._extract_queue_size(before)
        after_size = self._extract_queue_size(after)
        if before_size >= 0 and after_size > before_size:
            return True
        before_next = self._extract_next_marker(before)
        after_next = self._extract_next_marker(after)
        if after_next and after_next != before_next:
            return True
        return False

    async def _wait_for_queue_progress(
        self,
        queue_id,
        before_snapshot,
        require_active=False,
        attempts=PLAYBACK_POST_KICK_ATTEMPTS,
        delay=PLAYBACK_POST_KICK_DELAY,
    ):
        latest_snapshot = before_snapshot or {}
        for _ in range(attempts):
            latest_snapshot = await self._get_queue_snapshot(queue_id)
            queue_state = self._extract_playback_state(latest_snapshot)
            if require_active:
                if self._is_active_state(queue_state):
                    return True, latest_snapshot
            elif self._queue_has_progressed(before_snapshot, latest_snapshot):
                return True, latest_snapshot
            await asyncio.sleep(delay)
        return False, latest_snapshot

    async def _resolve_player_candidates(self, queue_id):
        candidates = []
        available_players = []

        def add(candidate):
            value = str(candidate or "").strip()
            if value and value not in candidates:
                candidates.append(value)

        def note_available(candidate):
            value = str(candidate or "").strip()
            if value and value not in available_players:
                available_players.append(value)

        preferred_player = str(self._preferred_player_id or "").strip()
        if preferred_player:
            add(preferred_player)

        if TARGET_PLAYER_ID and not preferred_player:
            add(TARGET_PLAYER_ID)
            return candidates

        players = await self.send_command("players/all")
        player_items = players.get("items", []) if isinstance(players, dict) else (
            players if isinstance(players, list) else []
        )
        for player in player_items:
            if not isinstance(player, dict):
                continue
            player_id = player.get("player_id") or player.get("id")
            note_available(player_id)
            active_queue = player.get("active_queue")
            active_source = player.get("active_source")
            if str(player_id or "").strip() == str(queue_id or "").strip():
                add(player_id)
            if str(active_queue or "").strip() == str(queue_id or "").strip():
                add(player_id)
            if str(active_source or "").strip() == str(queue_id or "").strip():
                add(player_id)

        if not candidates and len(available_players) == 1:
            add(available_players[0])

        return candidates

    async def _resolve_transport_player_candidates(self, preferred_player=None):
        if preferred_player is None:
            preferred_player = self._preferred_player_id
        preferred_player = str(preferred_player or "").strip()
        if preferred_player:
            return [preferred_player]

        target_queue = str(TARGET_QUEUE_ID or "").strip()
        candidates = []

        def add(candidate):
            value = str(candidate or "").strip()
            if value and value not in candidates:
                candidates.append(value)

        if TARGET_PLAYER_ID:
            return [str(TARGET_PLAYER_ID).strip()]
        if target_queue:
            for player_id in await self._resolve_player_candidates(target_queue):
                add(player_id)
            return candidates
        for queue_id in await self._resolve_queue_candidates():
            for player_id in await self._resolve_player_candidates(queue_id):
                add(player_id)
        return candidates

    @staticmethod
    def _options_for_request(cmd, uri, queue_state, source_active):
        if cmd == "play_now":
            return ("replace",)
        if cmd == "play_next":
            if source_active and str(queue_state or "").strip().lower() in ACTIVE_PLAYBACK_STATES:
                return ("next", "add")
            return ("add", "replace")
        if cmd == "queue_item":
            return ("add",)
        if cmd == "play_radio":
            return ("replace",)
        if source_active and str(queue_state or "").strip().lower() in ACTIVE_PLAYBACK_STATES:
            return ("add",)
        uri_text = str(uri or "").lower()
        if any(token in uri_text for token in ("/track/", "track://")):
            return ("play",)
        return ("replace",)

    async def _handle_transport_command(self, cmd, *, preferred_player=None):
        if self._local_queue_active:
            return await self._handle_local_transport_command(cmd)
        command_map = {
            "transport_toggle": "players/cmd/play_pause",
            "transport_stop": "players/cmd/stop",
            "transport_next": "players/cmd/next",
            "transport_previous": "players/cmd/previous",
        }
        api_command = command_map.get(cmd)
        if not api_command:
            return {"state": "error", "reason": "unsupported_transport_command", "command": cmd}

        successful_player = ""
        for player_id in await self._resolve_transport_player_candidates(preferred_player=preferred_player):
            response = await self._send_command_response(api_command, player_id=player_id)
            if response is not None:
                successful_player = player_id
                break
        if not successful_player:
            return {"state": "error", "reason": "transport_command_failed", "command": cmd}

        if cmd == "transport_stop":
            await self.register("available")
            return {"state": "available", "player_id": successful_player, "command": cmd}

        await asyncio.sleep(0.25)
        for queue_id in await self._resolve_queue_candidates():
            payload = await self._build_now_playing_payload(queue_id)
            if not isinstance(payload, dict):
                continue
            raw_state = self._extract_playback_state(payload)
            register_state = "paused" if raw_state == "paused" else (
                "playing" if self._is_active_state(raw_state) else ""
            )
            if not register_state:
                continue
            await self._publish_now_playing(
                queue_id,
                requested_uri=payload.get("uri", ""),
                reason=cmd,
                force_state=register_state,
            )
            return {
                "state": register_state,
                "player_id": successful_player,
                "command": cmd,
                "uri": payload.get("uri", ""),
            }

        if self._last_media:
            register_state = "paused" if cmd == "transport_toggle" and self._registered_state == "playing" else "playing"
            await self.register(register_state, auto_power=(register_state == "playing"))
            await self.post_media_update(
                title=self._last_media.get("title", ""),
                artist=self._last_media.get("artist", ""),
                album=self._last_media.get("album", ""),
                artwork=self._last_media.get("artwork", ""),
                state=register_state,
                reason=cmd,
                track_uri=self._last_media.get("track_uri", ""),
            )
            return {
                "state": register_state,
                "player_id": successful_player,
                "command": cmd,
                "uri": self._last_media.get("track_uri", ""),
            }
        return {"state": "available", "player_id": successful_player, "command": cmd}

    @staticmethod
    def _api_response_ok(response):
        if not isinstance(response, dict):
            return False
        if response.get("error"):
            return False
        return response.get("result") is not False

    def _apply_playback_target_from_data(self, data):
        if not isinstance(data, dict):
            return ""
        target_player_id = str(
            data.get("target_player_id")
            or data.get("audio_target_id")
            or (data.get("playback") or {}).get("audio_target_id")
            or ""
        ).strip()
        if target_player_id:
            self._preferred_player_id = target_player_id
        return target_player_id

    @staticmethod
    def _clean_queue_item_id(value):
        text = str(value or "").strip()
        if text.startswith("queue_item_"):
            return text[len("queue_item_"):]
        return text

    async def _resolve_queue_command_item(self, data):
        raw_item_id = self._clean_queue_item_id(
            data.get("queue_item_id") or data.get("id") or data.get("item_id")
        )
        raw_index = data.get("index")
        try:
            requested_index = int(raw_index)
        except (TypeError, ValueError):
            requested_index = -1
        for queue_id in await self._resolve_queue_candidates():
            snapshot = await self._get_queue_snapshot(queue_id)
            items = self._extract_queue_items(snapshot)
            for index, item in enumerate(items):
                marker = self._clean_queue_item_id(self._extract_queue_item_marker(item, index))
                if (raw_item_id and marker == raw_item_id) or index == requested_index:
                    return str(snapshot.get("resolved_queue_id") or queue_id).strip(), item, index, snapshot
        return "", {}, -1, {}

    async def _handle_queue_remove_command(self, data):
        queue_id, item, index, _snapshot = await self._resolve_queue_command_item(data)
        if not queue_id or not item:
            return {"state": "error", "reason": "queue_item_not_found"}
        queue_item_id = self._clean_queue_item_id(self._extract_queue_item_marker(item, index))
        attempts = [
            ("player_queues/delete_item", {"queue_id": queue_id, "queue_item_id": queue_item_id}),
            ("player_queues/remove_item", {"queue_id": queue_id, "queue_item_id": queue_item_id}),
            ("player_queues/delete_item", {"queue_id": queue_id, "item_id": queue_item_id}),
            ("player_queues/remove_item", {"queue_id": queue_id, "item_id": queue_item_id}),
        ]
        for command, kwargs in attempts:
            response = await self._send_command_response(command, **kwargs)
            if self._api_response_ok(response):
                return {
                    "state": "removed",
                    "queue_id": queue_id,
                    "queue_item_id": queue_item_id,
                    "index": index,
                    "command": command,
                }
        return {"state": "error", "reason": "remove_failed", "queue_item_id": queue_item_id}

    async def _handle_queue_play_next_command(self, data):
        queue_id, item, index, snapshot = await self._resolve_queue_command_item(data)
        if not queue_id or not item:
            return {"state": "error", "reason": "queue_item_not_found"}
        queue_item_id = self._clean_queue_item_id(self._extract_queue_item_marker(item, index))
        current_index = snapshot.get("current_index")
        try:
            current_index = int(current_index)
        except (TypeError, ValueError):
            current_index = -1
        target_index = max(0, current_index + 1) if current_index >= 0 else 0
        if index <= target_index and current_index >= 0:
            target_index = max(0, current_index)
        pos_shift = target_index - index
        attempts = [
            ("player_queues/move_item", {"queue_id": queue_id, "queue_item_id": queue_item_id, "pos_shift": pos_shift}),
            ("player_queues/move_item", {"queue_id": queue_id, "queue_item_id": queue_item_id, "position": target_index}),
            ("player_queues/move_item", {"queue_id": queue_id, "item_id": queue_item_id, "pos_shift": pos_shift}),
            ("player_queues/move_item", {"queue_id": queue_id, "item_id": queue_item_id, "position": target_index}),
        ]
        for command, kwargs in attempts:
            response = await self._send_command_response(command, **kwargs)
            if self._api_response_ok(response):
                return {
                    "state": "moved",
                    "queue_id": queue_id,
                    "queue_item_id": queue_item_id,
                    "index": index,
                    "target_index": target_index,
                    "command": command,
                }
        return {"state": "error", "reason": "move_failed", "queue_item_id": queue_item_id}

    async def _handle_queue_play_index_command(self, data):
        local_queue_item_index = self._parse_local_queue_index(
            self._clean_queue_item_id(
                data.get("queue_item_id") or data.get("id") or data.get("item_id")
            )
        )
        if local_queue_item_index >= 0 and self._local_queue_entries:
            payload = await self._play_local_queue_index(local_queue_item_index, reason="queue_play")
            if payload:
                return {
                    "state": "playing",
                    "queue_id": "local",
                    "index": local_queue_item_index,
                    "queue_item_id": f"{LOCAL_QUEUE_ITEM_PREFIX}{local_queue_item_index}",
                    "command": "local_queue_play",
                }
            return {"state": "error", "reason": "play_index_failed", "queue_item_id": local_queue_item_index}

        queue_id, item, index, _snapshot = await self._resolve_queue_command_item(data)
        if not queue_id or not item:
            return {"state": "error", "reason": "queue_item_not_found"}
        queue_item_id = self._clean_queue_item_id(self._extract_queue_item_marker(item, index))
        attempts = [
            ("player_queues/play_item", {"queue_id": queue_id, "queue_item_id": queue_item_id}),
            ("player_queues/play_item", {"queue_id": queue_id, "item_id": queue_item_id}),
            ("player_queues/play_index", {"queue_id": queue_id, "index": index}),
            ("player_queues/resume", {"queue_id": queue_id, "queue_item_id": queue_item_id}),
        ]
        for command, kwargs in attempts:
            response = await self._send_command_response(command, **kwargs)
            if self._api_response_ok(response):
                await asyncio.sleep(0.25)
                await self._publish_now_playing(queue_id, reason="queue_play", force_state="playing")
                return {
                    "state": "playing",
                    "queue_id": queue_id,
                    "index": index,
                    "queue_item_id": queue_item_id,
                    "command": command,
                }

        uri = self._extract_queue_uri(item)
        if uri:
            fallback = dict(data)
            fallback["url"] = uri
            return await self.handle_command("play_now", fallback)
        return {"state": "error", "reason": "play_index_failed", "queue_item_id": queue_item_id}

    async def _handle_transfer_queue_command(self, data):
        self._apply_playback_target_from_data(data)
        target_player_id = str(
            data.get("target_player_id")
            or data.get("player_id")
            or data.get("target")
            or ""
        ).strip()
        target_queue_id = str(data.get("target_queue_id") or target_player_id).strip()
        if not target_queue_id and not target_player_id:
            return {"state": "error", "reason": "missing_target_player"}

        source_queue_id = ""
        source_player_id = ""
        snapshot = {}
        for queue_id in await self._resolve_queue_candidates():
            candidate = await self._get_queue_snapshot(queue_id)
            if not isinstance(candidate, dict):
                continue
            source_queue_id = str(candidate.get("resolved_queue_id") or queue_id).strip()
            snapshot = candidate
            for player_id in await self._resolve_player_candidates(source_queue_id or queue_id):
                source_player_id = str(player_id or "").strip()
                if source_player_id:
                    break
            if self._extract_queue_items(candidate) or self._extract_current_queue_item(candidate):
                break

        if not source_queue_id:
            return {"state": "error", "reason": "missing_source_queue"}

        source_player_id = source_player_id or source_queue_id
        target_player_id = target_player_id or target_queue_id
        attempts = [
            (
                "player_queues/transfer",
                {
                    "source_queue_id": source_queue_id,
                    "target_queue_id": target_queue_id,
                },
            ),
            (
                "player_queues/transfer",
                {
                    "source_queue_id": source_queue_id,
                    "target_queue_id": target_queue_id,
                    "auto_play": True,
                },
            ),
            (
                "player_queues/transfer_queue",
                {
                    "source_queue_id": source_queue_id,
                    "target_player_id": target_player_id,
                    "auto_play": True,
                },
            ),
            (
                "player_queues/transfer_queue",
                {
                    "queue_id": source_queue_id,
                    "target_player_id": target_player_id,
                    "auto_play": True,
                },
            ),
            (
                "player_queues/transfer_queue",
                {
                    "source_player_id": source_player_id,
                    "target_player_id": target_player_id,
                    "auto_play": True,
                },
            ),
            (
                "player_queues/transfer_queue",
                {
                    "source_queue_id": source_queue_id,
                    "target_queue_id": target_player_id,
                    "auto_play": True,
                },
            ),
            (
                "player_queues/transfer",
                {
                    "source_player_id": source_player_id,
                    "target_player_id": target_player_id,
                    "auto_play": True,
                },
            ),
        ]

        last_error = None
        for api_command, kwargs in attempts:
            response = await self._send_command_response(api_command, **kwargs)
            if self._api_response_ok(response):
                self._preferred_player_id = target_player_id
                await asyncio.sleep(0.35)
                published = None
                for queue_id in await self._resolve_queue_candidates():
                    published = await self._publish_now_playing(
                        queue_id,
                        requested_uri=self._extract_queue_uri(self._extract_current_queue_item(snapshot)),
                        reason="transfer_queue",
                    )
                    if published:
                        break
                return {
                    "state": "transferred",
                    "source_queue_id": source_queue_id,
                    "source_player_id": source_player_id,
                    "target_player_id": target_player_id,
                    "target_queue_id": target_queue_id,
                    "command": api_command,
                    "media": published or {},
                }
            if isinstance(response, dict):
                last_error = response.get("error") or response

        logger.warning(
            "MASS queue transfer failed source_queue=%s source_player=%s target_player=%s error=%s",
            source_queue_id,
            source_player_id,
            target_player_id,
            last_error,
        )
        return {
            "state": "error",
            "reason": "transfer_failed",
            "source_queue_id": source_queue_id,
            "source_player_id": source_player_id,
            "target_player_id": target_player_id,
            "target_queue_id": target_queue_id,
        }

    async def _kick_player_transport(self, queue_id):
        kicked = False

        resume_response = await self._send_command_response(
            "player_queues/resume",
            queue_id=queue_id,
        )
        if resume_response is not None:
            logger.info("MASS transport kick: player_queues/resume accepted for %s", queue_id)
            kicked = True

        for player_id in await self._resolve_player_candidates(queue_id):
            play_response = await self._send_command_response(
                "players/cmd/play",
                player_id=player_id,
            )
            if play_response is not None:
                logger.info("MASS transport kick: players/cmd/play accepted for %s", player_id)
                kicked = True

        return kicked

    async def handle_command(self, cmd, data) -> dict:
        source_switch_stop = cmd == "transport_stop" and str(data.get("action") or "").strip().lower() == "stop"
        if not source_switch_stop:
            self._apply_playback_target_from_data(data)
        if cmd in {"transport_toggle", "transport_stop", "transport_next", "transport_previous"}:
            return await self._handle_transport_command(
                cmd,
                preferred_player="" if source_switch_stop else None,
            )
        if cmd == "transfer_queue":
            return await self._handle_transfer_queue_command(data)
        if cmd == "queue_remove":
            return await self._handle_queue_remove_command(data)
        if cmd == "queue_play_next":
            return await self._handle_queue_play_next_command(data)
        if cmd == "play_index":
            return await self._handle_queue_play_index_command(data)

        uri = self._resolve_command_url(data)
        if not uri:
            logger.warning("Unable to resolve URI for cmd=%s payload=%s", cmd, data)
            return {"state": "error", "reason": "unresolved_uri"}

        if cmd == "favorite_add":
            return await self._handle_favorite_add_command(data, uri)
        if cmd == "playlist_add":
            return await self._handle_playlist_add_command(data, uri)

        if self._forced_local_playback() and not self._local_player_ready():
            logger.warning("MASS local playback requested but player.type is not local.")
            return {"state": "error", "reason": "local_player_unavailable"}

        if self._should_try_local_playback():
            local_result = await self._handle_local_play_selection(cmd, data, uri)
            if local_result is not None:
                return local_result
            if self._forced_local_playback():
                return {"state": "error", "reason": "local_playback_unavailable", "uri": uri}

        self._reset_local_queue()

        queue_ids = await self._resolve_queue_candidates()
        if not queue_ids:
            logger.error("No MASS queue_id available for playback.")
            return {"state": "error", "reason": "missing_queue"}

        media_candidates = await self._resolve_media_candidates(uri)
        accepted_but_idle = False
        source_active = self._registered_state in {"playing", "paused"}

        for queue_id in queue_ids:
            before_snapshot = await self._get_queue_snapshot(queue_id)
            queue_state = self._extract_playback_state(before_snapshot)
            for option in self._options_for_request(cmd, uri, queue_state, source_active):
                accepted_response = None
                accepted_media = None

                for media in media_candidates:
                    logger.info(
                        "MASS playback cmd=%s queue=%s option=%s uri=%s media_type=%s",
                        cmd,
                        queue_id,
                        option,
                        uri,
                        type(media).__name__,
                    )
                    response = await self._send_command_response(
                        "player_queues/play_media",
                        queue_id=queue_id,
                        media=media,
                        option=option,
                        radio_mode=(cmd == "play_radio"),
                    )
                    if response is not None:
                        accepted_response = response
                        accepted_media = media
                        break

                if accepted_response is None:
                    continue

                if option in {"add", "next"}:
                    await asyncio.sleep(0.1)
                    latest_snapshot = await self._get_queue_snapshot(queue_id)
                    verified_enqueue = self._queue_has_enqueued(before_snapshot, latest_snapshot)
                    logger.info(
                        "MASS queue update accepted queue=%s option=%s uri=%s verified_enqueue=%s",
                        queue_id,
                        option,
                        uri,
                        verified_enqueue,
                    )
                    return {
                        "state": "queued",
                        "queue_id": queue_id,
                        "uri": uri,
                        "option": option,
                        "verified_enqueue": verified_enqueue,
                    }

                progressed, latest_snapshot = await self._wait_for_queue_progress(
                    queue_id,
                    before_snapshot,
                    require_active=False,
                    attempts=PLAYBACK_PRE_KICK_ATTEMPTS,
                    delay=PLAYBACK_PRE_KICK_DELAY,
                )
                latest_state = self._extract_playback_state(latest_snapshot)
                logger.info(
                    "MASS playback accepted queue=%s option=%s uri=%s media_type=%s state=%s progressed=%s",
                    queue_id,
                    option,
                    uri,
                    type(accepted_media).__name__,
                    latest_state or "idle",
                    progressed,
                )

                if self._is_active_state(latest_state):
                    published = await self._publish_now_playing(
                        queue_id,
                        data=data,
                        requested_uri=uri,
                        reason="track_change",
                        force_state="playing",
                    )
                    return {
                        "state": "playing",
                        "queue_id": queue_id,
                        "uri": (published or {}).get("uri", uri),
                        "option": option,
                        "verified_state": latest_state,
                    }

                transport_kicked = await self._kick_player_transport(queue_id)
                if transport_kicked:
                    active, active_snapshot = await self._wait_for_queue_progress(
                        queue_id,
                        latest_snapshot or before_snapshot,
                        require_active=True,
                        attempts=PLAYBACK_POST_KICK_ATTEMPTS,
                        delay=PLAYBACK_POST_KICK_DELAY,
                    )
                    active_state = self._extract_playback_state(active_snapshot)
                    if active:
                        logger.info(
                            "MASS playback verified after transport kick queue=%s state=%s uri=%s",
                            queue_id,
                            active_state or "unknown",
                            uri,
                        )
                        published = await self._publish_now_playing(
                            queue_id,
                            data=data,
                            requested_uri=uri,
                            reason="track_change",
                            force_state="playing",
                        )
                        return {
                            "state": "playing",
                            "queue_id": queue_id,
                            "uri": (published or {}).get("uri", uri),
                            "option": option,
                            "verified_state": active_state or "unknown",
                        }

                    if progressed and self._snapshot_has_loaded_media(active_snapshot or latest_snapshot):
                        logger.info(
                            "MASS playback verified by loaded queue queue=%s state=%s uri=%s",
                            queue_id,
                            active_state or latest_state or "loaded",
                            uri,
                        )
                        published = await self._publish_now_playing(
                            queue_id,
                            data=data,
                            requested_uri=uri,
                            reason="track_change",
                            force_state="playing",
                        )
                        return {
                            "state": "playing",
                            "queue_id": queue_id,
                            "uri": (published or {}).get("uri", uri),
                            "option": option,
                            "verified_state": active_state or latest_state or "loaded",
                        }

                    await asyncio.sleep(max(PLAYBACK_POST_KICK_DELAY, 0.6))
                    final_snapshot = await self._get_queue_snapshot(queue_id)
                    final_state = self._extract_playback_state(final_snapshot)
                    if self._is_active_state(final_state) or self._snapshot_has_loaded_media(final_snapshot):
                        logger.info(
                            "MASS playback verified after grace recheck queue=%s state=%s uri=%s",
                            queue_id,
                            final_state or "loaded",
                            uri,
                        )
                        published = await self._publish_now_playing(
                            queue_id,
                            data=data,
                            requested_uri=uri,
                            reason="track_change",
                            force_state="playing",
                        )
                        return {
                            "state": "playing",
                            "queue_id": queue_id,
                            "uri": (published or {}).get("uri", uri),
                            "option": option,
                            "verified_state": final_state or "loaded",
                        }

                logger.warning(
                    "MASS playback was accepted but transport stayed idle for queue=%s uri=%s",
                    queue_id,
                    uri,
                )
                accepted_but_idle = True
                break

        if accepted_but_idle:
            logger.error("MASS accepted playback but transport did not start for uri=%s payload=%s", uri, data)
            return {"state": "error", "reason": "transport_not_started", "uri": uri}

        logger.error("MASS playback rejected for uri=%s payload=%s", uri, data)
        return {"state": "error", "reason": "playback_rejected", "uri": uri}


if __name__ == '__main__':
    asyncio.run(MassSource().run())
