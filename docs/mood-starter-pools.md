# Backend mood starter pools

The library backend maintains **16 angular sectors × 5 active starter tracks**. Each sector has a bounded reserve (up to 20 familiar and 20 discovery candidates). A separate set of 16 long-form sectors prevents long SoundCloud mixes from displacing ordinary songs. These are private backend shortlists, not new MA library playlists or queues.

## Preparation and learning

The library service starts its own incremental maintenance loop. It loads the existing saved library/profiles, prepares one sector at a time, and refreshes sectors every 15 minutes. Selection reservations and shortages prioritise the affected sector; urgent rebuilds are limited to once per 30 seconds per sector. New profiles/catalogue metadata and saved personal calibration are noticed on the next backend catalogue check (at most a minute).

Preparation uses the existing recommendation model: favourites, trusted playlists, provider/MA familiarity, deliberate listening, time/context preferences, playlist overlap and transitions, long-term history and recent feedback. It does not create a separate popularity-first model. Normal history retention/decay remains unchanged. A successful refresh retains up to two eligible unused starters and replenishes the other slots, favouring unused candidates. Reserves retain both discovery categories and several duration bands.

Background scoring runs in a worker using a separate **read-only SQLite connection**. It never shares the live writer, changes database schema, starts audio analysis, contacts providers, or sends player/queue commands. Old pools remain available if refresh fails. Shutdown cancels maintenance; any already-running read-only worker can finish without accessing the closed live connection.

## Fast path and rotation

Both Home Media and BS5c use the existing library/mix endpoints, so they share these pools without an app or UI update. A warm mood request ranks at most 720 prepared references (usually fewer after URI deduplication), rather than scoring the entire catalogue. The actual angle and every discovery percentage remain continuous; the 16 preparation sectors do not quantise the wheel itself.

The live model rechecks mood compatibility, calibration, recent skip penalties, dislikes/session votes, duplicate recordings and versions, session history, artist spacing, discovery balance, root-duration bounds and gradual transitions. Current-track metadata can still be looked up by URI from the backend's full in-memory index even when it is not a starter candidate.

The first five selections reserve those recordings for rotation for two hours. These reservations **do not create listens, provider counts or preference feedback**. Repeated selections prefer unused candidates; if all are reserved, the last opening recording is avoided where another eligible choice exists. A one-track or otherwise sparse safe pool may repeat rather than admit an incompatible track.

When a running session exhausts its prepared candidates, it requests a targeted background refill carrying the root duration and session exclusions. A successful refill releases that mix's shortage retry so the existing queue monitor can continue promptly. The background worker itself never starts or alters playback. Steering retains the currently playing item/position and replaces only upcoming tracks through the existing guarded mix mechanism.

First-time/unprepared sectors use the existing catalogue-ranking fallback while the backend warms them. This uses saved profiles; it does not sample or recalibrate audio. Restarts hydrate saved URI references from current metadata. Missing/stale tracks disappear from the index. Genuinely insufficient mood data is reported as a shortage instead of filling with popular mismatches.

## Persistence and inspection

`mood_starters_v1` in the existing library database stores only bounded URI pools, timestamps and recent rotation reservations—no duplicate audio files, embeddings or profiles. Dirty state is checkpointed at most once a minute and on clean shutdown. A crash can lose the last minute of rotation reservations, but cannot fabricate listening history.

`GET /library/mood` includes `starters` status with per-sector slot counts, refresh timestamps and the last refresh error. Recommendation audit traces include `starter_cache` candidate count, sector and whether the fast path was used. Existing HA bridge endpoints continue to carry the same requests; no HA change is required.

## Validation

- Unit/integration tests cover repeated starts, continuous discovery, live skips/dislikes, profile changes/removals, separate long-form pools, duration diversity, root-length/version exclusions, persisted references, read-only background ranking and failure recovery.
- A warm mix steering test confirms the current item and elapsed position survive upcoming-track replacement.
- Frozen local snapshot: 15,777 entries; 80 normal and 80 long-form active slots filled. Eight angle/discovery checks had no cases where broad ranking found tracks but the warm path returned none. Mean selection time was 1,707 ms broad versus 378 ms warm (about 4.5× faster). Full background preparation took 68 seconds in the offline evaluation. These are Ubuntu measurements on saved data, not live BS5c timings or human validation of mood labels.
- No deployment, live playback commands or new APK form part of this change.
