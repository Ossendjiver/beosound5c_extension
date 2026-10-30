# B&O remote skip feedback through Home Assistant

MLGW emits `mlgw.ML_telegram` / `BEO4_KEY` / `COMMAND`. The existing Beo4 router maps Step Up to Link next. In that branch pass `remote_feedback: true` to `script.beosystem_link_transport`. Keep all existing source/interlock guards. Do not attach a second listener that also sends next.

Merge the Link transport example into the existing script rather than defining it twice. Install the companion package after replacing the example BS5c address. Reload REST commands and scripts after Home Assistant configuration validation; a core restart is not required. The existing Link targets and resolver are retained.

The Link script snapshots outgoing title, artist, URI, duration and extrapolated position before the next command. Once that command returns successfully, it launches the reporting script asynchronously. The helper requires changed, non-empty metadata within 12 seconds and sends the existing `/library/event` listen_feedback request. Only explicit forward skips from playing music qualify. Previous, pause, natural endings, missing timing/artist information and failed commands do not report. The last three seconds are excluded to avoid confusing normal completion with a skip. A command that succeeds but produces no HA metadata transition is deliberately left unclassified.

Each report has a stable event ID for all three delivery attempts. BS5c stores receipt IDs persistently, applies the existing recording-aware skip penalties, and suppresses stale outgoing router observations for up to 20 seconds or until the next distinct track appears. No new database schema is needed. Failed delivery after three tries is logged by HA; this is bounded retry, not an offline outbox. There is no historical backfill of remote skips.

The hook covers the HA Beo4 router path. Other transport paths must explicitly opt in only where they represent a user remote next command. It does not infer dislike from arbitrary MA changes or transfers. The optional hook defaults off for other callers.

Validation: 46 focused Python tests passed, including receipt persistence, recording matching, observer suppression, skip cooldowns and relationship learning. HA template tests cover no transition, unknown metadata, pauses, near-end exclusions and the remote opt-in. Actual audible remote skip verification should be performed during normal use; synthetic negative feedback must never be posted to the live learning database for testing.
