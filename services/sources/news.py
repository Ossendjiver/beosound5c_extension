#!/usr/bin/env python3
"""
BeoSound 5c News source (The Guardian API).

Fetches articles from The Guardian, groups them by section, and serves them
to the NEWS softarc frontend. The service also exposes:

- Play News shortcuts that call Home Assistant scripts
- Article TTS playback on configured Home Assistant media players
- Temporary WAV clip hosting for generated article narration

Config (config.json):
    "news": {
        "guardian_api_key": "YOUR_KEY",
        "play_news_scripts": {
            "link": "script.play_dynamic_morning_news",
            "cuisine": "script.play_dynamic_morning_news_cuisine_ma",
            "lounge": "script.play_dynamic_morning_news_lounge_ma"
        },
        "tts_targets": {
            "cuisine": "media_player.cuisine",
            "lounge": "media_player.lounge_mini_ma"
        },
        "tts_public_base_url": "http://beosound5c.local:8776"
    }

Port: 8776
"""

import asyncio
import io
import logging
import os
import re
import sys
import time
import wave

from aiohttp import web

sys.path.insert(0, "..")
sys.path.insert(0, ".")

from lib.config import cfg
from lib.source_base import SourceBase
from lib.tts import PIPER_BIN, PIPER_MODEL, _clean_audio, _piper_env

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [NEWS] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

GUARDIAN_API = "https://content.guardianapis.com/search"
REFRESH_INTERVAL = 15 * 60
CLIP_TTL_SECONDS = 15 * 60
MAX_TTS_CHARS = 6000

DEFAULT_PLAY_NEWS_SCRIPTS = {
    "play_news_link": "script.play_dynamic_morning_news",
    "play_news_cuisine": "script.play_dynamic_morning_news_cuisine_ma",
    "play_news_lounge": "script.play_dynamic_morning_news_lounge_ma",
}

PLAY_NEWS_SCRIPT_ALIASES = {
    "link": "play_news_link",
    "cuisine": "play_news_cuisine",
    "lounge": "play_news_lounge",
}

DEFAULT_TTS_TARGETS = {
    "cuisine": "media_player.cuisine",
    "lounge": "media_player.lounge_mini_ma",
}


def strip_html(text):
    return re.sub(r"<[^>]+>", "", text) if text else ""


def body_to_html(body_text):
    if not body_text:
        return ""
    paragraphs = body_text.strip().split("\n")
    return "".join(f"<p>{p.strip()}</p>" for p in paragraphs if p.strip())


def collapse_whitespace(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


class NewsService(SourceBase):
    id = "news"
    name = "News"
    port = 8776
    player = "local"
    action_map = {
        "go": "select",
        "up": "up",
        "down": "down",
        "left": "back",
        "right": "select",
    }

    def __init__(self):
        super().__init__()
        self._articles = []
        self._article_map = {}
        self._sections = []
        self._last_fetch = 0.0
        self._fetch_task = None
        self._api_key = ""
        self._ha_url = ""
        self._ha_token = ""
        self._play_news_scripts = dict(DEFAULT_PLAY_NEWS_SCRIPTS)
        self._tts_targets = dict(DEFAULT_TTS_TARGETS)
        self._tts_public_base_url = ""
        self._tts_clips = {}

    async def on_start(self):
        news_cfg = cfg("news", default={}) or {}
        self._api_key = str(cfg("news", "guardian_api_key", default="") or "").strip()
        if not self._api_key:
            log.info("No guardian_api_key in config; news source disabled")
            raise SystemExit(0)

        if isinstance(news_cfg.get("play_news_scripts"), dict):
            self._play_news_scripts.update({
                key: str(value).strip()
                for key, value in news_cfg.get("play_news_scripts", {}).items()
                if str(value or "").strip()
            })
        if isinstance(news_cfg.get("tts_targets"), dict):
            self._tts_targets.update({
                key: str(value).strip()
                for key, value in news_cfg.get("tts_targets", {}).items()
                if str(value or "").strip()
            })
        self._tts_public_base_url = str(news_cfg.get("tts_public_base_url") or "").strip().rstrip("/")
        self._ha_url = str(
            os.getenv("HA_URL")
            or cfg("home_assistant", "url", default="http://homeassistant.local:8123")
            or "http://homeassistant.local:8123"
        ).strip().rstrip("/")
        self._ha_token = os.getenv("HA_TOKEN", "").strip()

        log.info("Guardian API key configured, starting article fetch loop")
        await self.register("available")
        await self._refresh_content()
        self._fetch_task = self._spawn(self._refresh_loop(), name="news_refresh_loop")

    async def on_stop(self):
        if self._fetch_task:
            self._fetch_task.cancel()
        await self.register("gone")

    async def _refresh_loop(self):
        while True:
            try:
                await self._refresh_content()
            except asyncio.CancelledError:
                return
            except Exception as error:
                log.error("Fetch failed: %s", error)
            await asyncio.sleep(REFRESH_INTERVAL)

    async def _refresh_content(self):
        await self._fetch_articles()
        self._purge_expired_clips()

    async def _fetch_articles(self):
        log.info("Fetching articles from The Guardian...")
        params = {
            "show-fields": "trailText,bodyText,thumbnail",
            "page-size": 30,
            "api-key": self._api_key,
        }
        async with self._http_session.get(GUARDIAN_API, params=params) as resp:
            if resp.status != 200:
                log.error("Guardian API returned %d", resp.status)
                return
            data = await resp.json()

        results = data.get("response", {}).get("results", [])
        log.info("Got %d articles", len(results))

        self._articles = results
        self._article_map = {
            str(article.get("id") or "").strip(): article
            for article in results
            if str(article.get("id") or "").strip()
        }
        self._sections = self._group_by_section(results)
        self._last_fetch = time.time()

    def _group_by_section(self, articles):
        section_map = {}
        section_icons = {
            "world": "globe",
            "uk-news": "flag",
            "us-news": "flag",
            "politics": "scales",
            "environment": "leaf",
            "science": "flask",
            "technology": "cpu",
            "business": "chart-line-up",
            "sport": "football",
            "football": "football",
            "culture": "masks-theater",
            "music": "music-notes",
            "film": "film-slate",
            "books": "book-open",
            "tv-and-radio": "television",
            "artanddesign": "paint-brush",
            "stage": "masks-theater",
            "lifeandstyle": "heart",
            "fashion": "t-shirt",
            "food": "fork-knife",
            "travel": "airplane",
            "money": "currency-circle-dollar",
            "opinion": "chat-circle-text",
            "commentisfree": "chat-circle-text",
            "education": "graduation-cap",
            "society": "users-three",
            "media": "newspaper",
            "australia-news": "flag",
            "global-development": "globe-hemisphere-east",
        }
        section_colors = {
            "world": "#3498DB",
            "uk-news": "#E74C3C",
            "us-news": "#2ECC71",
            "politics": "#9B59B6",
            "environment": "#27AE60",
            "science": "#2980B9",
            "technology": "#4ECDC4",
            "business": "#F39C12",
            "sport": "#E67E22",
            "football": "#E67E22",
            "culture": "#8E44AD",
            "opinion": "#95A5A6",
            "commentisfree": "#95A5A6",
        }

        for article in articles:
            section_id = article.get("sectionId", "other")
            section_name = article.get("sectionName", "Other")

            if section_id not in section_map:
                section_map[section_id] = {
                    "id": f"sec-{section_id}",
                    "name": section_name,
                    "icon": section_icons.get(section_id, "newspaper"),
                    "color": section_colors.get(section_id, "#FF6348"),
                    "articles": [],
                    "children": [],
                }

            fields = article.get("fields", {}) or {}
            title = article.get("webTitle", "Untitled")
            trail = strip_html(fields.get("trailText", ""))
            body = fields.get("bodyText", "")
            thumbnail = fields.get("thumbnail", "")

            page_body = ""
            if trail:
                page_body += f"<p><em>{trail}</em></p>"
            page_body += body_to_html(body)

            item = {
                "id": article.get("id", ""),
                "name": title if len(title) <= 40 else title[:37] + "...",
                "page": {
                    "title": title,
                    "body": page_body,
                },
            }

            if thumbnail:
                item["image"] = thumbnail
            else:
                item["icon"] = "article"
                item["color"] = section_colors.get(section_id, "#FF6348")

            section_map[section_id]["articles"].append(item)

        sections = list(section_map.values())
        for section in sections:
            section["children"] = section["articles"]
        return sections

    def _cors_json(self, payload, *, status=200):
        return web.json_response(payload, status=status, headers=self._cors_headers())

    def _resolve_play_news_scripts(self):
        resolved = {}
        for raw_key, raw_value in (self._play_news_scripts or {}).items():
            value = str(raw_value or "").strip()
            if not value:
                continue
            key = str(raw_key or "").strip()
            normalized_key = PLAY_NEWS_SCRIPT_ALIASES.get(key, key)
            resolved[normalized_key] = value
        return resolved

    def _resolve_tts_targets(self):
        return {
            key: value
            for key, value in self._tts_targets.items()
            if str(value or "").strip()
        }

    async def _call_ha_service(self, domain, service, payload):
        headers = {"Content-Type": "application/json"}
        if self._ha_token:
            headers["Authorization"] = f"Bearer {self._ha_token}"

        url = f"{self._ha_url}/api/services/{domain}/{service}"
        async with self._http_session.post(url, headers=headers, json=payload) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise RuntimeError(f"HA service {domain}.{service} failed: HTTP {resp.status} {text[:300]}")
            return text

    def _purge_expired_clips(self):
        now = time.time()
        expired = [
            clip_id
            for clip_id, clip in self._tts_clips.items()
            if float(clip.get("expires_at") or 0) <= now
        ]
        for clip_id in expired:
            self._tts_clips.pop(clip_id, None)

    def _article_tts_text(self, article):
        fields = article.get("fields", {}) or {}
        title = collapse_whitespace(article.get("webTitle", ""))
        trail = collapse_whitespace(strip_html(fields.get("trailText", "")))
        body = collapse_whitespace(fields.get("bodyText", ""))
        parts = [part for part in (title, trail, body) if part]
        text = ". ".join(parts)
        text = collapse_whitespace(text)
        if len(text) <= MAX_TTS_CHARS:
            return text

        truncated = text[:MAX_TTS_CHARS].rsplit(". ", 1)[0].strip()
        if len(truncated) < 120:
            truncated = text[:MAX_TTS_CHARS].rsplit(" ", 1)[0].strip()
        return truncated or text[:MAX_TTS_CHARS].strip()

    async def _generate_tts_wav(self, text):
        piper = await asyncio.create_subprocess_exec(
            PIPER_BIN,
            "--model",
            PIPER_MODEL,
            "--output-raw",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=_piper_env(),
        )
        audio, _ = await piper.communicate(input=text.encode("utf-8"))
        if piper.returncode != 0 or not audio:
            raise RuntimeError(f"Piper failed with rc={piper.returncode}")

        cleaned = _clean_audio(audio)
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(22050)
            wav_file.writeframes(cleaned)
        return buffer.getvalue()

    async def _store_tts_clip(self, text):
        clip_id = f"{int(time.time())}-{os.urandom(4).hex()}"
        payload = await self._generate_tts_wav(text)
        self._tts_clips[clip_id] = {
            "payload": payload,
            "expires_at": time.time() + CLIP_TTL_SECONDS,
        }
        return clip_id

    def _clip_base_url(self, request):
        if self._tts_public_base_url:
            return self._tts_public_base_url
        return f"{request.scheme}://{request.host}"

    async def _trigger_play_news(self, action):
        script_entity = self._resolve_play_news_scripts().get(action, "")
        if not script_entity:
            raise RuntimeError(f"Unsupported Play News action: {action}")
        await self._call_ha_service("script", "turn_on", {"entity_id": script_entity})
        return {
            "status": "ok",
            "action": action,
            "script": script_entity,
        }

    async def _read_article_aloud(self, request, data):
        article_id = str(data.get("article_id") or data.get("id") or "").strip()
        target_key = str(data.get("target") or "").strip().lower()
        target_entity = self._resolve_tts_targets().get(target_key, "")
        if not article_id:
            raise RuntimeError("Missing article_id")
        if not target_entity:
            raise RuntimeError(f"Unsupported TTS target: {target_key or 'empty'}")

        article = self._article_map.get(article_id)
        if not article:
            raise RuntimeError(f"Article not found: {article_id}")

        text = self._article_tts_text(article)
        if not text:
            raise RuntimeError("Article has no readable body text")

        clip_id = await self._store_tts_clip(text)
        media_url = f"{self._clip_base_url(request)}/tts/{clip_id}.wav"
        await self._call_ha_service(
            "media_player",
            "play_media",
            {
                "entity_id": target_entity,
                "media_content_id": media_url,
                "media_content_type": "music",
            },
        )

        return {
            "status": "ok",
            "action": "read_article",
            "article_id": article_id,
            "target": target_key,
            "entity_id": target_entity,
            "media_url": media_url,
        }

    def add_routes(self, app):
        app.router.add_get("/articles", self._handle_articles)
        app.router.add_post("/action", self._handle_action)
        app.router.add_options("/action", self._handle_action)
        app.router.add_get("/tts/{clip_id}.wav", self._handle_tts_clip)

    async def _handle_articles(self, request):
        return self._cors_json(self._sections)

    async def _handle_action(self, request):
        if request.method == "OPTIONS":
            return web.Response(headers=self._cors_headers())

        try:
            data = await request.json()
        except Exception:
            return self._cors_json({"status": "error", "message": "Invalid JSON"}, status=400)

        action = str((data or {}).get("action") or "").strip()
        try:
            if action in self._resolve_play_news_scripts():
                payload = await self._trigger_play_news(action)
            elif action == "read_article":
                payload = await self._read_article_aloud(request, data or {})
            else:
                return self._cors_json(
                    {"status": "error", "message": f"Unsupported action: {action or 'empty'}"},
                    status=400,
                )
            return self._cors_json(payload)
        except Exception as error:
            log.error("Action failed (%s): %s", action or "empty", error)
            return self._cors_json(
                {"status": "error", "message": str(error), "action": action},
                status=500,
            )

    async def _handle_tts_clip(self, request):
        self._purge_expired_clips()
        clip_id = str(request.match_info.get("clip_id") or "").strip()
        clip = self._tts_clips.get(clip_id)
        if not clip:
            return web.Response(status=404, text="Clip not found", headers=self._cors_headers())

        return web.Response(
            body=clip["payload"],
            content_type="audio/wav",
            headers={
                **self._cors_headers(),
                "Cache-Control": "no-store",
            },
        )

    async def handle_status(self):
        return {
            "source": self.id,
            "name": self.name,
            "article_count": len(self._articles),
            "section_count": len(self._sections),
            "last_fetch": self._last_fetch,
            "api_key_set": bool(self._api_key),
            "play_news_scripts": self._resolve_play_news_scripts(),
            "tts_targets": self._resolve_tts_targets(),
        }

    async def handle_resync(self):
        await self.register("available")
        return {"status": "ok", "resynced": True}

    async def handle_command(self, cmd, data):
        if cmd == "refresh":
            await self._refresh_content()
            return {"refreshed": True, "article_count": len(self._articles)}
        return {}


if __name__ == "__main__":
    service = NewsService()
    asyncio.run(service.run())
