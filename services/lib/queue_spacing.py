"""Queue pacing without relaxing candidate eligibility or discovery quotas."""
from . import mix_policy

def slots(familiar, discovery):
    total = familiar + discovery
    return ['discovery' if (i + 1) * discovery // total > i * discovery // total
            else 'familiar' for i in range(total)]

def artist(item):
    return mix_policy.identity(item)[0]

def spaced(pool, recent, artist_names=None):
    """Prefer three intervening tracks; relax oldest spacing first if necessary."""
    tagged = [(pair, artist_names.get(pair[1].get('uri'), '') if artist_names is not None else artist(pair[1])) for pair in pool]
    for gap in range(min(3, len(recent)), -1, -1):
        blocked = set(recent[-gap:]) if gap else set()
        eligible = [pair for pair, name in tagged if not name or name not in blocked]
        if eligible:
            return eligible
    return pool
