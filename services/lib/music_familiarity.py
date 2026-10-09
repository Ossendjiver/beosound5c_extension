"""Small, read-only familiarity prior, separate from deliberate listening rewards.

Provider history is evidence of familiarity, not a preference or mood label.
Counters are frozen on first positive observation and overlap is taken by max,
so polling and automatic mixes cannot accumulate rewards in this prior.
"""
from __future__ import annotations

import asyncio
import math
import re
import time
from typing import Any

MAX_BOOST = .25
MAX_ITEMS = 1000
RETENTION = 180 * 86400
HISTORY_ID = re.compile(r"(?:^|[:/_-])(?:recently[-_]played|listening[-_]history|play[-_]history)(?:$|[:/_-])", re.I)


def number(value: Any) -> float:
    if isinstance(value, bool): return 0.0
    try:
        n = float(value)
        return n if math.isfinite(n) and n > 0 else 0.0
    except (TypeError, ValueError): return 0.0


def identity(item: dict) -> tuple[str, str]:
    artists = item.get("artists") or []
    artist = item.get("artist") or (artists[0].get("name") if artists and isinstance(artists[0], dict) else "")
    return str(artist or "").strip().casefold(), str(item.get("name") or item.get("title") or "").strip().casefold()


def aliases(item: dict) -> set[str]:
    values = {str(item.get("uri") or "")}
    for mapping in item.get("provider_mappings") or []:
        if not isinstance(mapping, dict): continue
        for provider in {mapping.get("provider_instance"), mapping.get("provider_domain")} - {None, ""}:
            if mapping.get("item_id"):
                values.add(f"{provider}://track/{mapping['item_id']}")
    return values - {""}


def track(item: Any) -> dict | None:
    if not isinstance(item, dict) or item.get("media_type") != "track" or not item.get("uri"):
        return None
    if item.get("available") is False or item.get("is_playable") is False: return None
    artist, name = identity(item)
    if not artist or not name: return None
    artists = item.get("artists") or []
    return {"uri": item["uri"], "name": str(item.get("name") or item.get("title")),
            "artist": str(item.get("artist") or artists[0].get("name")),
            "duration": number(item.get("duration")), "media_type": "track",
            "provider_mappings": item.get("provider_mappings") or [],
            "genres": (item.get("metadata") or {}).get("genres") or []}


def observe(cache: dict, item: dict, source: str, *, count=0, rank=0, history=False, now=None) -> None:
    media = track(item)
    count, rank = number(count), min(1.0, number(rank))
    if not media or not (count or rank or history): return
    now = time.time() if now is None else now
    entry = cache.setdefault(media["uri"], {"item": media, "signals": {}})
    # Refresh descriptive metadata, but not the initial evidence timestamp/counter.
    entry["item"] = media
    entry["signals"].setdefault(source, {"count": count, "rank": rank,
        "history": bool(history), "observed_at": now})


class Prior:
    """Index one snapshot once per ranking pass, including overlapping identities."""
    def __init__(self, cache: dict, local_history: list, now=None):
        self.now = time.time() if now is None else now
        self.cache = cache
        self.uris, self.keys, self.local_uris, self.local_keys = {}, {}, {}, {}
        for uri, entry in cache.items():
            item = entry.get("item", {})
            for alias in aliases(item): self.uris.setdefault(alias, set()).add(uri)
            key = identity(item)
            if key[0] and key[1]: self.keys.setdefault(key, set()).add(uri)
        for index, row in enumerate(local_history):
            if row["seconds"] < 90 and row["reward"] <= 0: continue
            self.local_uris.setdefault(row["uri"], set()).add(index)
            key = (row["artist"].strip().casefold(), row["title"].strip().casefold())
            if key[0] and key[1]: self.local_keys.setdefault(key, set()).add(index)

    def boost(self, item: dict) -> float:
        key, uris = identity(item), aliases(item)
        matched = set(self.keys.get(key, ()))
        local = set(self.local_keys.get(key, ()))
        for uri in uris:
            matched.update(self.uris.get(uri, ()))
            local.update(self.local_uris.get(uri, ()))
        scores = []
        for uri in matched:
            for signal in self.cache[uri].get("signals", {}).values():
                age = self.now - number(signal.get("observed_at"))
                if age < 0 or age > RETENTION: continue
                # All local plays, including automatic ones, subtract from the prior.
                count = max(0, number(signal.get("count")) - len(local))
                score = MAX_BOOST * min(1, math.log1p(count) / math.log(51))
                if not local:
                    score = max(score, .08 * number(signal.get("rank")), .10 if signal.get("history") else 0)
                scores.append(score * .5 ** (age / (90 * 86400)))
        return min(MAX_BOOST, max(scores, default=0.0))


def boost(item: dict, cache: dict, local_history: list, now=None) -> float:
    return Prior(cache, local_history, now).boost(item)


async def collect(command, previous: dict, now=None) -> tuple[dict, dict]:
    """Bounded snapshot import. Only explicit personal-history folders are opened."""
    now = time.time() if now is None else now
    cache = {uri: {"item": dict(e.get("item", {})), "signals": dict(e.get("signals", {}))}
             for uri, e in previous.items() if isinstance(e, dict)}
    status = {"ma_count_items": 0, "provider_history_items": 0, "unavailable": []}
    deadline = time.monotonic() + 60

    async def read(name, **args):
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0: raise TimeoutError("Snapshot time budget exhausted")
            data = await asyncio.wait_for(command(name, args), timeout=min(12, remaining))
            if not isinstance(data, list): raise ValueError("Expected MA list")
            return [x for x in data if isinstance(x, dict)]
        except asyncio.CancelledError: raise
        except Exception:
            status["unavailable"].append(name)
            return []

    rows = await read("music/tracks/library_items", limit=500, offset=0, order_by="play_count_desc")
    for index, item in enumerate(rows):
        # Never infer a play from an unplayed row merely because it appears in the list.
        count = number(item.get("play_count")) if item.get("provider") == "library" else 0
        played = item.get("provider") == "library" and number(item.get("last_played")) > 0
        if count or played:
            observe(cache, item, "ma:count" if count else "ma:rank", count=count, rank=(len(rows)-index)/max(1,len(rows)) if played and not count else 0, now=now)
            status["ma_count_items"] += 1
    providers = await read("providers")
    for provider in [p for p in providers if p.get("type") == "music" and p.get("available")
                     and "browse" in p.get("supported_features", [])][:8]:
        instance = str(provider.get("instance_id") or "")
        if not instance: continue
        prefix = instance + "://"
        roots = await read("music/browse", path=prefix)
        recommendations = [x for x in roots if x.get("item_id") == "recommendations" and str(x.get("path", "")).startswith(prefix)]
        if recommendations: roots += await read("music/browse", path=recommendations[0]["path"])
        folders = [x for x in roots if x.get("media_type") == "folder" and HISTORY_ID.search(str(x.get("item_id", "")))
                   and str(x.get("path", "")).startswith(prefix)]
        for folder in folders[:2]:
            items = await read("music/browse", path=folder["path"])
            expanded = [x for x in items if x.get("media_type") == "track"]
            # SoundCloud exposes Recently Played as a dynamic history playlist.
            for playlist in [x for x in items if x.get("media_type") == "playlist" and x.get("provider") in {instance, provider.get("domain")}][:2]:
                expanded += await read("music/playlists/playlist_tracks", item_id=playlist["item_id"],
                                       provider_instance_id_or_domain=instance)
            for item in expanded[:200]:
                # Public popularity/playback_count is never a personal listening count.
                personal = max(number(item.get("user_play_count")), number(item.get("personal_play_count")))
                observe(cache, item, "provider:" + instance, count=personal, history=True, now=now)
                if track(item): status["provider_history_items"] += 1
    cache = {uri: e for uri, e in cache.items() if any(
        0 <= now - number(s.get("observed_at")) <= RETENTION for s in e["signals"].values())}
    if len(cache) > MAX_ITEMS:
        ordered = sorted(cache, key=lambda uri: max(number(s.get("observed_at")) for s in cache[uri]["signals"].values()), reverse=True)
        cache = {uri: cache[uri] for uri in ordered[:MAX_ITEMS]}
    return cache, status
