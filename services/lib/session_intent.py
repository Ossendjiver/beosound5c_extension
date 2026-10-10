"""Bounded, short-lived listening intent, separate from permanent preferences."""
import math
from . import mix_policy, music_features, music_mood, profile_quality, radio_genres


def accepted(history, candidates, room, now):
    """Actual accepted listens from the last 90 minutes, never queued suggestions."""
    by_uri = {c.get('uri'): c for c in candidates if c.get('uri')}
    recent = []
    for row in history:
        row = dict(row)
        if row.get('room') != room or not 0 <= now-row['ts'] <= 5400:
            continue
        if row.get('end_reason') in ('skip', 'dislike') or row.get('reward', 0) <= 0:
            continue
        item = by_uri.get(row.get('uri'))
        if item is None:
            item = next((c for c in candidates if mix_policy.same_recording(c, row)), None)
        if item and not any(mix_policy.same_recording(item, old) for old in recent):
            recent.append(item)
        if len(recent) == 5:
            break
    return recent


def feedback_similarity(item, other, reason, similarity=music_features.similarity):
    """One feedback dimension; unknown descriptors never invent a match."""
    if reason in ('overplayed', 'dislike_recording'):
        return float(mix_policy.same_recording(item, other))
    if reason == 'track_match':
        if mix_policy.same_recording(item, other):
            return 1.
        features = getattr(similarity, 'features', None)
        a = features(item) if features else music_features.clean(item.get('audio_features'))
        b = features(other) if features else music_features.clean(other.get('audio_features'))
        x, y = a.get('embedding'), b.get('embedding')
        if not x or not y or len(x) != len(y) or not a.get('embedding_model') or a['embedding_model'] != b.get('embedding_model'):
            return 0.
        cosine = sum(i*j for i,j in zip(x,y))/math.sqrt(sum(i*i for i in x)*sum(j*j for j in y))
        return max(0., min(1., cosine))
    if reason == 'genre_match':
        a, b = radio_genres.labels(item), radio_genres.labels(other)
        if not a or not b:
            return 0.
        if radio_genres.specific(a) & radio_genres.specific(b):
            return 1.
        if radio_genres.style_distance(a, b) == 3:
            return 0.
        return .5 if radio_genres.dominant(a) & radio_genres.dominant(b) else 0.
    if reason in ('mood_match', 'wrong_mood'):
        a, b = music_mood.profile(item, {}), music_mood.profile(other, {})
        if not a or not b:
            return 0.
        for record, profile in ((item, a), (other, b)):
            if profile['source'] == 'audio_model' and profile_quality.confidence(record.get('audio_features') or {}) < .35:
                return 0.
        return max(0., 1-math.hypot(a['energy']-b['energy'], a['valence']-b['valence'])/.35)
    if reason == 'tempo_match':
        a = music_features.clean(item.get('audio_features'))
        b = music_features.clean(other.get('audio_features'))
        if not a.get('bpm') or not b.get('bpm') or min(a.get('bpm_confidence', .6), b.get('bpm_confidence', .6)) < .35:
            return 0.
        delta = min(abs(math.log2(a['bpm']*ratio/b['bpm'])) for ratio in (.5, 1, 2))
        return max(0., 1-delta/.25)
    return similarity(item, other)  # Unqualified and legacy positive votes.


def bonus(item, recent, votes, predecessor, similarity=music_features.similarity):
    """Refine eligible mood/length choices; missing analysis contributes nothing."""
    score = .7 * similarity(item, predecessor or {}) if predecessor else 0.
    weights = [2 ** (-i/2) for i in range(len(recent))]
    if weights:
        score += 1.2 * sum(w*similarity(item, other)
                          for w, other in zip(weights, recent))/sum(weights)
    # A vote changes this session's direction, not catalogue favourites or counts.
    for vote, strength in ((1, .8), (-1, -1.2)):
        examples = [v for v in votes if v['vote'] == vote and v.get('reason') not in ('overplayed', 'dislike_recording')][-8:][::-1]
        weights = [2 ** (-i/3) for i in range(len(examples))]
        if weights:
            score += strength * sum(w*feedback_similarity(item, example['item'], example.get('reason'), similarity)
                                    for w, example in zip(weights, examples))/sum(weights)
    return max(-3., min(3., score))


def reward(seconds, duration, origin, reason, replay=False):
    if reason in ('error', 'network_error', 'transport_error', 'disconnect', 'route_change', 'transfer', 'buffering', 'pause'):
        return 0.  # Technical events and pauses are not taste observations.
    if reason == 'dislike':
        return -4.
    if reason == 'skip':
        return -2. if seconds < 25 else -1.
    meaningful = seconds >= (min(900, duration*.20) if duration >= 1200 else 90) or duration > 0 and seconds >= duration*.45
    if not meaningful:
        return 0.
    # Autoplay completion is weak acceptance, never equivalent to choosing a song.
    if origin != 'manual':
        return .05 if origin == 'automatic' else .15
    fraction = min(1., max(0., seconds/duration)) if duration > 0 else .45
    return 1. + .5*max(0., (fraction-.45)/.55) + (.25 if replay else 0.)
