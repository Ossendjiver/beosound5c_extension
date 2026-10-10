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
            "energy": .5 + .30*math.sin(theta), "valence": .5 + .30*math.cos(theta),
            "target_version": 2,
            "ring": "familiar" if radius <= CENTRE_RADIUS else "discover",
            "discovery_percent": discovery_percent(radius),
            "discovery_fraction": discovery_percent(radius) / 100}


def resolve(mood, calibration=None):
    """Upgrade persisted targets and interpolate personally annotated sectors.

    Angle still chooses mood and radius still chooses discovery. Calibration
    uses explicit annotations only, never counts or automatically queued tracks.
    """
    if not mood or 'angle' not in mood:
        return mood
    result = selection(mood['angle'], mood.get('radius', .25))
    anchors = (calibration or {}).get('anchors', [])
    nearby = []
    for anchor in anchors if isinstance(anchors, list) else []:
        try:
            delta = abs((float(anchor['angle'])-result['angle']+180)%360-180)
            e, v = float(anchor['energy']), float(anchor['valence'])
            if delta <= 60 and all(math.isfinite(n) and 0 <= n <= 1 for n in (e, v)):
                nearby.append((max(.01, 1-delta/60), e, v))
        except (ValueError, KeyError, TypeError):
            continue
    if nearby:
        total = sum(w for w, _, _ in nearby)
        # Sparse personal annotation refines, rather than replaces, the default.
        strength = min(.85, total/(total+3))
        for key, index in [('energy', 1), ('valence', 2)]:
            result[key] = (1-strength)*result[key]+strength*sum(p[0]*p[index] for p in nearby)/total
        result['calibration_revision'] = (calibration or {}).get('revision', 0)
    return result


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
    from .profile_quality import confidence
    if p['source'] == 'audio_model' and confidence(item.get('audio_features') or {}) < .35:
        return False  # An unstable sample cannot prove a mood match.
    return distance(item, mood, learned) <= maximum + 1e-9


def adjustment(item, mood, learned):
    p = profile(item, learned)
    if not p:
        return -4.0
    distance = math.hypot(p["energy"]-mood["energy"], p["valence"]-mood["valence"])
    return 4.0 - 10.0 * distance


def trajectory(mood, previous, steps=3):
    """Bridge a deliberate steering change; keep discovery independent."""
    if not mood or not isinstance(previous, dict):
        return [mood] if mood else []
    try:
        start = [float(previous[k]) for k in ('energy', 'valence')]
        if not all(math.isfinite(n) and 0 <= n <= 1 for n in start):
            return [mood]
    except (KeyError, TypeError, ValueError):
        return [mood]
    if math.hypot(start[0]-mood['energy'], start[1]-mood['valence']) < .25:
        return [mood]
    return [dict(mood, energy=start[0]+(mood['energy']-start[0])*i/steps,
                 valence=start[1]+(mood['valence']-start[1])*i/steps) for i in range(1, steps+1)]
