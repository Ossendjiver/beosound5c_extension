"""Small, multi-interest taste profiles learned only from deliberate listening."""
import math
from . import music_features, music_relations


def fit(history, catalogue, now, maximum=8):
    by_uri = {i.get('uri'): i for i in catalogue}
    by_key = {music_relations.key(i): i for i in catalogue if music_relations.key(i)}
    sound = music_features.Similarity()
    clusters = []
    for row in reversed(history):
        row = dict(row)
        if row.get('origin') != 'manual' or row.get('reward', 0) < 1:
            continue
        item = by_uri.get(row.get('uri')) or by_key.get(music_relations.key(row))
        if not item or not (item.get('audio_features') or {}).get('embedding'):
            continue
        nearest = max(clusters, key=lambda c: sound(item, c['representative']), default=None)
        if nearest is None or sound(item, nearest['representative']) < .55:
            if len(clusters) < maximum:
                nearest = {'representative': item, 'observations': []}
                clusters.append(nearest)
            else:
                continue  # Do not blur unrelated tastes into an existing cluster.
        nearest['observations'].append(row)
        nearest['observations'] = nearest['observations'][-100:]
    return [c for c in clusters if len(c['observations']) >= 3]


def select(clusters, context, recent, now):
    sound = music_features.Similarity()
    ranked = []
    for cluster in clusters:
        weight = 0.
        for row in cluster['observations']:
            age = max(0, now-row['ts'])
            hour_delta = min(abs(context.get('hour', 12)-row['hour']), 24-abs(context.get('hour', 12)-row['hour']))
            hour = .5+.5*math.cos(hour_delta*math.pi/12)
            weight += min(1.5, row['reward'])*2**(-age/(28*86400))*(.5+hour)*(
                1.15 if context.get('room') == row.get('room') else 1.)
        if recent:
            weight *= .25+max(sound(cluster['representative'], r) for r in recent[:3])
        ranked.append((weight, cluster['representative']))
    return sorted(ranked, key=lambda pair: -pair[0])[:3]


def bonus(item, profiles, sound):
    total = sum(w for w, _ in profiles)
    if not total:
        return 0.
    # Query each taste separately instead of averaging unrelated embeddings.
    return min(2., 2*max((w/total)*sound(item, root) for w, root in profiles))
