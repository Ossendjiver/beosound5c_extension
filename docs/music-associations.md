# Personal track relationships

Pattern Play and Mood Mix use personal playlist overlap and directional listening
transitions as bounded ranking signals. These are not mood labels: mood distance,
seed duration limits, repeat/version exclusion, disliked tracks and recent-skip
cooldowns remain authoritative. A popular playlist or learned transition cannot
admit an otherwise ineligible track.

## Playlist overlap

The cached Music Assistant playlist tree is indexed locally, and rebuilt whenever
the cache changes. Album/artist folders and dynamic recently-played/history lists
are excluded. Duplicate playlist exports and repeated items within a playlist do
not inflate the signal. Titles retain version/remix words; normalized artist/title
identity and known cached URIs match recordings across providers conservatively.

For tracks A and B, overlap is the sum of `1/(playlist_size-1)` over shared
playlists, divided by the square root of their membership counts. It lies in
0..1. A 500-song favourites playlist contributes far less than a small curated
playlist. The ranking boost is capped at 0.75. The index is inverted membership,
not an expensive all-pairs matrix.

## Directional transitions and skips

The local SSD recommender database contains a `track_relations` table. An accepted,
meaningfully heard A can create a positive A -> B observation only when B was a
manual selection and was itself meaningfully heard. Automatically generated B,
legacy/unknown choices, brief interruptions, same-track replays, long gaps and
cross-room pairs do not create positive edges. Automatic A is allowed if it was
meaningfully accepted, because choosing B after hearing A is still deliberate.

Explicitly skipping B after an accepted A creates negative A -> B evidence even
if B was automatically suggested. Pause, stop and buffering never imply a skip.
Early skips weigh 1; skips after 90 seconds weigh 0.5. Repeated pair skips increase
a directional penalty up to 8 ranking points, in addition to existing global
recent-skip cooldowns. B following a different predecessor does not inherit this
pair penalty. Evidence has a 60-day half-life and 180-day retention. Same-room
observations have full weight; evidence from other rooms has half weight.

Positive transition probability is smoothed by three prior observations and capped
at 1.25 ranking points. Combined positive relationship signals remain at most 2
points. Duplicate app/router observation finishes are ignored and source/target
receipts prevent double counting. A one-time conservative backfill reads existing
history; it never promotes automatic destinations to positive examples or changes
MA play counts. Subsequent evidence persists across backend restarts.

Existing Home Media manual-play selection/listening events feed this backend.
BS5c also reports explicitly queued individual tracks. Queued intent is retained
for up to 72 hours, restricted to the supplied room, and consumed on first actual
play; it earns no transition reward merely for being enqueued. Immediate play
intent expires after 30 minutes. `/library/event` selection payloads may specify
`action=play_next|queue_item|add|next` plus `room`. Clients that do not report queued
intent do not have their automatic queue items guessed to be manual choices.

Regular recommendations use the current room track, or recent accepted room
history as the anchor. Persistent mixes use the currently playing song when
steering and the queued tail when appending; the original seed still controls the
session's duration constraints. Playlist names/styles and counts cannot bypass
mood compatibility.

## Full provider sweep

`beo-provider-sweep.service` runs the metadata-first provider worker once over the
whole current Tidal/SoundCloud inventory, using the private Discogs token when
available. It bypasses ordinary refresh TTLs, resumes an interrupted sweep from
atomic SSD checkpoints, and has a 48-hour service limit with idle I/O, 25% CPU and
512 MB memory limits. Daily profiling takes a non-blocking shared lock and defers
while the sweep owns it. No MA database edits or player/queue commands occur.

Each track checks online metadata before any provider preview, reuses exact local
profiles, and defers sampling while an available MA player is playing/paused or
has unknown state. Visiting the whole catalogue does not mean every track was
classifiable: unavailable tracks, provider errors and busy/deferred previews are
reported separately. `provider_profiles.status.json` is updated at checkpoints;
its `sweep` object records visited/total, completion, pending samples and errors.
A visited-complete sweep can still have samples pending for a later idle run.
