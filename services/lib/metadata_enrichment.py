"""Conservative metadata recovery for the unified Playing views.

The normal source payload remains authoritative.  This module is only used
when artwork is missing (or is one of the application's known placeholders),
and only fills fields which are themselves missing.  Candidates are queried
in the user-visible order: SHOWING, Music Assistant, the active provider, then
other registered local providers.  Every candidate must identify the same
item before it is allowed to contribute data.
"""

from __future__ import annotations

import asyncio
import re
import urllib.parse
from typing import Any

import aiohttp


_ACTIVE_STATES = {"playing", "paused", "buffering", "transitioning"}
_EMPTY_TEXT = {
    "", "-", "–", "—", "â€”", "unknown", "unknown artist",
    "unknown album", "not available", "unavailable", "now playing",
}
_PLACEHOLDER_ART_MARKERS = (
    "r0lgodlhaqabaiaa",          # transparent 1px GIF
    "phn2zyb3awr0ad0injqi",      # MASS generic 64px music-note SVG
    "viewbox='0 0 200 200'",    # built-in silent-vinyl SVG
    'viewbox="0 0 200 200"',
)


def meaningful_text(value: Any) -> bool:
    return str(value or "").strip().casefold() not in _EMPTY_TEXT


def valid_artwork(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    lowered = text.casefold().replace("%20", " ")
    if any(marker in lowered for marker in _PLACEHOLDER_ART_MARKERS):
        return False
    return text.startswith(("http://", "https://", "/", "data:image/"))


def _identity(value: Any) -> str:
    text = str(value or "").casefold()
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def same_item(left: dict, right: dict, *, allow_title_only: bool = False) -> bool:
    """Return True only when two metadata records identify the same item."""
    left_uri = str(left.get("uri") or left.get("track_uri") or "").strip()
    right_uri = str(right.get("uri") or right.get("track_uri") or "").strip()
    if left_uri and right_uri and left_uri == right_uri:
        return True

    left_title = _identity(left.get("title") or left.get("name"))
    right_title = _identity(right.get("title") or right.get("name"))
    if not left_title or left_title != right_title:
        return False

    supporting_comparisons = []
    for key in ("artist", "album"):
        a = _identity(left.get(key))
        b = _identity(right.get(key))
        if a and b:
            supporting_comparisons.append(a == b)
    if supporting_comparisons:
        return any(supporting_comparisons)
    return allow_title_only


def preserve_same_item_fields(payload: dict, previous: dict | None) -> dict:
    """Keep good fields across partial updates for exactly the same item."""
    if not previous or not same_item(payload, previous, allow_title_only=True):
        return payload
    merged = dict(payload)
    for field in ("title", "artist", "album"):
        if not meaningful_text(merged.get(field)) and meaningful_text(previous.get(field)):
            merged[field] = previous[field]
    for field in ("artwork", "back_artwork"):
        if not valid_artwork(merged.get(field)) and valid_artwork(previous.get(field)):
            merged[field] = previous[field]
    if not merged.get("artwork_candidates") and previous.get("artwork_candidates"):
        merged["artwork_candidates"] = list(previous["artwork_candidates"])
    return merged


def _absolute_artwork(value: Any, base_url: str) -> str:
    text = str(value or "").strip()
    if not valid_artwork(text):
        return ""
    if text.startswith(("http://", "https://", "data:")):
        return text
    return urllib.parse.urljoin(base_url.rstrip("/") + "/", text.lstrip("/"))


def _candidate_from_mapping(data: Any, base_url: str, source: str) -> dict | None:
    if not isinstance(data, dict):
        return None
    media_item = data.get("media_item") if isinstance(data.get("media_item"), dict) else {}
    title = data.get("title") or data.get("name") or media_item.get("name") or ""
    artist = (
        data.get("artist") or data.get("artist_str")
        or media_item.get("artist") or media_item.get("artist_str") or ""
    )
    if isinstance(artist, dict):
        artist = artist.get("name") or ""
    album = data.get("album") or data.get("album_name") or media_item.get("album") or ""
    if isinstance(album, dict):
        album = album.get("name") or ""
    if _identity(album) and _identity(album) == _identity(title):
        album = ""
    artwork = (
        data.get("artwork") or data.get("artwork_url") or data.get("image")
        or media_item.get("artwork") or media_item.get("image") or ""
    )
    if isinstance(artwork, dict):
        artwork = artwork.get("url") or artwork.get("path") or ""
    return {
        "title": str(title or "").strip(),
        "artist": str(artist or "").strip(),
        "album": str(album or "").strip(),
        "artwork": _absolute_artwork(artwork, base_url),
        "uri": str(data.get("uri") or data.get("url") or media_item.get("uri") or "").strip(),
        "state": str(data.get("state") or "").strip().lower(),
        "source": source,
    }


def _current_from_queue(data: Any, base_url: str, source: str) -> dict | None:
    if not isinstance(data, dict):
        return None
    tracks = data.get("tracks")
    if isinstance(tracks, list) and tracks:
        current_index = data.get("current_index", -1)
        try:
            current_index = int(current_index)
        except (TypeError, ValueError):
            current_index = -1
        current = next((item for item in tracks if isinstance(item, dict) and item.get("current")), None)
        if current is None and 0 <= current_index < len(tracks):
            current = tracks[current_index]
        if current is not None:
            candidate = _candidate_from_mapping(current, base_url, source)
            if candidate:
                candidate["state"] = str(data.get("state") or candidate.get("state") or "").lower()
            return candidate
    current = data.get("current_item")
    if isinstance(current, dict):
        return _candidate_from_mapping(current, base_url, source)
    return None


class MetadataEnricher:
    """Fetch and merge same-item fallback metadata without replacing truth."""

    def __init__(self, session: aiohttp.ClientSession):
        self._session = session

    async def _json(self, url: str, *, params: dict | None = None) -> dict | list | None:
        try:
            async with self._session.get(
                url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=1.5),
            ) as response:
                if response.status != 200:
                    return None
                return await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return None

    async def _showing(self) -> list[dict]:
        data = await self._json("http://127.0.0.1:8767/appletv")
        candidate = _candidate_from_mapping(data, "http://127.0.0.1:8767", "showing")
        if not candidate or candidate.get("state") not in _ACTIVE_STATES:
            return []
        return [candidate]

    async def _mass(self, payload: dict, hinted_uri: str) -> list[dict]:
        base = "http://127.0.0.1:8783"
        results: list[dict] = []
        if hinted_uri:
            item = await self._json(f"{base}/item_info", params={"uri": hinted_uri})
            candidate = _candidate_from_mapping(item, base, "mass")
            if candidate:
                results.append(candidate)
        queue, now = await asyncio.gather(
            self._json(f"{base}/queue"),
            self._json(f"{base}/now_playing"),
        )
        for candidate in (
            _current_from_queue(queue, base, "mass"),
            _candidate_from_mapping(now, base, "mass"),
        ):
            if candidate:
                results.append(candidate)
        title = str(payload.get("title") or "").strip()
        if title:
            search = await self._json(f"{base}/search", params={"q": title, "limit": 8})
            groups = search.get("groups") if isinstance(search, dict) else search
            if isinstance(groups, list):
                for group in groups:
                    items = group.get("items") if isinstance(group, dict) else None
                    if not isinstance(items, list):
                        continue
                    for item in items:
                        candidate = _candidate_from_mapping(item, base, "mass_search")
                        if candidate:
                            results.append(candidate)
        return results

    async def _provider(self, source: dict) -> list[dict]:
        base = str(source.get("base_url") or "").rstrip("/")
        source_id = str(source.get("id") or "provider")
        if not base:
            return []
        now, queue = await asyncio.gather(
            self._json(f"{base}/now_playing"),
            self._json(f"{base}/queue"),
        )
        return [
            candidate for candidate in (
                _candidate_from_mapping(now, base, source_id),
                _current_from_queue(queue, base, source_id),
            ) if candidate
        ]

    async def enrich(
        self,
        payload: dict,
        *,
        hinted_uri: str = "",
        source_id: str = "",
        sources: list[dict] | None = None,
    ) -> dict:
        """Return an enriched copy, never replacing meaningful source fields."""
        result = dict(payload)
        if valid_artwork(result.get("artwork")):
            return result
        if str(result.get("state") or "").lower() not in _ACTIVE_STATES:
            return result

        source_records = list(sources or [])
        active_provider = next((s for s in source_records if s.get("id") == source_id), None)
        other_providers = [
            s for s in source_records
            if s.get("id") not in {source_id, "mass"} and s.get("base_url")
        ]

        showing_task = self._showing()
        mass_task = self._mass(result, hinted_uri)
        provider_task = self._provider(active_provider) if active_provider and source_id != "mass" else asyncio.sleep(0, result=[])
        other_tasks = [self._provider(source) for source in other_providers]
        showing, mass, provider, *others = await asyncio.gather(
            showing_task, mass_task, provider_task, *other_tasks,
        )

        ordered_groups = [showing, mass, provider, [item for group in others for item in group]]
        accepted: list[dict] = []
        for group_index, group in enumerate(ordered_groups):
            for candidate in group:
                # Active source/MASS may fill an otherwise unidentified record;
                # cross-provider fallbacks always require strict identity.
                allow_title_only = group_index < 3
                if same_item(result, candidate, allow_title_only=allow_title_only):
                    accepted.append(candidate)

        artwork_candidates: list[str] = []
        for candidate in accepted:
            artwork = candidate.get("artwork")
            if valid_artwork(artwork) and artwork not in artwork_candidates:
                artwork_candidates.append(artwork)

        if artwork_candidates:
            result["artwork"] = artwork_candidates[0]
            result["artwork_candidates"] = artwork_candidates
        for field in ("title", "artist", "album"):
            if meaningful_text(result.get(field)):
                continue
            replacement = next(
                (candidate.get(field) for candidate in accepted if meaningful_text(candidate.get(field))),
                "",
            )
            if replacement:
                result[field] = replacement
        if accepted:
            result["metadata_sources"] = list(dict.fromkeys(
                candidate.get("source") for candidate in accepted if candidate.get("source")
            ))
        return result
