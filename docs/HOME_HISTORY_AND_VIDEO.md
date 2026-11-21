# Hourly Home Media history refresh and metadata-gated video

Home Media already observes BS5c router playback every 12 seconds and records
finished observations in its local history. Playback started directly on BS5c
was therefore not categorically missing. Selection-origin information still
determines whether an observation qualifies for deliberate taste/mood learning.

The former MA familiarity refresh ran daily. `ma_history_sync.run_hourly` now
runs it immediately at service startup and every 3,600 seconds thereafter,
using monotonic scheduling and a 120-second job timeout. Failure retains cached
evidence and does not terminate the observer or queue services.

The refresh also reads up to 200 tracks from MA's authenticated recent-items
API, including partial plays. It resolves metadata through MA's read-only item
API under a bounded time budget and caches the result in the local model's
`ma_recent_history_v1` KV snapshot. No MA database is opened or modified.

These MA results are unique-item history summaries, without reliable individual
play timestamps, room, selection intent or mood. They become familiarity
evidence, not fabricated local listen events or manual rewards. URI deduplication,
the existing frozen evidence counters and existing local-history overlap rules
prevent hourly polling from manufacturing extra listens or preference feedback.
The first live refresh imported 198 of 200 returned items; two lacked resolvable
metadata. Diagnostics expose counts, last sync and the 3,600-second interval.

## Video guard

Showing's display fallback previously substituted an application/source label
for missing artist metadata. A generic AUX label could therefore become a
YouTube lookup. The relay now explicitly indicates whether actual media title
and artist metadata exist. `video_metadata_policy` rejects missing/placeholder
identity, source-only labels (AUX/A.AUX/CD/tape/phono/line input), expired metadata
when supplied, and unverified predictions.

The router clears inherited video/canvas URLs and invalidates pending video
lookup generations when metadata is unavailable. Cached music-video lookup is
gated too. Both asynchronous video and canvas injection recheck current metadata
before publishing, preventing a late result from restarting a fallback video.
Recognised analogue music with actual metadata and valid network music metadata
remain eligible. This policy does not launch, pause, skip or alter physical audio.

## Integration and checks

`tools/apply_home_history_video_policy.py` adds scoped, idempotent hooks to the
installed `services/library.py`, `services/input.py` and `services/router.py`.
It compiles every result and refuses incompatible anchors before writing any
source. This preserves unrelated live Home Media and routing customisations.
Back up those three files and the local recommendation database before applying
to another installation. Install both helper modules in `services/lib/`, then:

```sh
python3 tools/apply_home_history_video_policy.py /home/thomas/beosound5c
sudo systemctl restart beo-router.service beo-input.service beo-library.service
python3 -m unittest discover -s tests -p test_home_history_video.py -v
```

Six offline/native tests cover read-only import, duplicate polling, cache
retention on failure, hourly scheduling and video metadata eligibility. A native
test extracted the actual patched live async video-injection method and verified
that missing metadata prevents publication while real metadata still permits it.
All three services were verified active after installation; the first hourly
job completed and its persisted diagnostics were checked.

Restore the backed-up three service files and restart the same services to roll
back. New helper modules and the new snapshot key can remain unused; no existing
listening history or other application settings need deletion.
