# Recommendation improvements: hybrid-v2

This is a GitHub-only implementation of the October 2026 recommendation review.
It changes the shared library backend used by BS5c and Home Media. It does not
deploy services, alter a live queue, install models, or build an APK.

## Implemented

- Corrected wheel geometry: targets occupy a usable central range rather than
  a radius that excluded balanced songs at every angle. This is a transparent
  default, not a claim that mood perception is universally calibrated.
- Radio distinguishes missing audio evidence from confident incompatibility.
  Same-genre fallback cannot override a high-confidence acoustic conflict.
  Loudness/mastering differences alone no longer veto a match.
- Profiles retain coverage, sample duration, patch disagreement, tempo confidence
  and a bounded confidence indicator. Very unstable samples cannot establish a
  mood match. Legacy profiles remain usable conservatively.
- Optional relaxed/aggressive, acoustic/electronic, vocal/instrumental and
  danceability classifiers reuse MSD-MusiCNN patch embeddings. Class names,
  model family, tensor shapes and probability outputs are validated. Missing
  optional heads leave the existing inference path intact.
- Up to eight separate taste clusters use deliberate accepted listens. Room,
  time of day and recent accepted listening select up to three profiles. Their
  modest ranking contribution never admits a wrong-mood or wrong-length track.
- Session feedback can carry `wrong_mood`, `overplayed`, `dislike_recording` or
  `more_like_this`. Overplayed/disliked recordings are excluded for the session
  without treating their entire sound style as unwanted. Pauses, transfers,
  buffering and transport failures provide no taste reward. Long mixes need
  proportionately longer listening before they count as accepted.
- Familiarity explanations distinguish local observations, imported reference
  playlists, provider history and recording novelty. Imported popularity remains
  a small prior, not a mood annotation or training completion.
- Persistent discovery accounting follows actually encountered recordings and
  the pending tail across refills/restarts. Discovery slots are spread through
  the queue; shortages cannot silently exceed the session quota.
- Wheel steering can bridge toward the target over three upcoming selections.
  The currently playing song remains protected by existing queue transactions.
  A two-step, width-four bounded planner balances transition smoothness, rolling
  artist/album diversity and deliberate playlist/listening relations. Hard mood,
  radio, recording/version, skip and length boundaries still apply first.
- Explicit calibration annotations are accepted via the existing event endpoint
  (`type=mood_calibration`, payload `uri`, `angle`). Only measured or manually
  annotated tracks can calibrate a sector; counts and inferred genre moods cannot.
- Decision traces retain reasons for exclusion, score components, evidence source,
  confidence, transition costs and elapsed time. One transaction per ranking run
  retains at most 100 traces / seven days in the recommendation SQLite DB.
  `GET /library/recommend/music?explain=1` explains a run;
  `GET /library/recommend/diagnostics?limit=5&queue_id=...` reads recent traces.
- The idle profiler prioritises requested seeds then uncertain profiles with
  `--priority-file` (JSON URI-to-numeric-priority map). `--upgrade-quality` with
  `--retry-pending` can revisit previews lacking quality metadata. These switches
  preserve shared locks, retry backoff and per-preview playback-idle checks.

The accepted Play Music architecture is preserved: pattern/context chooses the
initial selection, and its continuing session is seed radio. Mood sessions retain
more genre flexibility. The one-third to three-times root duration limit,
short-track/long-mix separation, alternate-version exclusion, recent skip
cooldowns and session-scoped votes remain in force. No MA database migration or
provider play-count rewrite is required.

## Offline verification

`tools/evaluate_recommendations.py` loads saved library/audio-profile files into a
new temporary recommendation database, with no MA, network or playback calls.
It measures queue fill, duration and duplicate violations, adjacent artist repeats,
artist concentration, transition costs and latency over diverse seeds and wheel
angles. Private library/history exports are not included in this repository.

Saved-library validation covered 15,777 catalogue entries, including 9,439
profile-bearing entries/aliases, eight seeds and 40 radio/wheel cases. There were
no duration, duplicate-recording or final-mood eligibility violations. All 32
wheel cases filled 20 slots; one radio case had no eligible recommendations and
returned an empty queue rather than unrelated filler. These are structural results,
not proof of personally satisfying choices. Initial latency on the test computer
was several seconds per full-library request; device performance remains to be
measured before deployment.

Optional `--labels` supplies seed-URI keyed `relevant` / `irrelevant` URI arrays.
The report includes judged precision, judgement coverage and labelled recall;
unjudged songs are not silently counted as bad. `--history-cutoff` excludes future
manual events and untimestamped imported familiarity/feedback/calibration priors.
This is an evaluation foundation, not a trained relevance model or commercial
quality benchmark. Playlist membership itself is not ground-truth relevance.

Example (offline paths only):

```bash
python tools/evaluate_recommendations.py \
  --library /path/to/library.json --features /path/to/audio_features.json \
  --history-snapshot /path/to/library-service-export.json \
  --output /path/to/evaluation.json --seeds 8
```

## Work still needed

1. Validate optional classifier weights against real audio and BS5c runtime/CPU
   budgets before enabling them on the device. A local download attempt timed out;
   inference contract tests are not a substitute for actual model verification.
   The staging installer validates the whole downloaded set before replacing
   existing files. Run it while profiling is stopped; an interrupted replacement
   should be repaired by rerunning installation. No model weights are committed.
2. Collect a small personally labelled set across classical, electronic, vocal,
   instrumental and long-mix seeds. Compare defaults and classifier variants with
   chronological holdouts and component ablations before fitting ranking weights.
   The current confidence number is a quality heuristic, not calibrated relevance.
3. Benchmark dedicated genre/style embeddings or an alternative embedding model.
   The existing embedding plus genre fallback remains the runtime default.
4. Long provider previews still represent one short excerpt. Patch disagreement
   describes that excerpt, not whole-mix coverage. Multi-position provider sampling
   needs a provider-supported audio route and a separate idle/bandwidth evaluation.
   Cached pooled embeddings alone cannot reproduce per-patch head predictions.
5. Add reason/calibration selectors and diagnostic displays in both frontends.
   Existing thumbs controls work; the richer backend fields need UI wiring.
   Automatically feeding current uncertain seeds into the profiler priority file
   is also pending; the ordering itself is implemented.
6. Evaluate opt-in ListenBrainz/public collaborative candidate retrieval and a
   contextual exploration policy once labels and deliberate feedback are sufficient.
   Nothing sends listening history externally in this commit. A personal library
   has little population-level evidence, so a new neural/bandit model alone would
   not establish better recommendations.

The next useful deployment is therefore the deterministic backend changes first,
then optional classifiers after validation, then a measured personal relevance
benchmark. No HA configuration change is needed for the existing API routes.

## Sources informing the design

- [Spotify: contextual and sequential user embeddings](https://research.atspotify.com/2021/4/contextual-and-sequential-user-embeddings-for-music-recommendation)
- [Essentia public music models and metadata](https://essentia.upf.edu/models.html)
- [Essentia danceability output labels](https://essentia.upf.edu/models/classification-heads/danceability/danceability-msd-musicnn-1.json)
- [ListenBrainz recommendation API](https://listenbrainz.readthedocs.io/en/latest/users/api/recommendation.html)

Optional Essentia model weights have separate CC BY-NC-SA licensing; installing
code does not bundle those weights. See `docs/mood-audio-analysis.md`.
