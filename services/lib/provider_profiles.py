"""Recording-safe provider metadata and reusable sidecar records."""
import hashlib
import json
import re
import unicodedata
from . import music_features, music_mood

PROVIDERS = ('tidal', 'soundcloud')
VERSION = 1


def normal(value):
    text = unicodedata.normalize('NFKD', str(value or '')).casefold()
    return ' '.join(re.findall(r'\w+', ''.join(c for c in text if not unicodedata.combining(c))))


def artists(item):
    values = item.get('artists') or []
    return [v.get('name', '') if isinstance(v, dict) else str(v) for v in values] or [item.get('artist', '')]


def title(item):
    name, version = str(item.get('name') or item.get('title') or ''), str(item.get('version') or '')
    return name if not version or normal(version) in normal(name) else name+' ('+version+')'


def length_matches(a, b):
    a, b = music_features.number(a, 1, 86400), music_features.number(b, 1, 86400)
    return a is not None and b is not None and abs(a-b) <= max(5, min(15, .03*a))


def recording_match(item, recording):
    """Never drop remix/live/version words; duration disambiguates same titles."""
    credit = recording.get('artist-credit') or []
    names = [v.get('name') or (v.get('artist') or {}).get('name', '') for v in credit if isinstance(v, dict)]
    return (normal(title(item)) == normal(recording.get('title'))
            and bool(names) and normal(artists(item)[0]) == normal(names[0])
            and length_matches(item.get('duration'), (recording.get('length') or 0)/1000))


def choose_recording(item, recordings):
    matches = {r['id']: r for r in recordings if r.get('id') and recording_match(item, r)}
    return next(iter(matches.values())) if len(matches) == 1 else None


def provider_identity(item, provider, identifier):
    """MA may return a canonical library item with exact provider mappings."""
    if str(item.get('item_id')) == str(identifier):
        return True
    return any(str(mapping.get('item_id')) == str(identifier) and
               (mapping.get('provider_instance') == provider or
                '--' not in provider and mapping.get('provider_domain') == provider)
               for mapping in item.get('provider_mappings') or [] if isinstance(mapping, dict))


def external_ids(item):
    result = {}
    for pair in item.get('external_ids') or []:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            continue
        kind, value = str(pair[0]).casefold(), str(pair[1])
        if kind == 'isrc' and re.fullmatch(r'[A-Z]{2}[A-Z0-9]{3}\d{7}', value.upper().replace('-', '')):
            result['isrc'] = value.upper().replace('-', '')
        if kind in ('musicbrainz_recordingid', 'musicbrainz_recording_id', 'musicbrainz') and re.fullmatch(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', value):
            result['mbid'] = value.lower()
    return result


def fingerprint(item):
    data = [VERSION, item.get('uri'), title(item), artists(item), item.get('duration'), external_ids(item)]
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def useful(metadata):
    return music_mood.profile(metadata, {}) is not None


def acoustic_metadata(item, document, mbid):
    """Archive classifier probabilities are estimates, not manual mood labels."""
    meta = document.get('metadata') or {}
    if not length_matches(item.get('duration'), (meta.get('audio_properties') or {}).get('length')):
        return {}
    tags = meta.get('tags') or {}
    submitted = tags.get('musicbrainz_recordingid') or tags.get('musicbrainz_trackid') or []
    submitted = [submitted] if isinstance(submitted, str) else submitted
    if submitted and mbid not in submitted:
        return {}
    high = document.get('highlevel') or {}
    probabilities = {}
    for label in ('happy', 'sad', 'relaxed', 'aggressive'):
        data = high.get('mood_'+label) or {}
        values = data.get('all') or {}
        positive = music_features.number(values.get(label), 0, 1)
        negative = music_features.number(values.get('not_'+label), 0, 1)
        if positive is None or negative is None or abs(positive+negative-1) > .02 or max(positive, negative) < .65:
            return {}
        probabilities[label] = positive
    if (probabilities['happy'] > .75 and probabilities['sad'] > .75
            or probabilities['relaxed'] > .75 and probabilities['aggressive'] > .75):
        return {}
    result = {'audio_features': {'model': 'acousticbrainz-mood-probabilities-v1',
                'valence': (probabilities['happy']+1-probabilities['sad'])/2,
                'energy': (probabilities['aggressive']+1-probabilities['relaxed'])/2}}
    return result


def cached_items(tree):
    """Provider playlist items need not be imported into MA's canonical library."""
    result = {}
    def walk(node, artist='', trusted=False):
        if isinstance(node, list):
            for value in node: walk(value, artist, trusted)
        elif isinstance(node, dict):
            artist = node.get('artist') or artist
            trusted = trusted or str(node.get('name', '')).casefold().startswith('most played') or node.get('favorite', False)
            uri = str(node.get('uri') or node.get('url') or '')
            if '://track/' in uri and uri.split('://')[0].split('--')[0] in PROVIDERS:
                result[uri] = {'uri': uri, 'name': node.get('name'), 'artist': artist,
                               'duration': node.get('duration'), 'version': node.get('version'), 'favorite': trusted}
            for key in ('tracks', 'items', 'children'):
                if isinstance(node.get(key), list):walk(node[key], artist, trusted)
    walk(tree)
    return list(result.values())


def inventory(canonical, cached):
    result = {i['uri']: dict(i, aliases=[i['uri']]) for i in cached}
    for item in canonical:
        mappings = [m for m in item.get('provider_mappings') or [] if m.get('provider_domain') in PROVIDERS and m.get('available', True)]
        if not mappings: continue
        aliases = {item['uri']}
        for mapping in mappings:
            aliases.add(f"{mapping['provider_instance']}://track/{mapping['item_id']}")
        # Prefer an already-cached provider URI; keep canonical mapping exact.
        key = next((u for u in sorted(aliases) if u in result), None)
        mapping = mappings[0]
        key = key or f"{mapping['provider_instance']}://track/{mapping['item_id']}"
        favorite=bool(item.get('favorite') or result.get(key,{}).get('favorite'))
        for alias in aliases:
            if alias != key:result.pop(alias,None)
        result[key] = {**item, 'uri': key, 'aliases': sorted(aliases), 'favorite':favorite}
    return list(result.values())


def load(payload, calibration=None):
    if not isinstance(payload, dict) or payload.get('version') != VERSION or not isinstance(payload.get('tracks'), dict):
        raise ValueError('Invalid provider profile sidecar')
    result = {}
    for uri, record in payload['tracks'].items():
        if not isinstance(record, dict): continue
        metadata = music_features.metadata(record.get('metadata') or {})
        if metadata.get('audio_features'):
            metadata['audio_features'] = music_features.calibrate(metadata['audio_features'], calibration)
        for alias in [uri]+[a for a in record.get('aliases', []) if isinstance(a, str)]:
            result.setdefault(alias, (metadata, record.get('audio_source') or record.get('source', 'metadata')))
    return result
