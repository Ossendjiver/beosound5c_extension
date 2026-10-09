"""Validated acoustic descriptors, independent of listening/familiarity counters."""
import math


def number(value, low, high):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) and low <= value <= high else None
    except (TypeError, ValueError):
        return None


def clean(value):
    if not isinstance(value, dict):
        return {}
    result = {}
    for key, low, high in [('bpm', 30, 300), ('energy', 0, 1), ('valence', 0, 1),
                           ('spectral_centroid_hz', 0, 24000), ('rms', 0, 2)]:
        item = number(value.get(key), low, high)
        if item is not None:
            result[key] = item
    # Coordinates require an identified model; tempo/loudness alone are not mood.
    if not value.get('model'):
        result.pop('energy', None)
        result.pop('valence', None)
    if value.get('model'):
        result['model'] = str(value['model'])[:200]
    vector = value.get('embedding')
    if isinstance(vector, list) and 8 <= len(vector) <= 2048:
        values = [number(v, -1e6, 1e6) for v in vector]
        if all(v is not None for v in values) and sum(v*v for v in values) > 0:
            result['embedding'] = values
            result['embedding_model'] = str(value.get('embedding_model') or '')[:200]
    return result


def merge(existing, incoming):
    """Empty provider fields must never erase useful cache or analysis data."""
    result = dict(existing)
    for key, value in incoming.items():
        if value is None or value == '' or value == [] or value == {}:
            continue
        if key in ('trusted', 'favorite'):
            result[key] = bool(result.get(key) or value)
        elif key in ('genres', 'mood_tags'):
            old = result.get(key) or []
            old = [old] if isinstance(old, str) else list(old)
            new = [value] if isinstance(value, str) else list(value)
            result[key] = list(dict.fromkeys(str(v) for v in old + new if v))
        elif key == 'duration' and number(value, 0.001, 86400) is None:
            continue
        elif key == 'audio_features':
            result[key] = {**clean(result.get(key)), **clean(value)}
        else:
            result[key] = value
    return result


def metadata(item):
    """Use provider descriptors when actually supplied, never infer from titles."""
    meta = item.get('metadata') or {}
    result = {key: item.get(key) or meta.get(key) for key in
              ('genres', 'genre', 'mood_tags', 'mood_profile', 'isrc')}
    features = clean(item.get('audio_features') or meta.get('audio_features'))
    bpm = number(item.get('bpm') or meta.get('bpm'), 30, 300)
    if bpm is not None:
        features['bpm'] = bpm
    if features:
        result['audio_features'] = features
    return {k: v for k, v in result.items() if v}


def similarity(item, root):
    """Small sound/tempo continuity bonus AFTER mood and length eligibility."""
    a, b = clean(item.get('audio_features')), clean(root.get('audio_features'))
    bonus = 0.0
    if a.get('bpm') and b.get('bpm'):
        # Beat trackers often disagree by a half/double tempo.
        delta = min(abs(math.log2(a['bpm'] * ratio / b['bpm'])) for ratio in (.5, 1, 2))
        bonus += .4 * max(0, 1-delta/.25)
    x, y = a.get('embedding'), b.get('embedding')
    if x and y and len(x) == len(y) and a.get('embedding_model') and a['embedding_model'] == b.get('embedding_model'):
        cosine = sum(i*j for i,j in zip(x,y)) / math.sqrt(sum(i*i for i in x)*sum(j*j for j in y))
        bonus += .6 * max(0, min(1, cosine))
    return bonus
