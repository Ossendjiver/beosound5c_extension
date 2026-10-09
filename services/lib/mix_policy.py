"""Duration and recording identity rules for local Pattern Play sessions."""
import re
import math
import unicodedata
from difflib import SequenceMatcher
from . import skip_policy

MIX_SECONDS = 20 * 60

def duration(item):
    try:
        value = float(item.get('duration') or 0)
        return value if math.isfinite(value) and value > 0 else 0
    except (ValueError, TypeError):
        return 0

def similar_length(item, seed):
    candidate, baseline = duration(item), duration(seed)
    if not candidate or not baseline:
        return False
    if baseline >= MIX_SECONDS:
        return candidate >= MIX_SECONDS and baseline / 3 <= candidate <= 3 * baseline
    if candidate >= MIX_SECONDS:
        return False
    return baseline / 3 <= candidate <= 3 * baseline

def identity(item):
    artists = item.get('artist') or item.get('artists') or ''
    if isinstance(artists, list):
        artists = ', '.join(a.get('name', '') if isinstance(a, dict) else str(a) for a in artists)
    title = item.get('name') or item.get('title') or ''
    title = re.sub(r'\([^)]*\)|\[[^]]*\]', '', str(title))
    title = re.split(r'\s[-–—]\s', title)[0]
    def clean(value):
        value = unicodedata.normalize('NFKD', str(value)).casefold()
        return ' '.join(re.findall(r'\w+', value))
    return clean(artists), clean(title)

def same_recording(a, b):
    artist_a, name_a = identity(a)
    artist_b, name_b = identity(b)
    return skip_policy.same(a, b) or bool(artist_a and artist_a == artist_b and name_a and name_b and
                (name_a == name_b or SequenceMatcher(None, name_a, name_b).ratio() >= .88))

def distinct(picks, previous):
    result = []
    index = RecordingIndex(previous)
    for item in picks:
        if index.matches(item):
            continue
        result.append(item)
        index.add(item)
    return result


class RecordingIndex:
    """Same recording rules indexed by artist, for large persistent sessions."""
    def __init__(self, items=()):
        self.uris = set()
        self.aliases = {}
        self.identities = {}
        for item in items:
            self.add(item)

    def add(self, item):
        uri = item.get('uri') or ''
        if uri:
            self.uris.add(uri)
        for artist, title in skip_policy.aliases(item):
            self.aliases.setdefault(artist, set()).add((title, uri))
        artist, title = identity(item)
        if artist and title:
            self.identities.setdefault(artist, set()).add((title, uri))

    def matches(self, item, *, alternate_only=False):
        uri = item.get('uri') or ''
        if not alternate_only and uri and uri in self.uris:
            return True
        def match(artist, title, groups):
            return any((not alternate_only or old_uri != uri) and
                       (old_title == title or SequenceMatcher(None, title, old_title).ratio() >= .88)
                       for old_title, old_uri in groups.get(artist, ()))
        if any(match(artist, title, self.aliases) for artist, title in skip_policy.aliases(item)):
            return True
        artist, title = identity(item)
        return bool(artist and title and match(artist, title, self.identities))
