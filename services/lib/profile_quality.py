"""Validated profile uncertainty and coverage; independent of play counts."""
import math


def finite(value, low=0., high=1.):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) and low <= value <= high else None
    except (TypeError, ValueError):
        return None


def confidence(features):
    """A quality indicator, not a calibrated probability of musical relevance."""
    explicit = finite(features.get('profile_confidence'))
    if explicit is not None:
        return explicit
    if features.get('analysis', '').startswith('provider-preview'):
        return .55
    if features.get('model') and features.get('embedding_model'):
        return .70  # Legacy profiles remain usable, with conservative certainty.
    return .60 if features.get('model') else .25


def summary(section_moods, sections, sample_seconds, tempo_confidences=()):
    """Record patch disagreement; short previews are not whole-track evidence."""
    pairs = [(finite(p.get('energy')), finite(p.get('valence'))) for p in section_moods]
    pairs = [(e, v) for e, v in pairs if e is not None and v is not None]
    if not pairs:
        return {}
    means = [sum(p[i] for p in pairs)/len(pairs) for i in (0, 1)]
    deviations = [math.sqrt(sum((p[i]-means[i])**2 for p in pairs)/len(pairs)) for i in (0, 1)]
    variation = max(deviations)
    quality = max(.25, min(.90, .55+.10*min(3, max(0, sections-1))-1.5*variation))
    result = {'profile_confidence': round(quality, 4), 'profile_quality_version': 1,
              'sample_sections': sections, 'sample_seconds': sample_seconds,
              'mood_variation': {'energy': round(deviations[0], 4), 'valence': round(deviations[1], 4)},
              'section_moods': [{'energy': round(e, 4), 'valence': round(v, 4)} for e, v in pairs[:32]],
              'coverage': 'multi_section' if sections > 1 else 'preview_only'}
    usable = [finite(c) for c in tempo_confidences]
    usable = [c for c in usable if c is not None]
    if usable:
        result['bpm_confidence'] = round(sum(usable)/len(usable), 4)
    return result


def classifiers(value):
    allowed = {'relaxed', 'aggressive', 'happy', 'sad', 'acoustic', 'electronic',
               'voice', 'instrumental', 'danceability', 'speech'}
    if not isinstance(value, dict):
        return {}
    return {key: finite(v) for key, v in value.items() if key in allowed and finite(v) is not None}
