# Mood-first selection and acoustic analysis

## Why this approach

Bang & Olufsen describes MOTS as analysing a library for a similar musical
signature to a selected root recording ([B&O press release, page 4](https://investor.bang-olufsen.com/static-files/449178cb-f00d-4676-83e1-3283f3c995c4)).
Its proprietary implementation is not public. This implementation draws on the
idea, without claiming to reproduce MOTS.

Spotify separates audio-derived track representations from listening-derived
user representations ([Spotify Research](https://research.atspotify.com/2025/9/generalized-user-representations-for-large-scale-recommendations)).
BS5c similarly separates sound/mood evidence from familiarity. Play counts say
whether a recording is familiar, not whether it fits the mood wheel.

[Essentia](https://essentia.upf.edu/models.html) provides audio embeddings,
tempo extraction and trained arousal/valence models. BPM is useful for rhythmic
continuity but does not establish emotional valence. Genre/playlist labels are
coarse hints; the model-based descriptors are preferred when available.
AcousticBrainz's historical data is not assumed to cover this streaming library.

## Selection behavior

Mood requests reject unclassified or incompatible tracks before history or play
counts are considered. Within the eligible familiar/discovery pools, mood
distance is ranked in 0.05-wide bands, then preference and familiarity rank
tracks within a band. The existing discovery quota remains in effect. Explicit
track annotations take precedence over audio-model predictions, which take
precedence over supplied genre/curated-playlist hints. Titles and artist names
are never used to guess emotions.

Tempo and same-model embedding similarity contribute at most one ranking point
after mood/length/skip eligibility. Half/double tempo estimates are reconciled.
Existing root length bounds, normal-song versus long-mix separation,
recording/version exclusions and escalating recent-skip penalties remain.

The current song is preserved. A mood choice replaces its upcoming tracks. If
no eligible tracks have sufficient metadata, the incompatible tail is cleared
and an explicit error returned. The mix exposes `refresh_status`,
`pending_refresh`, and `applied_mood`; it retries insufficient coverage after
15 minutes. These are additional response fields, not changed request fields.
Ordinary refill shortages preserve an already compatible tail. Current-item
races remain pending and retry even when the old queue is full. Requests and
refills are bounded to avoid tight polling on failure.

## Metadata enrichment

The backend gradually reads full track metadata through MA's supported API,
up to 24 records per 15 minutes by default. Results are cached locally, with
successful reads refreshed after 30 days and failed reads retried next day.
Empty values never erase useful metadata. This retrieves what providers have;
it cannot manufacture missing mood or acoustic descriptors.

This uses the existing MA URL/token environment. It does not open MA's database.
The BS5c `library.metadata_enrichment_batch` option accepts 0–40 (0 disables).
One local SQLite transaction saves each batch, rather than per-track writes.

## Higher-quality audio analysis

Run `tools/analyze_music_audio.py` separately, preferably on a desktop/server
with access to the audio files. It is not run in the BS5c playback process,
and no heavyweight package is added to the standard BS5c installation.
It currently accepts **local files**, not protected provider streams. Streaming
catalogue entries without accessible audio retain their provider metadata;
the worker is not a solution for those missing files by itself.

Requirements: ffmpeg/ffprobe, NumPy, and Essentia with TensorFlow support.
For compatible Linux/Python environments a prerelease wheel is available:

```sh
python3 -m venv audio-analysis-env
audio-analysis-env/bin/pip install numpy --pre essentia-tensorflow
```

See [Essentia installation instructions](https://essentia.upf.edu/installing.html)
for other architectures. Model weights are separate from the repository:

- [MusiCNN embedding model](https://essentia.upf.edu/models/feature-extractors/musicnn/msd-musicnn-1.pb)
- [emoMusic arousal/valence head](https://essentia.upf.edu/models/classification-heads/emomusic/emomusic-msd-musicnn-2.pb)
- [Model metadata/output order](https://essentia.upf.edu/models/classification-heads/emomusic/emomusic-msd-musicnn-2.json)

The models are [CC BY-NC-SA 4.0](https://essentia.upf.edu/models.html); they are
not bundled or downloaded automatically by BS5c.

Create a manifest with exact MA recording URIs, not fuzzy artist/title matches:

```json
[{"uri":"library://track/123", "path":"/music/example.flac"}]
```

Then run:

```sh
audio-analysis-env/bin/python tools/analyze_music_audio.py manifest.json \
  --models /path/to/model-files --output /path/to/audio_features.json
```

The worker samples up to three 30-second sections, infers and averages mood
coordinates and embeddings, measures tempo separately per section, and stores
BPM, RMS and spectral centroid. RMS/centroid are descriptors, not emotional
labels. emoMusic returns valence/arousal on a 1–9 scale; these are converted to
0–1, in the documented order. Predictions are estimates, not ground truth.

Fingerprints include file and model changes. Unchanged recordings are skipped;
completed analysis is saved by atomic rename with fsync for resumability. Only
descriptors, not audio, go to BS5c. The sidecar is reloaded when its file changes.
Set `library.audio_features_file` or `BS5C_AUDIO_FEATURES_FILE`; otherwise place
it beside the cached library as `audio_features.json`.

## Home Assistant and app follow-up

No HA entity, automation, script, or bridge whitelist change is needed. Existing
`mix`, `mix_state`, `mood`, and recommendation routes are retained. Analysis
files live on BS5c, outside HA. No HA restart is required by these code changes.

One **recommended bridge update**: its current client maps all non-2xx backend
responses to a generic 502 service-unavailable response. Expected mood/queue
rejection reasons (such as insufficient matching metadata) should be translated
to bounded, safe error codes/messages instead. Until that separate update,
remote callers can still retrieve `refresh_status` through `mix_state`, but
the failed update itself gets a misleading availability error. No bridge code
or live HA configuration was changed in this BS5c update.

Home Media should subsequently show the new refresh/no-match status and retain
the backend error body. Its separate automatic-room/manual-selection problem
requires an app navigation fix, not a HA script workaround. These frontend
changes are not included in this BS5c backend commit.

Publishing code does not install models, analyse the library, or deploy BS5c.
Until audio is available and analysed, strict mood matching can produce fewer
tracks rather than pretending unclassified high-count tracks fit the wheel.

## Validation for this revision

Local Python suite: 828 passed, 2 skipped. JavaScript suite: 102 passed.
Regression coverage includes incompatible high-history tracks, mood distance
ordering, metadata preservation, bounded enrichment, queue-transition races,
no-match reporting and atomic sidecar writes. The real MusiCNN/emoMusic models
also ran on an offline synthetic sample, producing finite normalized mood
coordinates and a 200-value embedding. That smoke test verifies execution and
schema, not subjective recommendation quality on the user's music. Existing
HA bridge policy permits the unchanged steering and state requests.
