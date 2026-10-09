"""Transparent MoodWheel coordinates; tags are hints, never acoustic analysis."""
import math


CENTRE_RADIUS = 0.25


def discovery_percent(radius):
    radius = float(radius)
    if not math.isfinite(radius) or not 0 <= radius <= 1:
        raise ValueError("Radius must be finite and between zero and one")
    return 0 if radius <= CENTRE_RADIUS else min(90, 10 + math.floor((radius-CENTRE_RADIUS)/(1-CENTRE_RADIUS)*80 + .5))


def selection(angle, radius):
    angle, radius = float(angle), float(radius)
    if not math.isfinite(angle) or not math.isfinite(radius) or not 0 <= radius <= 1:
        raise ValueError("Mood angle must be finite and radius between zero and one")
    angle %= 360
    theta = math.radians(angle)
    return {"angle": angle, "radius": radius,
            "energy": (math.sin(theta) + 1) / 2, "valence": (math.cos(theta) + 1) / 2,
            "ring": "familiar" if radius <= CENTRE_RADIUS else "discover",
            "discovery_percent": discovery_percent(radius),
            "discovery_fraction": discovery_percent(radius) / 100}


TAG_HINTS = {
    "ambient": (0.1, 0.5), "downtempo": (0.2, 0.5), "chill": (0.2, 0.6),
    "relax": (0.15, 0.6), "acoustic": (0.3, 0.5), "jazz": (0.35, 0.55),
    "classical": (0.3, 0.5), "dance": (0.85, 0.7), "disco": (0.8, 0.8),
    "house": (0.8, 0.65), "techno": (0.9, 0.4), "metal": (0.9, 0.2),
    "punk": (0.85, 0.35), "happy": (0.65, 0.9), "upbeat": (0.8, 0.8),
    "melanchol": (0.3, 0.1), "sad": (0.25, 0.1), "dark": (0.55, 0.15),
}


def profile(item, learned):
    explicit = learned.get(item.get("uri")) or item.get("mood_profile")
    if isinstance(explicit, dict):
        try:
            e, v = float(explicit["energy"]), float(explicit["valence"])
            if all(math.isfinite(n) and 0 <= n <= 1 for n in (e, v)):
                return {"energy": e, "valence": v, "source": "manual"}
        except (KeyError, TypeError, ValueError):
            pass
    from .music_features import number
    features = item.get('audio_features')
    # Eligibility needs two validated coordinates, not a re-validation of every
    # 200-value embedding on each distance/ranking call across the full library.
    if isinstance(features, dict) and features.get('model'):
        energy = number(features.get('energy'), 0, 1)
        valence = number(features.get('valence'), 0, 1)
        if energy is not None and valence is not None:
            return {'energy': energy, 'valence': valence, 'source': 'audio_model'}
    # Use supplied genres/curated playlist labels, not guessed title or artist mood.
    tags = str(item.get("genres") or item.get("genre") or "") + " " + str(item.get("playlist_tags") or "") + " " + str(item.get('mood_tags') or '')
    matches = [values for tag, values in TAG_HINTS.items() if tag in tags.casefold()]
    if not matches:
        return None
    return {"energy": sum(p[0] for p in matches)/len(matches),
            "valence": sum(p[1] for p in matches)/len(matches), "source": "tag_hint"}


def distance(item, mood, learned):
    p = profile(item, learned)
    if not p:
        return None
    return math.hypot(p['energy']-mood['energy'], p['valence']-mood['valence'])


def compatible(item, mood, learned):
    p = profile(item, learned)
    if not p:
        return False
    # Tags are coarse hints, so do not stretch them to far-away wheel positions.
    maximum = .28 if p['source'] == 'tag_hint' else .35
    return distance(item, mood, learned) <= maximum


def adjustment(item, mood, learned):
    p = profile(item, learned)
    if not p:
        return -4.0
    distance = math.hypot(p["energy"]-mood["energy"], p["valence"]-mood["valence"])
    return 4.0 - 10.0 * distance
