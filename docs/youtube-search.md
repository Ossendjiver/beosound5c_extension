# Optional YouTube search

Text and voice search support independent **YT music** and **YT videos** switches, both off by default. Search-panel choices persist in the browser. MA-provider results remain available if an optional YouTube search fails.

Music searches return finite audio tracks through a stable NewPipe bridge URL. Video selections explicitly launch the configured Samsung TV app and send the video ID to the separate YouTube playback entity. They bypass MA music queue routing and do not activate the shared speaker system.

Configure `mass.youtube_search` with `music_enabled`, `videos_enabled`, optional `bridge_url`, `frame_entity` (physical TV), `playback_entity` (separate YouTube on TV player) and optional `app_id`. Blank bridge URL uses the configured MA host on port 8089. A search-enabled NewPipe bridge must already be installed. Do not use the same HA entity for physical TV and YouTube playback.

The MA bridge must supply bounded `/search?q=...&kind=music|video`, `/metadata/VIDEO_ID` and stable `/audio/VIDEO_ID` endpoints. Provider credentials remain in device-local configuration.

[Saving channels as podcasts](youtube-channel-podcasts.md) is an explicit library action, separate from playback. Offline regressions cover switch persistence, HTTP/voice flags, failure isolation, metadata, Frame dispatch and volume, and channel subscription deduplication.
