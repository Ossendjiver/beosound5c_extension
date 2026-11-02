# MA Endless Mix recommendations in the local selector

Music Assistant supplies candidate tracks through its public
`music/tracks/similar_tracks` API. BS5c continues to own ranking and queue refill.
No dynamic playlist is created and MA autoplay is not enabled alongside the
local session worker. The existing MASS queue writer still disables MA autoplay
before appending/replacing upcoming tracks.

## Selection

* Song radio queries the original root and, as the session progresses, a locally
  compatible current/tail anchor. Every resulting candidate must still pass the
  original root's audio/genre checks. Unvalidated provider suggestions never
  become stepping stones, preventing an uncontrolled similarity-graph walk.
* Mood and Pattern Play query up to three anchors chosen by the existing local
  predictor. The sixteen starter sectors also prefetch recommendations in the
  background. Available results join the candidate pool before the existing
  sequence planner runs.
* Unknown mood remains unknown: provider recommendation membership, popularity
  or play counts do not create a mood label. Full metadata is requested for at
  most three descriptor-poor results per batch. Exact provider mappings hydrate
  canonical local metadata and already-available audio profiles. No artist/title
  guess is used to attach an acoustic profile.
* Mood compatibility, gradual steering, root-relative 1/3–3x duration, long-mix
  separation, recording/version deduplication, artist spacing, recent-skip
  cooldowns, transition aversion and session/global feedback remain enforced.
* Recommendations do not create favourites, trusted tracks or listens. New
  Pattern Play discoveries require independently compatible sound/genre data;
  discovery quotas remain unchanged. A modest 0.25 preference tie-break is only
  applied after compatibility and skip checks. Provider rank/popularity is not
  imported as personal preference.

## Runtime and storage

Wheel and queue requests never await the provider. They use cached results and
prioritise their anchors for the existing library service's background worker.
Cold, offline or unsupported MA/provider requests retain the local selection
path. New results can wake a relevant starved session; they do not replace a
healthy queue, restart playback or wake unrelated sessions.

The cache holds at most 64 seed batches of 40 compact track descriptors. Fresh
results last six hours; successful stale results can be used for seven days
while being refreshed. Empty results and failures retry after fifteen minutes.
Calls have timeouts, are serialised and paced, and HTTP 429 Retry-After applies
globally. A short global cooldown also prevents a burst across every seed while
MA is unavailable. Active sessions take priority over background sector work.

The cache is checkpointed at most once per minute, plus service shutdown, to
`ma_recommendation_candidates_v1` in the existing SSD recommendation database.
Embeddings, credentials, stream URLs and opaque provider payloads are not stored
in this cache. Exact mapping indexes are prepared during the background library
bind, avoiding a full catalogue scan for each wheel interaction.

`GET /library/context` exposes `ma_recommendations` cache/pending/error counts.
Recommendation audit traces record requested seeds, candidate counts and each
selected track's recommendation provenance. Set `library.ma_recommendations` to
`false` in the private config to disable this source without changing local
recommendation/refill behaviour.

## Compatibility and deployment

The API arguments were checked against MA 2.10.0 and current upstream code.
Unsupported API/provider responses are treated as unavailable candidate sources,
not permission to use unrelated music. Recommendation quality and metadata
coverage still depend on the connected providers. A track with insufficient
metadata may remain ineligible until its existing profile/metadata is available.
The worker does not start audio sampling or change the nightly analysis window.

Deploy `services/library.py` and `services/lib/ma_recommendations.py` together,
retaining private configuration and the SSD database. Only `beo-library` needs
restarting; no MA/HA restart, APK build or new system service is required. This
change is staged on GitHub, not installed by its offline tests.

## Verification

Dedicated tests cover read-only API calls, cache-only request paths, bounded
metadata reads, persistence, priority, in-flight deduplication, cancellation,
provider/API failure, 429 backoff, exact-mapping hydration, discovery quotas,
unknown/incompatible mood and genre rejection, long mixes, cross-provider recent
skips, and relevant-only shortage retry.

Eight additional offline cases use the saved 15,777-track library snapshot with
simulated MA responses drawn from its actual tracks: three genre radio seeds,
one long-form seed and four mood directions. All produced twelve selections
with zero compatibility, length or recording-duplicate violations. These are
integration/policy checks, not a claim of live provider recommendation quality.

Upstream references:

* https://www.music-assistant.io/usage/#endless-mix
* https://www.music-assistant.io/settings/core/#player-queues
* https://github.com/music-assistant/server/blob/2.10.0/music_assistant/controllers/music/media/tracks.py
* https://github.com/music-assistant/server/pull/6254
