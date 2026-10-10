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
from collections import Counter
from .reference_plays import weights
from typing import Any

MAX_BOOST = .25
MAX_ITEMS = 5000
MOST_PLAYED = re.compile(r"^most\s+played\b", re.I)
RETENTION = 180 * 86400
HISTORY_ID = re.compile(r"(?:^|[:/_-])(?:recently[-_]played|listening[-_]history|play[-_]history)(?:$|[:/_-])", re.I)


def most_played(name: Any) -> bool:
    return bool(MOST_PLAYED.match(str(name or "").strip()))


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


def observe(cache: dict, item: dict, source: str, *, count=0, rank=0, history=False, baseline=False, now=None) -> None:
    media = track(item)
    count, rank = number(count), min(1.0, number(rank))
    if not media or not (count or rank or history or baseline): return
    now = time.time() if now is None else now
    entry = cache.setdefault(media["uri"], {"item": media, "signals": {}})
    # Refresh descriptive metadata, but not the initial evidence timestamp/counter.
    entry["item"] = media
    entry["signals"].setdefault(source, {"count": count, "rank": rank,
        "history": bool(history), "baseline": bool(baseline), "observed_at": now})


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

    def baseline(self, item: dict) -> bool:
        matches = set(self.keys.get(identity(item), ()))
        for uri in aliases(item): matches.update(self.uris.get(uri, ()))
        return any(signal.get("baseline") and 0 <= self.now - number(signal.get("observed_at")) <= RETENTION
                   for uri in matches for signal in self.cache[uri].get("signals", {}).values())

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

    def describe(self, item: dict) -> dict:
        """Expose novelty independently of trusted playlist membership."""
        key, uris = identity(item), aliases(item)
        local = set(self.local_keys.get(key, ()))
        matched = set(self.keys.get(key, ()))
        for uri in uris:
            local.update(self.local_uris.get(uri, ()))
            matched.update(self.uris.get(uri, ()))
        signals = [s for uri in matched for s in self.cache[uri].get('signals', {}).values()
                   if 0 <= self.now-number(s.get('observed_at')) <= RETENTION]
        heard = bool(local or any(number(s.get('count')) or s.get('history') for s in signals))
        provenance = sorted({source for uri in matched for source in self.cache[uri].get('signals', {})})
        return {'recording_heard': heard, 'local_observations': len(local),
                'strength': min(1., self.boost(item)/MAX_BOOST),
                'sources': provenance[:8], 'imported_reference': any(s.startswith(('most-played:', 'reference-rank')) for s in provenance)}


def boost(item: dict, cache: dict, local_history: list, now=None) -> float:
    return Prior(cache, local_history, now).boost(item)


async def collect(command, previous: dict, now=None) -> tuple[dict, dict]:
    """Bounded snapshot import. Only explicit personal-history folders are opened."""
    now = time.time() if now is None else now
    cache = {uri: {"item": dict(e.get("item", {})), "signals": dict(e.get("signals", {}))}
             for uri, e in previous.items() if isinstance(e, dict)}
    status = {"ma_count_items": 0, "provider_history_items": 0, "baseline_playlists": 0, "baseline_tracks": 0, "unavailable": []}
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
    # Saved personal History Mix playlists are explicit evidence of familiarity.
    # Membership is not a numeric play count or a time/mood training observation.
    playlists = []
    for offset in range(0, 2000, 100):
        page = await read("music/playlists/library_items", limit=100, offset=offset)
        playlists.extend(x for x in page if most_played(x.get("name")))
        if len(page) < 100: break
    reference_counts = Counter()
    reference_items = {}
    for playlist in playlists[:40]:
        provider = playlist.get("provider")
        item_id = playlist.get("item_id")
        if not provider or not item_id: continue
        imported = False
        # MA's controller returns the complete playlist; page is not pagination
        # here (some versions accept it but return the same list again).
        items = await read("music/playlists/playlist_tracks", item_id=item_id,
                           provider_instance_id_or_domain=provider)
        for item, rank_count in zip(items[:1000], weights(len(items))):
            if track(item):
                reference_counts[item["uri"]] += rank_count
                reference_items[item["uri"]] = item
                observe(cache, item, "most-played:" + str(playlist.get("uri") or f"{provider}:{item_id}"),
                        history=True, baseline=True, now=now)
                status["baseline_tracks"] += 1
                imported = True
        status["baseline_playlists"] += int(imported)
        if time.monotonic() >= deadline: break
    for uri, count in reference_counts.items():
        observe(cache, reference_items[uri], "reference-rank", count=count, baseline=True, now=now)
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
