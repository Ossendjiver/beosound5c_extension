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

Local Python suite: 844 passed, 2 skipped. JavaScript suite: 102 passed.
Regression coverage includes incompatible high-history tracks, mood distance
ordering, metadata preservation, bounded enrichment, queue-transition races,
no-match reporting and atomic sidecar writes. The real MusiCNN/emoMusic models
also ran on an offline synthetic sample, producing finite normalized mood
coordinates and a 200-value embedding. That smoke test verifies execution and
schema, not subjective recommendation quality on the user's music. Existing
HA bridge policy permits the unchanged steering and state requests.

## Portable incremental profiler (optional deployment)

`tools/music_audio_onnx.py` uses the published ONNX versions of the same
MusiCNN/emoMusic models with NumPy and CPU ONNX Runtime. The frontend matches
Essentia's 512-point Hann/96-band Slaney mel compression. Three 30-second
sections are read; four evenly spaced 3-second inference patches per section
keep CPU work bounded. Tempo/RMS/centroid use the complete sampled sections.
The spectral-flux BPM estimate is conservative and omitted when weak; it is
not substituted for emotional labels. `sample_seconds` and `inference_patches`
record the actual analysis coverage.

Real-recording parity testing against Essentia's TensorFlow implementation,
using identical model windows, gave a mean embedding absolute error below
0.00001 and valence/arousal output differences below 0.00002. This checks
implementation fidelity, not subjective accuracy of learned mood estimates.
ONNX Runtime provides CPU wheels for Linux ARM64; verify the installed Python
version is supported before deployment.

Prepare the optional environment **on the SSD**, as the BS5c account:

```sh
mkdir -p /media/local/cache/audio-analysis/models
python3 -m venv /media/local/cache/audio-analysis/venv
/media/local/cache/audio-analysis/venv/bin/pip install numpy onnxruntime
```

Model files (same CC BY-NC-SA 4.0 licensing and Essentia attribution):

- [MusiCNN ONNX](https://essentia.upf.edu/models/feature-extractors/musicnn/msd-musicnn-1.onnx)
- [emoMusic ONNX](https://essentia.upf.edu/models/classification-heads/emomusic/emomusic-msd-musicnn-2.onnx)

Copy these into `/media/local/cache/audio-analysis/models`, and copy the
prepared `audio_features.json` atomically beside `mass_playlists.json` on the
SSD. Exact provider aliases share a recording's features. Similar names are
never treated as a provider identity assertion.

`tools/profile_music_library.py` paginates the existing MA catalogue API and
checks locally accessible filesystem mappings. It analyses new/modified files
and model changes, updates aliases without reanalysing unchanged audio, and
retries failed analysis next day. It leaves successful old descriptors intact
on decoder errors or files changing mid-read. There are bounded batches,
one writer lock, atomic/checkpoint writes and a separate
`audio_features.status.json` coverage report. No MA database access or queue
commands are involved. Provider-only tracks with no local audio are explicitly
reported as unprofiled; this worker does not open protected provider streams
or synthesize listening/play-count events.

The accompanying `beo-audio-profile.timer` checks daily at 04:15, with up to
30 minutes of jitter and catch-up after downtime. The service uses idle I/O,
nice 19, a 25% CPU limit and SSD-only writes, processing up to 250 recordings
or one hour per run. The ordinary playback service automatically notices a
changed profile file. More than a daily batch of new music is completed over
subsequent runs; a manual catch-up can use higher explicit limits.

After preparing the optional files, install the timer with:

```sh
sudo bash ~/beosound5c/tools/install_audio_profiler.sh
systemctl list-timers beo-audio-profile.timer
```

The optional timer is deliberately installed separately from ordinary BS5c
services, so deploying UI/backend code alone cannot unexpectedly start a
large audio-analysis job. The installer does not install models/dependencies,
edit HA, or touch music/play-count databases.

### Acoustic range calibration

Real-library testing exposed the model's central score clustering: literal
0/1 wheel targets could have no eligible familiar recordings despite adequate
audio coverage. The sidecar now includes an **unweighted** 5th/95th-percentile
range per model and axis. On loading, the backend maps that measured range to
0..1, clipping the tails. Thus the wheel expresses mood relative to the
profiled collection. Raw model predictions remain unchanged in the profile;
BPM and embeddings are untouched, and manual mood annotations retain priority.
At least 100 profiles and a 0.1-wide axis range are required, so a tiny or
near-constant sample is not stretched into fabricated emotional variety.
Play counts, reference playlists and favourites do not enter calibration.
The daily updater recomputes the ranges after integrating new audio.

Eligibility now validates only the two mood coordinates. It does not
revalidate a 200-number embedding every time a mood distance is computed,
which avoids unnecessary work during steering over a large profiled library.

Long-session recording/version exclusions now use artist-indexed lookups with
unchanged exact/fuzzy recording rules. Real-library queue simulation exercised
six mood changes and 15 consecutive refill batches, preserving the playing
item and its elapsed position each time. Full profile embeddings use six
decimal places without expanding float32 representation noise in JSON.

The normal MA library cache refresh now runs daily at 02:00 (previously a
second day was added), before the audio profiler's 04:15 window. This ensures
new catalogue music reaches the recommendation candidates as well as the
profile file. Both jobs read the supported MA APIs; neither requests playback.

The optional service requires the `/media/local` SSD mount. If it is absent,
profiling is skipped rather than writing its cache into the SD-card root.

A manually selected mix seed remains an artist preference anchor after it is
excluded from upcoming queue items. Same-artist tracks can continue the session
without prior favourites, but must still pass mood, duration, skip and recording
exclusion rules. Unrelated artists retain the normal discovery limits.
