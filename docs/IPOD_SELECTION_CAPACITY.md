# iPod selection priorities and connected-device capacity

Sync selection now uses these strict tiers:

1. Favourites and selected/trusted playlists.
2. Predicted morning, afternoon and evening song radios, interleaved across time periods.
3. Further predicted tracks, favouring recordings with no qualifying play and fewer than two skips; then up to 50 other predictions.
4. Listening within 180 days with at least 30 seconds played.
5. Remaining library tracks as filler.

The prediction model still excludes explicit dislikes and active recent-skip cooldowns from its predictions. A play is a completion, or a non-skip positive listening event lasting at least 30 seconds. Existing MA play counts also qualify when exposed by the catalogue. Recording-normalised evidence is accumulated incrementally in the recommender database, so cross-provider copies share evaluation evidence and trimming the rolling log does not reset recorded totals. History that has already been discarded cannot be reconstructed.

Ubuntu and the next Android build send the connected device's free space and verified existing managed recording sizes/timestamps to the BS5c export planner. It reserves at least 1 GiB, uses actual source sizes, charges unchanged retained recordings zero additional bytes, and selects in priority order. If a file does not fit, smaller later files can still fill the remaining budget. Existing managed and unmanaged files occupy space; these two clients do not delete music. Direct BS5c docking uses the same priority catalogue with its existing measured byte budget and managed-file rotation policy.

Older phone builds remain compatible and receive the new priority order, enforcing their existing per-file capacity checks locally. Device-budget requests validate sizes and bounds, and a cached plan is not reused for different device storage. Capacity is rechecked during copying to handle other writers or disconnects.

Validation: strict tier ordering; morning/afternoon/evening balance in a three-item selection; cross-provider two-skip evidence and idempotence after log cleanup; small/large/full device budgets; retained local tag edits; malformed storage requests; Ubuntu transfer tests and Android iPod engine tests. No playback commands are issued by selection or export.
