"""Bounded look-ahead sequencing; candidate eligibility remains a hard boundary."""
import math
from collections import Counter
from . import music_features, music_mood, queue_spacing, profile_quality


def transition_cost(previous, item, sound=None):
    a, b = (sound.features(previous), sound.features(item)) if sound else (
        music_features.clean(previous.get('audio_features')), music_features.clean(item.get('audio_features')))
    cost = 0.
    if a.get('model') and a.get('model') == b.get('model'):
        for key in ('energy', 'valence'):
            if key in a and key in b:
                cost += 1.5*abs(a[key]-b[key])
    if a.get('bpm') and b.get('bpm') and min(a.get('bpm_confidence', .5), b.get('bpm_confidence', .5)) >= .15:
        delta = min(abs(math.log2(a['bpm']*ratio/b['bpm'])) for ratio in (.5, 1, 2))
        cost += min(1., delta/.30)
    x, y = a.get('classifiers', {}), b.get('classifiers', {})
    if x and y:
        shared = x.keys() & y.keys()
        cost += sum(abs(x[k]-y[k]) for k in shared)/max(1, len(shared))
    return cost


class Planner:
    """Two future steps over twelve candidates, retaining the best four paths."""
    def __init__(self, sound, relations, band, artist_names, mood=None, learned=None):
        self.sound, self.relations, self.band = sound, relations, band
        self.artist_names, self.mood, self.learned = artist_names, mood, learned or {}
        self.steps = 0

    def value(self, pair, anchor, recent, position=0):
        score, item = pair
        artist = self.artist_names.get(item['uri'], '')
        album = str(item.get('album') or '').casefold()
        artists = Counter(queue_spacing.artist(i) for i in recent[-12:])
        albums = Counter(str(i.get('album') or '').casefold() for i in recent[-8:])
        diversity = .8*max(0, artists[artist]-1) if artist else 0.
        if album:
            diversity += .4*max(0, albums[album]-1)
        return score+self.relations.boost(anchor, item)+.7*self.sound(item, anchor)-(
            2.*transition_cost(anchor, item, self.sound))-diversity

    def choose(self, pool, anchor, recent_artists, recent_items, future_pool=()):
        eligible = queue_spacing.spaced(pool, recent_artists, self.artist_names)
        best_band = min(self.band(p[1]) for p in eligible)
        same_band = [p for p in eligible if self.band(p[1]) == best_band]
        top = sorted(same_band, key=lambda p: -self.value(p, anchor, recent_items))[:8]
        # Future paths never override the current candidate's seed/mood band.
        def path(first):
            self.steps += 1
            previous = first[1]
            beam = [(self.value(first, anchor, recent_items), {previous['uri']}, previous,
                     recent_items+[previous], (recent_artists+[self.artist_names.get(previous['uri'], '')])[-3:])]
            for depth in range(2):
                expanded = []
                for value, used, previous, history, artists in beam:
                    available = [p for p in future_pool if p[1]['uri'] not in used]
                    if not available:
                        expanded.append((value, used, previous, history, artists))
                        continue
                    spaced = queue_spacing.spaced(available, artists, self.artist_names)
                    best = min(self.band(p[1]) for p in spaced)
                    same = [p for p in spaced if self.band(p[1]) == best]
                    for pair in sorted(same, key=lambda p: -self.value(p, previous, history))[:4]:
                        item = pair[1]
                        expanded.append((value+.25**(depth+1)*self.value(pair, previous, history),
                            used|{item['uri']}, item, history+[item],
                            (artists+[self.artist_names.get(item['uri'], '')])[-3:]))
                beam = sorted(expanded, key=lambda state: -state[0])[:4]
            return max(state[0] for state in beam)
        return max(top, key=path)
