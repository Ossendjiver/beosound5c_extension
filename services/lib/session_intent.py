"""Bounded, short-lived listening intent, separate from permanent preferences."""
from . import mix_policy, music_features


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


def bonus(item, recent, votes, predecessor, similarity=music_features.similarity):
    """Refine eligible mood/length choices; missing analysis contributes nothing."""
    score = .7 * similarity(item, predecessor or {}) if predecessor else 0.
    weights = [2 ** (-i/2) for i in range(len(recent))]
    if weights:
        score += 1.2 * sum(w*similarity(item, other)
                          for w, other in zip(weights, recent))/sum(weights)
    # A vote changes this session's direction, not catalogue favourites or counts.
    for vote, strength in ((1, .8), (-1, -1.2)):
        examples = [v['item'] for v in votes if v['vote'] == vote and v.get('reason') not in ('overplayed', 'dislike_recording')][-8:][::-1]
        weights = [2 ** (-i/3) for i in range(len(examples))]
        if weights:
            score += strength * sum(w*similarity(item, other)
                                    for w, other in zip(weights, examples))/sum(weights)
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
