"""Optional NewPipe search and explicit Samsung Tube routing for MASS search."""
from __future__ import annotations

import asyncio
import copy
import re
from urllib.parse import urlencode, urlsplit
from aiohttp import ClientSession, ClientTimeout

VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
VIDEO_URI = re.compile(r"youtube://video/([A-Za-z0-9_-]{11})\Z")


def enabled(value):
    return value is True or str(value).lower() in {"true", "on", "1"}


def search_flag(value):
    if value is None:
        return None
    if str(value).lower() not in {"true", "false", "on", "off", "1", "0"}:
        raise ValueError("Search switches must be on or off")
    return enabled(value)


class YouTubeSearch:
    def __init__(self, config, default_bridge, ha_url="", ha_token=""):
        self.config = config or {}
        self.bridge = str(self.config.get("bridge_url") or default_bridge).rstrip("/")
        self.ha_url = ha_url.rstrip("/")
        self.ha_token = ha_token

    def flags(self, music=None, videos=None):
        return (enabled(self.config.get("music_enabled", False)) if music is None else bool(music),
                enabled(self.config.get("videos_enabled", False)) if videos is None else bool(videos))

    def options(self):
        music, videos = self.flags()
        return {"youtube_music": music, "youtube_videos": videos}

    async def _bridge_json(self, path, timeout=45):
        parsed = urlsplit(self.bridge)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
            raise ValueError("Invalid NewPipe bridge base URL")
        async with ClientSession(timeout=ClientTimeout(total=timeout)) as session:
            async with session.get(self.bridge + path) as response:
                response.raise_for_status()
                return await response.json()

    async def search(self, query, kind, limit):
        payload = await self._bridge_json("/search?" + urlencode({"q": query, "kind": kind}), timeout=15)
        items = []
        seen = set()
        for raw in payload.get("items", []):
            if not isinstance(raw, dict):
                continue
            video_id = str(raw.get("video_id") or "")
            title = str(raw.get("title") or "").strip()
            if not VIDEO_ID.fullmatch(video_id) or not title or video_id in seen:
                continue
            seen.add(video_id)
            video = kind == "video"
            items.append({
                "id": f"youtube:{kind}:{video_id}", "item_id": video_id, "video_id": video_id,
                "name": title, "artist": str(raw.get("channel") or ""),
                "image": str(raw.get("artwork") or ""), "duration": raw.get("duration", 0),
                "url": f"youtube://video/{video_id}" if video else self.bridge + "/audio/" + video_id,
                "media_type": "video" if video else "track", "provider": "youtube",
                "youtube_video": video, "live_search": True,
                "subtitle": str(raw.get("channel") or "") + (" · Samsung Frame" if video else " · YouTube Music"),
            })
            if len(items) >= limit:
                break
        if not items:
            return None
        return {"id": f"search_youtube_{kind}", "name": "YouTube videos" if kind == "video" else "YouTube music",
                "tracks": items, "live_search": True}

    def audio_id(self, uri):
        prefix = self.bridge + "/audio/"
        if not str(uri).startswith(prefix):
            return None
        value = str(uri)[len(prefix):]
        return value if VIDEO_ID.fullmatch(value) else None

    async def audio_item(self, uri, mass_command):
        video_id = self.audio_id(uri)
        if not video_id:
            return None
        metadata = await self._bridge_json("/metadata/" + video_id)
        raw = await mass_command("music/item_by_uri", uri=uri)
        if not isinstance(raw, dict) or raw.get("provider") != "builtin" or raw.get("media_type") != "track" or not raw.get("duration"):
            raise ValueError("YouTube audio did not resolve as a finite MA track")
        result = copy.deepcopy(raw)
        result["name"] = metadata.get("title") or raw.get("name")
        result.pop("sort_name", None)
        image = metadata.get("artwork")
        if image:
            result.setdefault("metadata", {})["images"] = [{"type": "thumb", "path": image, "provider": "builtin", "remotely_accessible": True}]
        return result

    async def _ha(self, method, path, data=None):
        if not self.ha_url or not self.ha_token:
            raise ValueError("Home Assistant URL/token required for Frame video")
        async with ClientSession(timeout=ClientTimeout(total=15), headers={"Authorization": "Bearer " + self.ha_token}) as session:
            async with session.request(method, self.ha_url + "/api/" + path, json=data) as response:
                response.raise_for_status()
                return await response.json()

    async def play_video(self, uri):
        match = VIDEO_URI.fullmatch(str(uri))
        if not match:
            raise ValueError("Invalid YouTube video selection")
        physical = str(self.config.get("frame_entity") or "").strip()
        playback = str(self.config.get("playback_entity") or "").strip()
        if not re.fullmatch(r"media_player\.[a-z0-9_]+", physical) or not re.fullmatch(r"media_player\.[a-z0-9_]+", playback) or physical == playback:
            raise ValueError("Configure separate Frame and YouTube on TV entities")
        app = str(self.config.get("app_id") or "tUb3Xq7Lm9.Tube")
        state = await self._ha("GET", "states/" + physical)
        if state.get("state") in {"off", "unavailable", "unknown"} or state.get("attributes", {}).get("art_mode_status") == "on":
            await self._ha("POST", "services/media_player/turn_on", {"entity_id": physical})
        await self._ha("POST", "services/media_player/play_media", {"entity_id": physical, "media_content_id": app, "media_content_type": "app"})
        await asyncio.sleep(3)
        await self._ha("POST", "services/media_player/play_media", {"entity_id": playback, "media_content_id": match[1], "media_content_type": "video", "enqueue": "play"})
        return {"state": "video_sent", "target": "Samsung Frame", "video_id": match[1]}
