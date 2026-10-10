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
Each long-press thumb menu offers Track match, Genre match, Mood match and
Tempo match. The thumb determines positive or negative polarity. Negative feedback
also offers Heard it too often. A reason refines only its intended dimension:
recording identity, specific style/broad genre evidence, validated energy/valence,
or reliable tempo (allowing half/double beat-tracker estimates). Missing data is
neutral. Negative votes exclude the voted recording for this session; matching
reason signals modestly steer other eligible tracks. Overplayed only excludes the
recording, without penalising its entire genre or mood. Unqualified thumb taps
retain bounded acoustic intent. Older stored reason keys remain accepted.
All refinements occur after hard radio, mood and length eligibility checks and
never update permanent favourites or counts. Recent votes are averaged so a long
session cannot saturate every candidate's score.

For active mixes, a changed vote marks the future queue for guarded replacement
through the existing mix monitor, keeping the playing song and its position.
If nothing qualifies, the old tail is cleared rather than retaining disallowed
tracks. Regular manually assembled queues record feedback without automatically
rebuilding their contents.

Home Media exposes highlighted reversible thumbs in the collapsed-by-default
queue controls drawer, with current-item/owner/revision checks. Long press opens
the reason submenu; selecting another reason updates rather than clears the vote.
The app resolves a fresh feedback snapshot when tapped. Local and remote
connections use the same payload. Remote reasons need Home Media Bridge 0.1.4
through HACS, with its existing closed command boundary. Stale item/session IDs,
unknown reasons and reasons attached to a cleared vote are rejected.

Validation uses offline protocol, ranking and signal-isolation tests; no test
issues playback commands or modifies the live queue.
