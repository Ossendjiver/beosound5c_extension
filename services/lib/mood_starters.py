"""Backend-owned, bounded mood starter pools; never sample audio or mutate queues.

Persist URI references only. Profiles are hydrated once by the backend, not by
wheel requests. The existing ranker remains the authority for every selection.
"""
import math
import time
from . import mix_policy, music_mood

SECTORS = 16
ACTIVE = 5
RESERVE_PER_CATEGORY = 20
REFRESH_SECONDS = 15 * 60
ROTATION_SECONDS = 2 * 3600


class MoodStarters:
    def __init__(self, load=None, save=None, clock=time.time):
        self.clock, self.save = clock, save
        value = load() if load else {}
        self.bands = value.get('bands', {}) if isinstance(value, dict) and value.get('version') == 1 else {}
        self.bands = self.bands if isinstance(self.bands, dict) else {}
        self.bands = {k: v for k, v in self.bands.items() if k in self.keys() and isinstance(v, dict)
                      and isinstance(v.get('active'), list) and isinstance(v.get('reserve'), list)}
        for band in self.bands.values():
            band['active'] = [u for u in band['active'] if isinstance(u, str)][:ACTIVE]
            band['reserve'] = [u for u in band['reserve'] if isinstance(u, str)][:2 * RESERVE_PER_CATEGORY]
            stamp = band.get('updated', 0)
            band['updated'] = stamp if isinstance(stamp, (int, float)) and math.isfinite(stamp) else 0
        self.recent = value.get('recent', []) if isinstance(value, dict) and value.get('version') == 1 else []
        self.recent = self.recent if isinstance(self.recent, list) else []
        self.recent = [r for r in self.recent if isinstance(r, dict) and isinstance(r.get('uri'), str)
                       and isinstance(r.get('ts'), (int, float)) and math.isfinite(r['ts'])][-256:]
        self.catalogue = None
        self.index = {}
        self.revision = None
        self.urgent = set()
        self.requested = None
        self.requests = {}
        self.dirty = False
        self.saved_at = self.clock()
        self.last_error = ''

    @staticmethod
    def keys():
        return [f'{kind}:{sector}' for kind in ('song', 'mix') for sector in range(SECTORS)]

    @staticmethod
    def key(mood, seed=None):
        sector = int((float(mood['angle']) % 360 + 180 / SECTORS) // (360 / SECTORS)) % SECTORS
        return f'{"mix" if mix_policy.duration(seed or {}) >= mix_policy.MIX_SECONDS else "song"}:{sector}'

    def bind(self, catalogue, revision=None):
        self.catalogue = catalogue
        self.index = {i['uri']: i for i in catalogue if i.get('uri')}
        if self.revision != revision:
            self.urgent.update(self.keys())
        self.revision = revision

    def due(self):
        now = self.clock()
        keys = self.keys()
        urgent = [k for k in keys if k in self.urgent and
                  now - self.bands.get(k, {}).get('updated', 0) >= 30]
        if self.requested in urgent:
            return self.requested
        return next(iter(urgent), None) or next(
            (k for k in keys if now - self.bands.get(k, {}).get('updated', 0) >= REFRESH_SECONDS), None)

    def candidates(self, mood, seed=None):
        """None is cold start; an empty warm result means insufficient safe data."""
        key = self.key(mood, seed)
        self.requested = key
        if self.catalogue is None or key not in self.bands:
            self.urgent.add(key)
            return None
        kind = key.split(':')[0]
        # All sectors allow the existing gradual trajectory planner to bridge moods.
        # At most 16 * (5 active + 40 reserve) references, not the full library.
        uris = dict.fromkeys(u for k, band in self.bands.items() if k.startswith(kind + ':')
                             for u in band['active'] + band['reserve'] if isinstance(u, str))
        return [dict(self.index[u]) for u in uris if u in self.index]

    def fresh(self, candidates):
        recent = [r for r in self.recent if self.clock() - r['ts'] < ROTATION_SECONDS]
        index = mix_policy.RecordingIndex([self.index.get(r['uri'], r) for r in recent])
        return [i for i in candidates if not index.matches(i)]

    def request(self, mood, seed, context):
        key = self.key(mood, seed)
        self.requests[key] = {'seed': seed or {}, 'exclude': set(context.get('starter_exclude', [])),
            'previous': context.get('queue_previous', []) + context.get('queue_versions', []),
            'room': context.get('room', ''), 'ts': self.clock()}
        self.requested = key
        self.urgent.add(key)

    def reserve(self, mood, picks, seed=None):
        # These are rotation reservations, never synthetic listens or play counts.
        if not picks:
            return
        now = self.clock()
        selected = [dict(i) for i in picks[:ACTIVE]]
        self.recent = [r for r in self.recent if now - r['ts'] < ROTATION_SECONDS]
        self.recent.extend({'uri': i['uri'], 'artist': mix_policy.identity(i)[0],
                            'name': mix_policy.identity(i)[1], 'head': n == 0, 'ts': now} for n, i in enumerate(selected))
        self.recent = self.recent[-256:]
        self.requested = self.key(mood, seed)
        self.urgent.add(self.requested)
        self.dirty = True

    def refresh(self, key, ranked):
        """Publish one complete band atomically; old bands survive rank failures."""
        kind = key.split(':')[0]
        ranked = mix_policy.distinct([i for i in ranked if mix_policy.duration(i) and
            (mix_policy.duration(i) >= mix_policy.MIX_SECONDS) == (kind == 'mix')], [])
        unused = self.fresh(ranked)
        unused_uris = {i['uri'] for i in unused}
        rotated = unused + [i for i in ranked if i['uri'] not in unused_uris]
        reserves = []
        for category in ('familiar', 'discovery'):
            groups = {}
            for item in rotated:
                if item.get('_selection_category', 'familiar') == category:
                    # Duration diversity prevents normal 4-minute favourites from
                    # erasing short songs or extended tracks from a starter pool.
                    band = math.floor(math.log(max(1, mix_policy.duration(item)) / 40, 3))
                    groups.setdefault(band, []).append(item)
            selected = []
            while groups and len(selected) < RESERVE_PER_CATEGORY:
                for band in list(groups):
                    selected.append(groups[band].pop(0))
                    if not groups[band]: del groups[band]
                    if len(selected) == RESERVE_PER_CATEGORY: break
            reserves.extend(selected)
        fresh = self.fresh(reserves)
        fresh_uris = {i['uri'] for i in fresh}
        ordered = fresh + [i for i in reserves if i['uri'] not in fresh_uris]
        old = self.bands.get(key, {}).get('active', [])
        available = {i['uri']: i for i in ordered}
        # Retain up to two still-eligible, unused tracks; replenish the other slots.
        retain = [available[u] for u in old if u in available and u in fresh_uris][:2]
        distinct = mix_policy.distinct(retain + ordered, [])
        spaced, artists = [], set()
        for item in distinct:
            artist = mix_policy.identity(item)[0]
            if artist not in artists:
                spaced.append(item); artists.add(artist)
        spaced += [i for i in distinct if i not in spaced]
        self.bands[key] = {'active': [i['uri'] for i in spaced[:ACTIVE]],
                           'reserve': [i['uri'] for i in reserves], 'updated': self.clock()}
        self.urgent.discard(key)
        self.dirty = True
        self.last_error = ''

    def persist(self, force=False):
        # Small URI-only checkpoint, at most once per minute; never PCM/features.
        if self.save and self.dirty and (force or self.clock() - self.saved_at >= 60):
            self.save({'version': 1, 'bands': self.bands, 'recent': self.recent})
            self.saved_at, self.dirty = self.clock(), False

    def status(self):
        return {'sectors': SECTORS, 'tracks_per_sector': ACTIVE, 'refresh_seconds': REFRESH_SECONDS,
                'ready': self.catalogue is not None and bool(self.bands), 'last_error': self.last_error,
                'bands': {k: {'active_count': sum(u in self.index for u in v['active']),
                              'updated': v.get('updated', 0)} for k, v in self.bands.items()}}
