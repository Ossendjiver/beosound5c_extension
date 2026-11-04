# Play Music rotation and artist avoidance

Play Music retains contextual taste learning, trusted playlists and favourites. Its previous cumulative positive artist reward could grow without limit, while repeated recordings lost only 1.5 points for two hours. The hourly tie-break also made consecutive requests almost identical.

Positive historical reinforcement now uses a bounded logarithmic bonus. Plays of at least 30 seconds create recording-aware fatigue across provider versions, with stronger recent-artist penalties. Play Music starts prefer recordings outside the last 72 hours, then relax the oldest cooldown tiers first when the safe pool is exhausted. Skips retain their separate stronger cooldowns. No taste, mood, duration, recording-identity or radio eligibility rule is relaxed.

The first five proposed tracks are reserved for up to twelve hours in existing SSD-backed model state, bounded to 500 references. These are rotation hints, never synthetic play counts or positive taste evidence. Consecutive requests get separate bounded tie-breaking variation. Mood starters remain cached; automatic requests do not resample or recalibrate audio. Offline ranking does not consume proposal reservations.

Artist avoidance is a hard filter in the common ranker, including cached mood starters, song radio and provider recommendations. It matches normalized metadata artist names, collaborations and explicit artist/title upload prefixes. It does not fuzzy-match unrelated artists or remove files, and does not interrupt a currently playing song.

The existing `/library/event` endpoint accepts:

```json
{"type":"artist_feedback","artist":"Artist name","action":"avoid"}
```

Use `action: "allow"` to undo. Names are limited to 200 characters and stored in `avoided_artists` in existing model state. This uses fields already allowed through the HA bridge; no HA restart or new endpoint is needed. The app exposes Avoid artist / Allow artist again in artist and library-item menus and retries saved preferences through local or remote HA.

Diagnostics expose avoided-artist rejections, fatigue tiers/penalties and the capped positive-learning contribution. Simulation verifies rotation and eligibility, not the listener's subjective enjoyment. Sparse catalogues may still repeat after all eligible choices are exhausted; explicit artist blocks never relax.
