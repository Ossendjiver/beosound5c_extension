# On-demand radio seed metadata

A manually chosen provider track may be absent from playlist/catalogue scans. Previously its session kept only title, artists and duration; radio rejected every candidate when the seed had neither genre nor comparable audio. The normal background metadata loop only visits cached library entries, so this state could persist indefinitely.

Before radio eligibility filtering, merge cached descriptors and fetch missing seed metadata through MA music/item_by_uri with allow_update_metadata=false. The request uses an isolated bounded HTTP connection, a four-second network timeout and five-second outer deadline. Exact URI or exact provider-instance/item mapping is required before accepting canonical metadata. Persist successful descriptors in the existing track_metadata cache and use a 15-minute retry backoff for unavailable/insufficient data. No track sampling is performed on this path.

Existing genre/audio compatibility, duration, skip penalties, version deduplication and artist spacing remain unchanged. Missing evidence does not cause unrelated popularity fallback. Initial empty radio fills and later continuation shortages now persist a visible waiting_for_candidates status and last_error while retaining retry scheduling.

Deployed with the pending shared queue menu and remote-skip feedback repairs on 2 November 2026. Encrypted source/private-config backup and a SQLite backup-API snapshot were verified on SSD before installation. Only the library and MA source adapters were restarted; MA/player/router and the TV diagnostic units were preserved. HA REST commands/scripts were reloaded and UI resources refreshed with display power preserved.

Validation: 10 seed-specific tests cover the observed genre gap, real recommender eligibility, canonical identity checks, cache/backoff, cancellation and visible initial retry state. Combined deployment passed 1300+ Python checks, 129 JavaScript checks, two offline Chromium queue walkthroughs and HA configuration validation. Live provider lookup verified available metadata without queue mutations or synthetic taste feedback.
