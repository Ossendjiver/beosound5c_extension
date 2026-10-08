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
