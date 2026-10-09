# MoodWheel and shared PatternPlay-style learning

The mood wheel and automatic recommendations use the same SQLite listening history, favourite/trusted-playlist pool, explicit feedback, room, weather and time context. Existing news, sleep and post-run yoga behavior remains separate from music learning. This is a transparent local implementation inspired by documented B&O behavior, not a reproduction of its proprietary algorithm.

## OEM comparison

B&O documents MoodWheel as bright/happy at the top, dark/contemplative at the bottom, relaxed at the left and energetic at the right. The inner ring represents familiar collection music, the middle blends familiar and unfamiliar music, and the outer offers discovery. PatternPlay tracks listening alongside weekday/time and offers a one-touch appropriate mix.

Primary references:
- [B&O Beosound history: MoodWheel and PatternPlay](https://www.bang-olufsen.com/en/de/story/beosound-history)
- [Original B&O user guide, pages 8–9](https://bnoservice.nl/media/manuals/BEOSOUND/Beosound%20Moment/User%20Guide_MULTI_b2200b75ecf57cf0a6131484488e3356.pdf)
- [B&O product sheet: PatternPlay day/time and MoodWheel](https://bangolufsenassistentgohe.blob.core.windows.net/manuals/SOUND_SYSTEMS/BEOSOUND_MOMENT/BeoSound_Moment.pdf)

Public documentation does not expose the proprietary music analysis, model weights or training code. These sources establish interaction and behavioral goals, not algorithm equivalence.

## Controls

BS5c: Music submenu → Mood wheel, immediately above Search. Turn the navigation wheel to rotate the selection. Left/right move through the three layers: the button toward the screen edge moves outward, and the other moves inward. Laser input is ignored while open. GO starts the selected mix; hold GO or use the Back icon to exit. Mood and discovery labels fade slowly in and out only after one second without movement. Mouse/touch and keyboard controls are also available. Selecting a mood does not start playback until GO/Play.

Home Media: Music library → Mood wheel. Drag the point, or use the mood/discovery sliders. Play targets the current room music player or local Music Assistant player using existing routing/default-player logic. The shared library currently requires home Wi-Fi. No separate phone recommender is introduced.

The shared coordinate contract is clockwise degrees from north and radius 0–1. Familiar (inner third) has no discovery. Blend (middle third) caps discovery at 10%. Discover (outer third) explicitly permits up to 50%, from known artists or tracks with supplied matching mood hints. Ordinary automatic recommendations retain the 90/10 baseline. An empty seed pool never falls back to random catalogue music.

## Integration with existing learning

Purposeful individual track selections from the BS5c and Home Media music library mark manual choices; passive listening remains weak evidence. Successful mood playback registers its selected tracks and atmosphere. Subsequent listening outcomes are stored alongside existing day/time/room/weather data. PatternPlay-style automatic recommendations can use the preferred atmosphere once at least five mood-context listening observations exist. Explicit wheel coordinates override that suggestion.

A completed listen is evidence of preference in that atmosphere, not an acoustic label for the track. Negative explicit skips/dislikes remain distinct from neutral pauses, stops and transfers. Old history remains available with reduced influence. Music context adjustment remains gated by sufficient deliberate history.

Genre tags and curated playlist labels provide coarse mood hints. Unknown tracks remain explicitly unclassified; the API and interfaces report when mood coverage is limited. Titles and artist names are not used to invent acoustic mood. Manual mood annotations can be supplied through `mood_profile` events. Reliable valence/energy analysis is a future extension, not silently simulated.

## API

- `GET /library/recommend/music?room=lounge&limit=20&angle=90&radius=0.5` returns one mood mix plus coverage information; read-only.
- `GET /library/mood?room=lounge` returns a pattern-derived atmosphere or `not_enough_history`.
- `POST /library/event` with `type=selection`, track `uri`, `title`, `artist`, and `media_type` marks a purposeful track choice.
- `type=mood_session`, `angle`, `radius` and `items` registers an accepted mood playback session. Clients must not register preview-only requests.
- `type=mood_profile`, `uri`, `angle` and `radius` explicitly annotates a track's mood.

## Verification and deployment

Python tests cover axes, rings, invalid values, seed preservation, discovery quotas, learning integration and history migration. JS and Kotlin tests verify shared pointer geometry and ring boundaries. Android compilation and unit tests are required; this source change does not request or produce a new APK.

Device deployment replaces the library service, its new pure mood helper, the MASS iframe and new wheel JS/CSS. The hardware-input change is a narrow pointer interception applied to the device's existing file, preserving unrelated local edits. Back up source and SQLite first. Restart only the library service; no Music Assistant, router, Home Assistant or speaker restart is required. Refresh the BS5c browser to load the UI. Verification uses read-only endpoints and offline UI interaction, without starting speakers.

### Imported familiarity (trusted-baseline-v3)

Both clients use the shared library service. It now reads a separate familiarity
snapshot every 15 minutes; neither the APK nor the wheel maintains a second learner.

- Music Assistant: read up to 500 tracks ordered by `play_count_desc`. Use numeric
  `play_count` when the API returns it. Versions which omit that field can supply a
  smaller ordinal signal only for library tracks with a positive `last_played`.
  An unplayed row gets no signal just because it appears in the ordered list.
- Providers: inspect the provider's explicit personal history folders, including
  SoundCloud's `recently-played` dynamic playlist. Related recommendations, “Top
  Tracks”, editorial charts, and public popularity/playback counts are excluded.
  Explicit `user_play_count`/`personal_play_count` are supported within history.
- Numeric counts add a logarithmic score capped at **0.25**. A history appearance
  adds at most **0.10**; count-order-only evidence adds at most **0.08**. Take the
  strongest overlapping source rather than adding MA/provider counters together.
- Each source/track's first positive snapshot is frozen, not incremented on every
  poll. Locally observed meaningful plays (manual or automatic) are subtracted;
  learning from those outcomes already occurs in `listens`. This prevents an
  automatic mix from growing its own imported reward. Priors decay with a 90-day
  half-life and expire after 180 days.
- History does not mark tracks as favourites/trusted, enlarge the trusted seed
  pool, teach a mood/time/room, or override dislikes, discovery quotas, recording
  deduplication or track-length policy. New history metadata remains untrusted.
- Snapshot reads have per-call and total time limits. Unsupported providers and
  temporary failures retain the existing cached evidence. No provider credentials
  or personal history are published in GitHub. The MA calls are read-only.

The SQLite `kv` entries `music_familiarity` and `music_familiarity_status` store the
separate cache and import diagnostics. Recommendation responses include a small
`familiarity` status object. Remove `music_familiarity` to deliberately reset the
frozen import baseline; do not reset it on ordinary polling or queue changes.

Deploy `services/library.py` and `services/lib/music_familiarity.py` together and
restart only `beo-library`. Existing preference history is retained. No APK build,
MA database write, HA change, or playback action is needed.


## Personal “Most played…” baseline and continuing queues

MA library playlists whose names start with `most played` (case insensitive,
leading whitespace ignored) are imported once daily. Library pagination
finds up to 2,000 playlists; up to 40 matching playlists and 1,000 tracks per
playlist are processed within the existing read-only snapshot time budget.
Only playable track metadata is retained. These saved personal History Mixes
make their tracks eligible for the familiar pool, while favourites still rank
higher. Overlapping month/year playlists use the user-approved rank weights as a
separate capped familiarity signal. These are inferred reference weights, not
actual historical timestamps or provider-reported counts. They do not train mood,
time of day, room, or weather; dislikes and duration rules continue to apply.

Mood Mix and Play Radio from here replenish below five upcoming tracks. Excluded
tracks are filtered before ranking, avoiding starvation after the initial top
50. Once fresh eligible tracks are exhausted, older exact recordings may be
reused, preferring the least recently used, while alternate versions stay
excluded. The current item and existing upcoming items are always preserved.
A single eligible song cannot provide a distinct next song; temporary MA/library
failures are retried while the mix remains active. Stops or external playback
end the session. Sessions currently live in service memory, so restarting the
library service ends their monitoring.

Queue snapshots for mixes fetch around the current index once it reaches 400,
so playback beyond the first 500 historical queue entries does not lose the
upcoming tail. Normal Home Media Play music now starts the same server-owned
radio session instead of a finite recommended batch; that frontend change
requires an app update. BS5c's contextual music action also registers the active
MA queue with this monitor. Plain manually queued playlists remain finite.


## One-time weighted MA count import

At the user's request, `tools/import_reference_plays.py` adds rank-weighted
estimates to MA **track** play counts. A playlist of N tracks is interpolated
from N+1 plays at the first position to 1 at the last, rounded half-up; a
single-track playlist gets 1. A five-track playlist gives 6, 5, 4, 2, 1.
Every occurrence contributes, including overlaps between playlists. Existing
counts are added to, not overwritten. These are synthetic baseline estimates.

A `homemedia_reference_play_imports` table in the same MA database records each
stable provider playlist identity once. Counts and ledger are committed in one
transaction; repeated runs and edits to an already-imported playlist do not
apply it again. No last_played timestamps, playlog entries, artist counts, or
provider play reports are created. Apply must run as root on the Linux MA host with MA and its clients stopped.
The tool refuses writes when another process holds the database or its sidecars.
It requires a backup and full database/FTS consistency checks, and aborts before
writes if any track is unresolved. Never access the live exclusive-mode database
from an external writer. Prepare large repairs in RAM, then flush the staged
file and directory renames before restarting MA to limit SD-card writes. It is schema-dependent and
must be checked before use with a different MA version. Retain the ledger and
backups during MA migration; dropping the ledger removes the once-only guard.

The normal reference checker remains read-only and runs once every 24 hours.
Its last completed check is persisted so service restarts do not cause an extra
scan. It can discover new playlists for the shared baseline; MA count imports
are a separate explicit one-time operation and are not repeated by that scan.
Rank weights also feed the capped familiarity prior without training listening
context or promoting items to favourites.

## Durable sessions and explicit skip feedback

Pattern Play and Mood Mix save the original root track, mood, queue ownership and recent recording identities in the BS5c library database. Writes happen on session edits and song transitions, not on each polling tick. After restart the session continues only while the actual queue still belongs to it; manual playback cancels it without changing the queue.

Every generated track must have a known finite duration between one-third and three times the original root duration, inclusive. Steering the mood or advancing songs never changes that root. Normal songs and long mixes (20 minutes or longer) remain separate. Unknown root durations prevent automatic mix startup rather than guessing.

Home Media captures Music Assistant's actual current queue item before an explicit Next command and records a durable UUID-tagged report only after the command succeeds. The phone outbox retries locally or through the authenticated HA bridge; server receipts prevent duplicate learning. Pauses, stops, seeks, transfers and natural song endings are not explicit skips. Android Auto and notification Next controls use the same reporting path. Remote feedback requires Home Media Bridge 0.1.1.

One recent explicit skip excludes the recording for six hours; two within the past week exclude it for 48 hours; three or more exclude it for seven days. A decaying ranking penalty remains after the cooldown. Artist/title aliases carry this across providers, including uploads titled `Artist - Song` and remastered versions. The original skip timestamp is preserved through retries. These changes do not rewrite Music Assistant's library database or existing queue history.
