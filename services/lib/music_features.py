"""Validated acoustic descriptors, independent of listening/familiarity counters."""
import math
import re
from . import track_enrichment
from . import profile_quality


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
    for key in ('profile_confidence', 'bpm_confidence'):
        n = number(value.get(key), 0, 1)
        if n is not None:
            result[key] = n
    for key in ('sample_seconds', 'sample_sections'):
        n = number(value.get(key), 0, 86400)
        if n is not None:
            result[key] = n
    for key in ('coverage', 'analysis', 'classifier_fingerprint'):
        if isinstance(value.get(key), str):
            result[key] = value[key][:100]
    if value.get('profile_quality_version') == 1:
        result['profile_quality_version'] = 1
    patches = value.get('section_moods')
    if isinstance(patches, list):
        result['section_moods'] = [dict(energy=e, valence=v) for e, v in
            ((number(x.get('energy'), 0, 1), number(x.get('valence'), 0, 1))
             for x in patches[:32] if isinstance(x, dict)) if e is not None and v is not None]
    tags = profile_quality.classifiers(value.get('classifiers'))
    if tags:
        result['classifiers'] = tags
    variation = value.get('mood_variation')
    if isinstance(variation, dict):
        result['mood_variation'] = {k: number(v, 0, 1) for k, v in variation.items()
                                    if k in ('energy', 'valence') and number(v, 0, 1) is not None}
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


def artist_name(item):
    value=item.get('artist') or item.get('artists') or ''
    if isinstance(value,list):
        return ', '.join(str(v.get('name') or '') if isinstance(v,dict) else str(v) for v in value if v)
    if isinstance(value,dict):return str(value.get('name') or '')
    return str(value)


def metadata(item):
    """Use provider descriptors when actually supplied, never infer from titles."""
    meta = item.get('metadata') if isinstance(item.get('metadata'), dict) else {}
    result = {key: item.get(key) or meta.get(key) for key in
              ('genres', 'genre', 'mood_tags', 'mood_profile', 'isrc', 'artist_source', 'performers', 'inferred_genres', 'genre_evidence', 'genre_source', 'soundcloud_style_tags')}
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
    result.update(track_enrichment.performer_metadata(item))
    if track_enrichment.soundcloud(item):
        result.update(track_enrichment.genre_metadata(item))
        result.pop('genre', None)
    return {k: v for k, v in result.items() if v or k == 'genres' and track_enrichment.soundcloud(item)}


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

def radio_audio_assessment(item, root, sound=None):
    """Seed-anchored eligibility; no play counts and no predecessor drift."""
    a,b=(sound.features(item),sound.features(root)) if sound else (clean(item.get('audio_features')),clean(root.get('audio_features')))
    from lib import queue_spacing
    same_artist=bool(queue_spacing.artist(root) and queue_spacing.artist(root)==queue_spacing.artist(item))
    from lib import radio_genres
    if radio_genres.classical_work(root) and not same_artist and not radio_genres.classical_work(item) and 'classical' not in radio_genres.labels(item):
        return {'status': 'incompatible', 'reason': 'classical_work', 'confidence': 1.}
    distances=[]
    certainty = min(profile_quality.confidence(a), profile_quality.confidence(b))
    # RMS differs across mastering and provider normalization: it is not a veto.
    for key,maximum in [('spectral_centroid_hz',1.8)]:
        if a.get(key) and b.get(key):
            ratio=max(a[key]/b[key],b[key]/a[key])
            if ratio>maximum:return {'status': 'incompatible', 'reason': key, 'confidence': certainty}
    if a.get('model') and a.get('model')==b.get('model') and all(k in a and k in b for k in ('energy','valence')):
        d=math.hypot(a['energy']-b['energy'],a['valence']-b['valence'])
        if d>.20:return {'status': 'incompatible', 'reason': 'mood_distance', 'confidence': certainty}
        distances.append(d/.20)
    x,y=a.get('embedding'),b.get('embedding')
    if x and y and len(x)==len(y) and a.get('embedding_model') and a['embedding_model']==b.get('embedding_model'):
        cosine=sum(i*j for i,j in zip(x,y))/math.sqrt(sum(i*i for i in x)*sum(j*j for j in y))
        if cosine<.85:return {'status': 'incompatible', 'reason': 'embedding_distance', 'confidence': certainty}
        distances.append((1-min(1,cosine))/.15)
    if distances:
        return {'status': 'compatible', 'distance': sum(distances)/len(distances), 'confidence': certainty}
    return {'status': 'unknown', 'reason': 'missing_comparable_audio', 'confidence': 0.}


def _radio_audio_distance(item, root):
    assessment = radio_audio_assessment(item, root)
    return assessment.get('distance') if assessment['status'] == 'compatible' else None


def radio_distance(item, root, sound=None):
    """Close audio, then specific genre, then broader family; never popularity."""
    from lib import radio_genres,queue_spacing
    if radio_genres.conflicting(item,root):return None
    assessment=radio_audio_assessment(item,root,sound)
    if assessment['status']=='compatible':return assessment['distance']
    if assessment['status']=='incompatible' and assessment['confidence'] >= .65:
        return None  # A weaker tag must not override trustworthy contradictory audio.
    genre=radio_genres.fallback(item,root)
    if genre is not None:return genre
    artist=queue_spacing.artist(root)
    if artist and artist==queue_spacing.artist(item):return 1.6
    return None
