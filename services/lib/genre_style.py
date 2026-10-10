"""Conservative library-trained style transfer. No play counts or title inference.

The batch builder requires NumPy; playback only reads the resulting sidecar.
Artist-disjoint evidence, compatible embedding models, and short/long track
separation prevent provider aliases and artist recognition inflating confidence.
"""
import math
from . import mix_policy, radio_genres
SOURCE = 'library-audio-style-knn-v1'


def explicit(item):
    copy = dict(item, inferred_genres=[], genre_evidence={})
    copy['metadata'] = dict(item.get('metadata') or {}, inferred_genres=[], genre_evidence={})
    return radio_genres.labels(copy)


def accepted(evidence):
    return (isinstance(evidence, dict) and evidence.get('source') == SOURCE
        and type(evidence.get('matched_artists')) is int and evidence['matched_artists'] >= 3
        and isinstance(evidence.get('confidence'), (int, float))
        and not isinstance(evidence['confidence'], bool) and .8 <= evidence['confidence'] <= 1
        and evidence.get('validated') is True)


def vector(item):
    f = item.get('audio_features') or {}
    v = f.get('embedding'); model = f.get('embedding_model')
    if not model or not isinstance(v, list) or not 8 <= len(v) <= 2048:
        return None
    if any(isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x) for x in v):
        return None
    norm = math.sqrt(sum(x*x for x in v))
    return (model, [x/norm for x in v]) if norm > 0 else None


class Model:
    def __init__(self, items):
        import numpy as np
        self.np = np
        self.groups = {}
        seen = mix_policy.RecordingIndex()
        artist_counts = {}
        for item in sorted(items, key=lambda x: str(x.get('uri', ''))):
            artist = mix_policy.identity(item)[0]
            data = vector(item); tags = explicit(item)
            if not data or not tags or not artist or seen.matches(item) or not mix_policy.duration(item):
                continue
            if artist_counts.get(artist, 0) >= 8: continue
            artist_counts[artist] = artist_counts.get(artist, 0)+1
            seen.add(item)
            key = (data[0], len(data[1]), mix_policy.duration(item) >= mix_policy.MIX_SECONDS)
            self.groups.setdefault(key, []).append((artist, tags, data[1]))
        for key, rows in list(self.groups.items()):
            self.groups[key] = (rows, np.asarray([r[2] for r in rows], dtype=np.float32))

    def predict(self, item):
        data = vector(item); artist = mix_policy.identity(item)[0]
        if not data or not artist or not mix_policy.duration(item): return None
        rows, matrix = self.groups.get((data[0], len(data[1]), mix_policy.duration(item) >= mix_policy.MIX_SECONDS), ([], None))
        if matrix is None: return None
        scores = matrix @ self.np.asarray(data[1], dtype=self.np.float32)
        indices = self.np.argsort(-scores)
        neighbours = []; artists = set()
        for i in indices:
            other, tags, _ = rows[int(i)]
            if other == artist or other in artists: continue
            if float(scores[i]) < .9: break
            artists.add(other); neighbours.append((tags, float(scores[i])))
            if len(neighbours) == 7: break
        if len(neighbours) < 3: return None
        candidates = set().union(*(tags for tags, _ in neighbours))
        candidates |= set().union(*(radio_genres.families(tags) for tags, _ in neighbours))
        weights = [max(.01, score-.85) for _, score in neighbours]
        predictions = []
        for label in sorted(candidates):
            support = [n for n,(tags,_) in enumerate(neighbours) if label in tags or label in radio_genres.families(tags)]
            confidence = sum(weights[n] for n in support)/sum(weights)
            if len(support) >= 3 and confidence >= .8:
                predictions.append((label, confidence, len(support)))
        if not predictions: return None
        return {'inferred_genres':[p[0] for p in predictions], 'genre_evidence':{
            'source':SOURCE, 'confidence':min(p[1] for p in predictions),
            'matched_artists':min(p[2] for p in predictions), 'validated':False}}

    def evaluate(self, items, limit=400):
        # Excluding the query artist in predict is a leave-one-artist-out test.
        # Alternate versions/provider aliases of that artist are excluded too.
        rows = sorted((i for i in items if explicit(i) and vector(i)), key=lambda i: str(i.get('uri')))
        selected = rows[::max(1, len(rows)//limit)][:limit]
        tested = correct = exact = 0
        label_stats = {}
        for item in selected:
            result = self.predict(item)
            if not result: continue
            prediction = set(result['inferred_genres']); truth = explicit(item)
            truth = truth | radio_genres.families(truth)
            for label in prediction:
                stats=label_stats.setdefault(label,[0,0]);stats[0]+=1;stats[1]+=int(label in truth)
            a,b = radio_genres.families(prediction), radio_genres.families(truth)
            tested += 1
            correct += bool(a and b and a <= b)
            exact += bool(radio_genres.specific(prediction) & radio_genres.specific(truth))
        validated_labels=[label for label,(n,c) in label_stats.items() if n>=10 and c/n>=.85]
        trained_families=set().union(*(radio_genres.families(tags) for rows,_ in self.groups.values() for _,tags,_ in rows))
        return {'validated_labels':sorted(validated_labels), 'label_validation':{label:{'accepted':n,'precision':c/n} for label,(n,c) in label_stats.items()}, 'trained_families':sorted(trained_families), 'holdout_items':len(selected), 'accepted_items':tested,
            'family_precision': correct/tested if tested else 0,
            'exact_style_agreement':exact/tested if tested else 0,
            'validation':'leave-one-artist-out; explicit metadata is noisy, not listening ground truth',
            'enabled':len(trained_families)>=3 and len(validated_labels)>=2 and tested >= 20 and correct/tested >= .8}
