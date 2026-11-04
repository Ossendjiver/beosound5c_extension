"""Recording-aware listening fatigue and persistent proposal rotation, never taste rewards."""
import math
import re
import unicodedata
from collections import Counter
from . import mix_policy

DAY = 86400


def artist_key(name):
    normalized = unicodedata.normalize('NFKD', str(name or '')).casefold()
    normalized = ''.join(c for c in normalized if not unicodedata.combining(c))
    return ' '.join(re.findall(r'\w+', normalized))


def artists(item):
    value = item.get('artists') or item.get('artist') or ''
    names = [v.get('name', '') if isinstance(v, dict) else str(v) for v in value] if isinstance(value, list) else [str(value)]
    result = {artist_key(n) for n in names}
    # Provider uploads may expose a channel as artist and put the performer in the title.
    title = str(item.get('name') or item.get('title') or '')
    prefix = re.split(r'\s[-–—]\s', title, maxsplit=1)
    if len(prefix) == 2: result.add(artist_key(prefix[0]))
    for name in names:
        result.update(artist_key(n) for n in re.split(r'\s*(?:,|;|\s&\s|\sfeat\.?\s|\sfeaturing\s)\s*', name, flags=re.I))
    return result - {''}


def avoided(item, preferences):
    blocked = {artist_key(name) for name in preferences}
    return bool(artists(item) & blocked)


class Prior:
    def __init__(self, history, proposals=(), now=0):
        self.tiers = {n: mix_policy.RecordingIndex() for n in range(1, 5)}
        self.artist_exposures = Counter()
        self.rows = {}
        self.now = now
        for raw in history:
            row = dict(raw)
            age = now - float(row.get('ts') or 0)
            # Brief/technical contacts and skips have their own policy, not a full-play cooldown.
            if not 0 <= age <= 7*DAY or float(row.get('seconds') or 0) < 30 or row.get('end_reason') == 'skip':
                continue
            tier = 3 if age < 6*3600 else 2 if age < DAY else 1 if age < 3*DAY else 0
            if tier: self.tiers[tier].add(row)
            for key in artists(row):
                self.rows.setdefault(key, []).append(row)
                if age < 6*3600: self.artist_exposures[key] += 1
        for raw in proposals:
            row = dict(raw)
            age = now - float(row.get('ts') or 0)
            if not 0 <= age < 12*3600: continue
            tier = 4 if age < 30*60 else 3 if age < 2*3600 else 2 if age < 6*3600 else 1
            self.tiers[tier].add(row)

    def describe(self, item):
        tier = next((n for n in range(4, 0, -1) if self.tiers[n].matches(item)), 0)
        candidates = {r.get('id', (r.get('uri'), r.get('ts'))): r for key in artists(item) for r in self.rows.get(key, [])}
        matches = [r for r in candidates.values() if mix_policy.same_recording(item, r)]
        penalty = min(32., sum(12*2**(-(self.now-float(r['ts']))/(2*DAY)) for r in matches))
        artist_penalty = min(4., .5*max([0]+[self.artist_exposures[key] for key in artists(item)]))
        return {'tier': tier, 'penalty': penalty + artist_penalty, 'recent_plays': len(matches)}


def remember(proposals, picks, now):
    # Only the first five proposed starts are reserved. No play counts/rewards are invented.
    old = [dict(r) for r in proposals if isinstance(r, dict) and 0 <= now-float(r.get('ts') or 0) < 12*3600]
    return (old + [{'uri': i.get('uri'), 'name': i.get('name'), 'artist': i.get('artist'),
                    'artists': i.get('artists'), 'ts': now} for i in picks[:5]])[-500:]


def learning_bonus(total):
    """Retain long-term preference ordering without unlimited popularity amplification."""
    return min(6., 2*math.log1p(max(0., total)))
