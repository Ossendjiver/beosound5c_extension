# Trusted music baseline

The library recommendation service uses `trusted-baseline-v2`. This replaces unrestricted catalogue exploration with a seeded pool. No APK or speaker playback is needed to install this service change.

## Seed configuration

Music Assistant favourite tracks always seed the pool. The service refreshes them in the background every 15 minutes and retains the cached seeds if MA is unavailable. Playlist names are matched without case sensitivity. The default trusted playlist names are `Trusted music` and `All favorited tracks`.

Add curated playlists to the existing `library` section in device configuration:

```yaml
library:
  trusted_playlists:
    - Trusted music
    - All favorited tracks
    # Add an existing playlist you deliberately trust here.
```

Do not include random playlists, automatically generated genre pools, news, or meditation. With no trusted tracks the service returns an empty recommendation list instead of sampling the entire catalogue. Small pools return shorter queues.

## Ranking and learning

At least 90% of each returned queue is familiar music. At most 10% is unfamiliar music from artists already in the seed pool. Provider aliases are deduplicated by artist and title. An explicit dislike excludes all observed aliases of that recording.

A meaningful deliberate listen (90 seconds or 45% of the track) has more influence than automatic playback. Automatic and unknown-origin observations cannot expand the trusted baseline. Short pauses, stops, transfers and unspecified interruptions are neutral. Only an explicitly reported early skip is negative; an interruption is never assumed to be a skip. Spoken audio, podcasts, news and meditation are excluded from music learning and recommendations.

Room/time/weather adjustments begin only after at least 20 meaningful manual observations spanning five tracks. They are modest adjustments inside the seeded pool. History decays over 28 days. Recently heard tracks receive a small penalty rather than being replaced by unrelated catalogue tracks.

Existing SQLite history is retained. Rows created before this change receive origin `legacy`; their positive influence is reduced and old short interruptions no longer supply negative training.

## Event contract and current limits

`POST /library/event` accepts `listen_start` with `selection_origin` set to `manual` or `automatic`, and `listen_stop` with `reason` set to `skip`, `pause`, `transfer` or `stop`. Listeners that do not supply explicit origin or reason are treated conservatively as unknown. The existing passive router monitor does not reliably identify deliberate choices or explicit skips; this change does not invent those signals. Recommendations returned by this service are recognised as automatic observations. Manual-learning context therefore remains gated until clients supply reliable deliberate selection events.

Explicit feedback can be recorded using:

```json
{"type":"feedback","uri":"library://track/123","action":"dislike"}
```

Supported actions are `like`, `dislike` and `clear`. This is an API contract; no new dislike button is added by this backend change.

## Verification and deployment

Run `python -m pytest tests/unit/python/test_library_service.py -q`. Tests cover cold start, discovery quota, provider aliases, legacy database migration, neutral interruptions, trusted playlist propagation and spoken-audio filtering.

A read-only check of the current device cache on 8 October 2026 found six favourited tracks among 14,316 catalogue entries. The offline preview returned only those trusted tracks and did not play anything. A broader deliberately curated playlist is needed for a useful initial mix.

Before device deployment, back up `services/library.py` and make a SQLite backup of the learning database. Preserve device configuration and unrelated local source changes. Replace only `services/library.py`, restart only `beo-library.service`, then verify `/library/recommend/music` reports `trusted-baseline-v2`. Deployment needs SSH access; this document does not claim deployment has occurred.
