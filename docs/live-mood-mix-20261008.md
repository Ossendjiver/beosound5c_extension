# Live Mood Mix and Pattern Play — 8 October 2026

## Behavior

Both frontends share a BS5c library-owned session attached to a MA queue. A new wheel selection changes only the upcoming tracks using MA `replace_next`; it does not play, stop or seek the current track. The current queue item is checked again after ranking to reject results produced against an older song.

An individual song or playable music item can start a Mood Mix. The first item starts, the collection tail is removed, and the wheel waits for deliberate selection. The backend prepares a normal Pattern Play fallback and applies it in the final five seconds if no selection was made. Paused playback does not trigger that fallback. The monitor refills locally without requiring the phone. A manual replacement outside the session cancels it. Sessions currently end on backend restart; the MA queue itself remains intact.

The centre quarter of the wheel radius is a generous 0% discovery area. The rest maps to every integer from 10% through 90%. The range outlines have been removed. Discovery is a target quota of the chosen candidates, subject to available trusted and relevant music; a 20-track batch cannot express an exact one-percent proportion.

Play Radio from here uses the same local ranking, listening history, trusted seeds and mood feedback instead of enabling MA's provider radio/autoplay. Queue Autoplay in Home Media also controls this local Pattern Play refill. Native MA autoplay is disabled for these queues.

Duration policy separates long mixes at 20 minutes. A normal song seed excludes all long mixes; a long mix seed excludes short tracks. Candidates must be between half and twice the seed duration. Unknown candidate durations are excluded rather than guessed. Duration is retained in the MA library export and favorite metadata; existing exports will gain it on the next source library refresh. Different versions with the same normalized title and artist, including very similar titles, are filtered from the batch and from subsequent played history for that session.

## Frontends

Home Media: active mix gives Mood wheel priority at R1. Queue buttons below the time bar are in a drawer that starts collapsed. Volume buttons are removed from the queue now-playing card. Automatic room selection is locked while the wheel is open. Android Auto exposes Mood wheel as car-safe mood choices followed by 0%, then every discovery percentage from 10–90; choosing again adjusts the phone queue without restarting its current song. The shared BS5c backend must be reachable to use those choices.

BS5c: centred wheel; no main arc context menu while active. The physical pointer controls discovery and the wheel controls angle. Left opens the existing playing queue overlay; right closes that overlay, then returns from the wheel to the music submenu. The current song appears upper left. Idle mood/discovery text pulses after one second.

Sleep timer menus omit implementation notes and show active state. An end-of-track timer also arms an MA timer for the remaining duration, while the phone observes the exact queue item boundary. This MA fallback is approximate if paused or sought afterwards; cancel and successful end-of-track stop clear it. Timed 30/60/90/120 minute timers remain MA-owned.

## Music Assistant findings

Current MA calls the refill behavior Autoplay, while retaining the `dont_stop_the_music` API alias. The alias and `play_media` with `replace_next` are used for compatibility. Dynamic queues use Smart Shuffle, and similar-track generation depends on provider capabilities; a library fallback is useful when providers cannot supply related tracks.

Useful future Pattern Play improvements are stronger recency/artist spacing, provider-capability-aware related candidates, and keeping playlist/source ordering distinct from shuffled radio. These are design recommendations, not claims that the local ranker already implements the full MA dynamic-playlist system. The local ranker remains a trusted-library/context/feedback model rather than an acoustic B&O classifier.

Sources checked:
- https://www.music-assistant.io/settings/core/
- https://www.music-assistant.io/usage/
- https://raw.githubusercontent.com/music-assistant/server/main/music_assistant/controllers/player_queues/controller.py
- https://github.com/orgs/music-assistant/discussions/5129 (design discussion; not treated as a deployed feature)

## Validation and deployment

Offline tests: 778 Python passed, 2 skipped; 102 JavaScript passed. Real browser checks pass for geometry, background, pulse, removal of outlines, captured discovery, title callback, queue overlay mount/content/close and right exit. Python tests include preserving current item and elapsed position, rejecting stale recommendations, five-second/paused fallback, MA command contract, duration separation and recording deduplication.

No live playback commands have been issued. Backend deployment requires the staged installer, which validates current file hashes, backs up files and the listening database, and restarts only `beo-source-mass.service` and `beo-library.service`. Home Media must be installed alongside that backend update to use the new mix API.
