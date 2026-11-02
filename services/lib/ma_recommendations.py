"""Read-only MA similar-track candidates; the local selector alone owns the queue.

Requests only read a bounded cache and schedule background work. Provider results
are suggestions, never favourites, mood labels, listens or permission to play.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from email.utils import parsedate_to_datetime
import re
import time

import aiohttp

from . import music_features

MAX_SEEDS = 64
MAX_TRACKS = 40
FRESH_SECONDS = 6 * 3600
STALE_SECONDS = 7 * 86400
RETRY_SECONDS = 15 * 60


def track_ref(uri):
    if not isinstance(uri, str) or not re.fullmatch(r'[\w-]{1,128}://track/[^\s\x00-\x1f]{1,512}', uri):
        return None
    provider, _, item = uri.partition('://track/')
    if provider in ('http', 'https', 'file'):
        return None
    return provider, item


def normalise(item):
    """Keep descriptors and recording identity, discard provider popularity/trust."""
    if not isinstance(item, dict) or not track_ref(item.get('uri')):
        return None
    if item.get('media_type', 'track') != 'track' or item.get('available') is False or item.get('is_playable') is False:
        return None
    title = str(item.get('name') or item.get('title') or '').strip()[:512]
    artist = music_features.artist_name(item).strip()[:512]
    duration = music_features.number(item.get('duration'), 1, 86400)
    if not title or not artist or duration is None:
        return None
    result = {'uri': item['uri'], 'name': title, 'artist': artist, 'duration': duration,
              'media_type': 'track', 'favorite': False, 'trusted': False}
    for key in ('version', 'isrc'):
        if isinstance(item.get(key), str):
            result[key] = item[key][:256]
    album = item.get('album')
    if album:
        result['album'] = str(album.get('name') or '')[:512] if isinstance(album, dict) else str(album)[:512]
    # Do not persist embeddings or opaque provider payloads in the candidate cache.
    # Exact library/profile aliases hydrate the richer local analysis at read time.
    meta = music_features.metadata(item)
    for key in ('genres', 'genre', 'mood_tags'):
        value = meta.get(key)
        if isinstance(value, str):
            result[key] = value[:512]
        elif isinstance(value, list):
            result[key] = [str(v)[:128] for v in value[:24] if isinstance(v, str)]
    return result


def aliases(item):
    result = {item.get('uri')} if track_ref(item.get('uri')) else set()
    result.update(u for u in item.get('aliases') or [] if track_ref(u))
    for mapping in item.get('provider_mappings') or []:
        if not isinstance(mapping, dict) or mapping.get('available') is False:
            continue
        uri = f"{mapping.get('provider_instance', '')}://track/{mapping.get('item_id', '')}"
        if track_ref(uri):
            result.add(uri)
    return result


class Candidates:
    def __init__(self, command, *, load=None, save=None, clock=time.time, enabled=True):
        self.command, self.save, self.clock, self.enabled = command, save, clock, enabled
        self.cache = OrderedDict()
        self.pending = OrderedDict()
        self.retry = OrderedDict()
        self.cooldown = 0.
        self.last_error = None
        self.last_refreshed_seed = None
        self.dirty = False
        self.last_save = self.clock()
        data = load() if load else {}
        if isinstance(data, dict) and data.get('version') == 1 and isinstance(data.get('seeds'), dict):
            self.cooldown = music_features.number(data.get('cooldown'), 0, self.clock() + 86400) or 0.
            for uri, entry in list(data['seeds'].items())[-MAX_SEEDS:]:
                if not track_ref(uri) or not isinstance(entry, dict):
                    continue
                stamp = music_features.number(entry.get('updated'), 0, self.clock())
                tracks = entry.get('tracks')
                if stamp is None or self.clock() - stamp > STALE_SECONDS or not isinstance(tracks, list):
                    continue
                self.cache[uri] = {'updated': stamp, 'tracks': [t for i in tracks[:MAX_TRACKS]
                                                              if (t := normalise(i))]}
        self._reindex()

    def _reindex(self):
        self.index = {item['uri']: (entry['updated'], item)
                      for entry in self.cache.values() for item in entry['tracks']}

    def describe(self, uri):
        entry = self.index.get(uri)
        return dict(entry[1]) if entry and self.clock() - entry[0] <= STALE_SECONDS else {}

    def get(self, seeds, *, priority=False):
        if not self.enabled:
            return []
        now = self.clock()
        result = {}
        for seed in seeds[:3]:
            uri = seed.get('uri')
            if not track_ref(uri):
                continue
            entry = self.cache.get(uri)
            if entry and now - entry['updated'] <= STALE_SECONDS:
                for item in entry['tracks']:
                    result.setdefault(item['uri'], dict(item, _ma_recommendation=uri))
            fresh_for = FRESH_SECONDS if entry and entry['tracks'] else RETRY_SECONDS
            if (not entry or now - entry['updated'] >= fresh_for) and now >= self.retry.get(uri, 0):
                self.pending.setdefault(uri, None)
                while len(self.pending) > MAX_SEEDS:
                    self.pending.popitem(last=False)
        if priority:
            for seed in reversed(seeds[:3]):
                if seed.get('uri') in self.pending:
                    self.pending.move_to_end(seed['uri'], last=False)
        return list(result.values())

    def status(self):
        return {'enabled': self.enabled, 'cached_seeds': len(self.cache),
                'cached_tracks': sum(len(e['tracks']) for e in self.cache.values()),
                'pending_seeds': len(self.pending), 'last_error': self.last_error,
                'retry_after_seconds': max(0, round(self.cooldown - self.clock()))}

    async def refresh_one(self):
        if not self.enabled or not self.pending or self.clock() < self.cooldown:
            return False
        uri, _ = self.pending.popitem(last=False)
        if self.clock() < self.retry.get(uri, 0):
            return False
        entry = self.cache.get(uri)
        if entry and self.clock() - entry['updated'] < (FRESH_SECONDS if entry['tracks'] else RETRY_SECONDS):
            return False  # A request may have requeued this seed during an in-flight read.
        provider, item_id = track_ref(uri)
        try:
            # Public API supported in MA 2.10 and current development versions.
            # No dynamic-playlist creation or player/queue/autoplay calls here.
            response = await asyncio.wait_for(self.command('music/tracks/similar_tracks',
                {'item_id': item_id, 'provider_instance_id_or_domain': provider,
                 'limit': MAX_TRACKS, 'allow_lookup': True}), timeout=8)
            if isinstance(response, dict):
                if response.get('error') or response.get('error_code'):
                    raise ValueError('MA recommendation request failed')
                response = response.get('items', response.get('result'))
            if not isinstance(response, list):
                raise ValueError('Invalid MA recommendation response')
            tracks = {}
            for raw in response[:MAX_TRACKS]:
                item = normalise(raw)
                if item and item['uri'] != uri:
                    tracks.setdefault(item['uri'], item)
            # Provider recommendation lists often omit descriptors. Read a few
            # full metadata records in the worker, never on the wheel request.
            for item in [i for i in tracks.values() if not (i.get('genres') or i.get('genre') or i.get('mood_tags'))][:3]:
                try:
                    detail = await asyncio.wait_for(self.command('music/item_by_uri',
                        {'uri': item['uri'], 'allow_update_metadata': False}), timeout=3)
                    full = normalise(detail)
                    if full and full['uri'] == item['uri']:
                        tracks[item['uri']] = music_features.merge(item, full)
                except asyncio.CancelledError:
                    raise
                except aiohttp.ClientResponseError as error:
                    if error.status == 429:
                        raise  # Apply the same global backoff as the similar-track query.
                except Exception:
                    pass  # Unknown metadata stays unknown; never borrow the seed's tags.
            self.cache[uri] = {'updated': self.clock(), 'tracks': list(tracks.values())}
            self.cache.move_to_end(uri)
            self.retry.pop(uri, None)
            while len(self.cache) > MAX_SEEDS:
                old, _ = self.cache.popitem(last=False)
                self.retry.pop(old, None)
            self._reindex()
            self.last_error = None
            self.last_refreshed_seed = uri
            self.dirty = True
            return bool(tracks)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.last_error = type(error).__name__  # Never log credentials/response bodies.
            self.retry[uri] = self.clock() + RETRY_SECONDS
            self.cooldown = max(self.cooldown, self.clock() + 60)  # Avoid sweeping every seed while MA is down.
            while len(self.retry) > MAX_SEEDS:
                self.retry.popitem(last=False)
            if isinstance(error, aiohttp.ClientResponseError) and error.status == 429:
                value = (error.headers or {}).get('Retry-After', '')
                delay = music_features.number(value, 1, 86400)
                if delay is None:
                    try:
                        delay = parsedate_to_datetime(value).timestamp() - self.clock()
                    except (TypeError, ValueError, OverflowError):
                        delay = RETRY_SECONDS
                self.cooldown = self.clock() + max(RETRY_SECONDS, min(86400, delay))
                self.retry[uri] = self.cooldown
            self.dirty = True
            return False  # Retain the last successful batch and local fallback.

    def persist(self, force=False):
        if self.save and self.dirty and (force or self.clock() - self.last_save >= 60):
            self.save({'version': 1, 'seeds': dict(self.cache), 'cooldown': self.cooldown})
            self.last_save = self.clock()
            self.dirty = False

    async def run(self, refreshed=None):
        while True:
            if await self.refresh_one() and refreshed:
                refreshed(self.last_refreshed_seed)
            try:
                self.persist()
            except Exception as error:
                self.last_error = type(error).__name__  # Keep the worker alive on checkpoint failure.
            await asyncio.sleep(5)
