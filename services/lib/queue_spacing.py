"""Queue pacing without relaxing candidate eligibility or discovery quotas."""
from . import mix_policy

def slots(familiar, discovery):
    total = familiar + discovery
    return ['discovery' if (i + 1) * discovery // total > i * discovery // total
            else 'familiar' for i in range(total)]

def artist(item):
    return mix_policy.identity(item)[0]

def spaced(pool, recent):
    """Prefer three intervening tracks; relax oldest spacing first if necessary."""
    for gap in range(min(3, len(recent)), -1, -1):
        blocked = set(recent[-gap:]) if gap else set()
        eligible = [pair for pair in pool if not artist(pair[1]) or artist(pair[1]) not in blocked]
        if eligible:
            return eligible
    return pool
