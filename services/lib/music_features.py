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
    meta = item.get('metadata') if isinstance(item.get('metadata'), dict) else {}
    result = {key: item.get(key) or meta.get(key) for key in
              ('genres', 'genre', 'mood_tags', 'mood_profile', 'isrc')}
    styles = item.get('style') or meta.get('style')
    if styles:
        result['genres'] = ([result['genres']] if isinstance(result.get('genres'), str) else list(result.get('genres') or [])) + ([styles] if isinstance(styles, str) else list(styles))
    if not result.get('mood_tags') and meta.get('mood'):
        result['mood_tags'] = [meta['mood']] if isinstance(meta['mood'], str) else meta['mood']
    features = clean(item.get('audio_features') or meta.get('audio_features'))
    bpm = number(item.get('bpm') or meta.get('bpm') or (item.get('audio_metadata') if isinstance(item.get('audio_metadata'), dict) else {}).get('bpm'), 30, 300)
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


class Similarity:
    """Validate/normalise descriptors once per ranking pass, not per queue slot."""
    def __init__(self):
        self.cache = {}

    def features(self, item):
        key = id(item)
        if key not in self.cache:
            value = clean(item.get('audio_features'))
            vector = value.get('embedding')
            if vector:
                norm = math.sqrt(sum(v*v for v in vector))
                value['embedding'] = [v/norm for v in vector]
            self.cache[key] = (item, value)  # Keep references so object IDs cannot be reused.
        return self.cache[key][1]

    def __call__(self, item, root):
        a, b = self.features(item), self.features(root)
        bonus = 0.
        if a.get('bpm') and b.get('bpm'):
            delta = min(abs(math.log2(a['bpm']*ratio/b['bpm'])) for ratio in (.5, 1, 2))
            bonus += .4*max(0, 1-delta/.25)
        x, y = a.get('embedding'), b.get('embedding')
        if x and y and len(x) == len(y) and a.get('embedding_model') and a['embedding_model'] == b.get('embedding_model'):
            bonus += .6*max(0, min(1, sum(i*j for i,j in zip(x,y))))
        return bonus


def library_calibration(records):
    """Unweighted acoustic range, never driven by play counts or favourites.

    Models often occupy a much narrower range than their nominal 0..1 scale.
    Interpret the wheel relative to a sufficiently varied profiled library.
    Keep raw predictions on disk; this transform applies at load time only.
    """
    groups = {}
    for record in records:
        features = record.get('audio_features') or {}
        if not isinstance(features, dict) or not features.get('model'):
            continue
        energy, valence = number(features.get('energy'), 0, 1), number(features.get('valence'), 0, 1)
        if energy is not None and valence is not None:
            groups.setdefault(str(features['model']), []).append((energy, valence))
    def quantile(values, fraction):
        values = sorted(values)
        index = (len(values)-1)*fraction
        lower = math.floor(index)
        return values[lower]+(values[min(lower+1, len(values)-1)]-values[lower])*(index-lower)
    result = {'version': 1, 'method': 'unweighted-library-p05-p95', 'models': {}}
    for model, values in groups.items():
        if len(values) < 100:
            continue
        axes = {}
        for index, key in enumerate(('energy', 'valence')):
            low, high = quantile([v[index] for v in values], .05), quantile([v[index] for v in values], .95)
            if high-low >= .1:  # Never expand a near-constant collection/noise.
                axes[key] = {'low': low, 'high': high}
        if axes:
            result['models'][model] = {'count': len(values), **axes}
    return result


def calibrate(features, calibration):
    """Validated library-relative coordinates; embeddings/BPM remain unchanged."""
    result = dict(features)
    if not isinstance(calibration, dict) or calibration.get('version') != 1:
        return result
    models = calibration.get('models')
    if not isinstance(models, dict):
        return result
    model = models.get(features.get('model'))
    if not isinstance(model, dict) or number(model.get('count'), 100, 1e7) is None:
        return result
    for key in ('energy', 'valence'):
        axis = model.get(key)
        if not isinstance(axis, dict):
            continue
        low, high, value = number(axis.get('low'), 0, 1), number(axis.get('high'), 0, 1), number(features.get(key), 0, 1)
        if low is not None and high is not None and high-low >= .1 and value is not None:
            result[key] = max(0., min(1., (value-low)/(high-low)))
    return result

def radio_distance(item, root):
    """Seed-anchored eligibility; no play counts and no predecessor drift."""
    a,b=clean(item.get('audio_features')),clean(root.get('audio_features'))
    distances=[]
    if a.get('model') and a.get('model')==b.get('model') and all(k in a and k in b for k in ('energy','valence')):
        d=math.hypot(a['energy']-b['energy'],a['valence']-b['valence'])
        if d>.30:return None
        distances.append(d/.30)
    x,y=a.get('embedding'),b.get('embedding')
    if x and y and len(x)==len(y) and a.get('embedding_model') and a['embedding_model']==b.get('embedding_model'):
        cosine=sum(i*j for i,j in zip(x,y))/math.sqrt(sum(i*i for i in x)*sum(j*j for j in y))
        if cosine<.65:return None
        distances.append((1-min(1,cosine))/.35)
    if distances:return sum(distances)/len(distances)
    # Missing analysis must not admit the entire favourite catalogue.
    def genres(v):
        value=v.get('genres') or v.get('genre') or []
        return {str(g).strip().casefold() for g in ([value] if isinstance(value,str) else value) if g}
    shared=genres(item)&genres(root)
    if shared:return .8
    from lib import queue_spacing
    artist=queue_spacing.artist(root)
    if artist and artist==queue_spacing.artist(item):return .9
    return None
