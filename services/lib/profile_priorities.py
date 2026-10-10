"""Order idle profiling requests; never start profiling or change playback."""
from . import profile_quality
import math


def ordered(entries, records, requests=None):
    requests = requests if isinstance(requests, dict) else {}
    def priority(entry):
        request = max((requests.get(alias, 0) for alias in entry.get('aliases', [])+[entry['uri']]
                       if isinstance(requests.get(alias, 0), (int, float)) and not isinstance(requests.get(alias), bool) and math.isfinite(requests.get(alias, 0))), default=0)
        features = (records.get(entry['uri'], {}).get('metadata') or {}).get('audio_features') or {}
        uncertainty = 1-profile_quality.confidence(features)
        return (-request, -uncertainty, entry['uri'])
    return sorted(entries, key=priority)
