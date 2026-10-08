"""Duration and recording identity rules for local Pattern Play sessions."""
import re
import unicodedata
from difflib import SequenceMatcher

MIX_SECONDS = 20 * 60

def duration(item):
    try:
        return max(0, float(item.get('duration') or 0))
    except (ValueError, TypeError):
        return 0

def similar_length(item, seed):
    candidate, baseline = duration(item), duration(seed)
    if not candidate:
        return False
    if baseline >= MIX_SECONDS:
        return candidate >= MIX_SECONDS and .5 * baseline <= candidate <= 2 * baseline
    if candidate >= MIX_SECONDS:
        return False
    return not baseline or not candidate or .5 * baseline <= candidate <= 2 * baseline

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
    return bool(artist_a and artist_a == artist_b and name_a and name_b and
                (name_a == name_b or SequenceMatcher(None, name_a, name_b).ratio() >= .88))

def distinct(picks, previous):
    result = []
    for item in picks:
        if any(same_recording(item, other) for other in previous + result):
            continue
        result.append(item)
    return result
