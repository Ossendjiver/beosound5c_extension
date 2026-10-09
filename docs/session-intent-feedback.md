# Audio similarity and session feedback

Mood compatibility, original-root length limits (one third to three times),
recording deduplication and recent-skip cooldowns remain eligibility gates.
Artist spacing and evenly distributed discovery still apply. Audio signals and
play counts cannot admit an incompatible or unprofiled mood.

Ranking now combines the root's validated tempo/embedding, the last five accepted
listens in the same room within 90 minutes, and acoustic continuity with each
upcoming predecessor. Already-played recordings remain descriptor evidence even
after they are excluded from candidates. Queued-but-unheard suggestions are not
accepted listening evidence. A 64-candidate shortlist bounds expensive sequence
embedding comparisons; recent intent is scored across all eligible candidates.
Missing or incompatible embedding models contribute no acoustic bonus.

Manual listening reward increases modestly with the fraction heard; deliberate
replay adds a small bonus. Autoplay completion remains a weak signal. Explicit
skip/dislike penalties remain unchanged; short pauses, stops, transfers and
buffering do not imply dislikes.

## Queue-session thumbs

`GET /library/mix?queue_id=...&feedback=1` includes feedback for the actual current
MA queue item. `POST /library/mix` with `action=feedback`, `queue_id`, `session_id`,
`current_item_id` and integer `vote` (-1, 0, 1) records or clears its vote.
The backend snapshots the queue again and rejects stale song/session identifiers.
Votes replace previous votes for the same recording across provider versions;
they never edit MA favourites, play counts or permanent dislikes.

Feedback is persisted on the recommender's existing SSD SQLite KV store. Active
mix votes survive phone disconnection and backend restart; explicit new mixes
get new identities. Ordinary queue sessions reset on unrelated queue replacement
or after prolonged inactivity. Polling unchanged state does not write to disk.
Positive votes provide bounded acoustic intent; negative votes exclude that
recording throughout the session and mildly penalise similar audio. Recent votes
are averaged so a long session cannot saturate every candidate's score.

For active mixes, a changed vote marks the future queue for guarded replacement
through the existing mix monitor, keeping the playing song and its position.
If nothing qualifies, the old tail is cleared rather than retaining disallowed
tracks. Regular manually assembled queues record feedback without automatically
rebuilding their contents.

Home Media exposes highlighted reversible thumbs in the collapsed-by-default
queue controls drawer, with current-item/owner/revision checks. Local and remote
connections use the same payload. Remote access needs Home Media Bridge 0.1.2
through HACS; its closed command boundary explicitly validates this action.
Older backends leave the thumbs disabled.

This change is committed only; deployment remains subject to the scheduled
idle/sweep checks. No playback commands or live database changes were used to test
it, and no APK was built.
