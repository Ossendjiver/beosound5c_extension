# Optional YouTube search on BS5c

The existing wheel keyboard and voice search query all configured Music Assistant providers. They do not otherwise query YouTube. This change adds independent **YT music ON/OFF** and **YT videos ON/OFF** keys to that same search panel. Both default off. Browser choices survive refresh; configuration supplies the initial defaults.

- **YouTube music** searches the public YouTube Music song catalogue through NewPipeExtractor. Songs play as audio through the currently selected MASS target, using a stable bridge URL. This does not require a YouTube Music account or automatically add songs to the library.
- **YouTube videos** searches normal public YouTube videos. GO, or Play on Samsung Frame in the context menu, explicitly launches the configured Tube app and sends the video ID to its linked YouTube on TV HA entity. It bypasses the MASS audio queue and BS3 activation. No generic youtube.com URL is handed to Samsung.
- Both keyboard and voice searches use these same switches. Existing MA results remain available if an optional YouTube search fails, with a warning.

Requires the NewPipe MA bridge's search-enabled version. Its `/search?q=...&kind=music|video` API returns a bounded list of metadata, never temporary CDN URLs. Audio uses `/audio/VIDEO_ID`; metadata uses `/metadata/VIDEO_ID`.

## Configuration

In BS5c Config → Music Assistant, set the search defaults, bridge URL and Frame entities. For this installation:

```json
{
  "mass": {
    "youtube_search": {
      "music_enabled": false,
      "videos_enabled": false,
      "bridge_url": "http://192.168.4.103:8089",
      "frame_entity": "media_player.the_frame",
      "playback_entity": "media_player.youtube_on_frame_65",
      "app_id": "tUb3Xq7Lm9.Tube"
    }
  }
}
```

The HA token stays in the existing service environment, never in browser code. Physical and playback entities must be separate. The route wakes the TV if off/in art mode, launches `media_player.play_media` with type `app`, waits for launch, then sends type `video` plus `enqueue=play` to the linked YouTube entity. There is no TV power-off call in this feature. Existing Frame volume routing is preserved.

## Verification and deployment status

- Search-enabled bridge: Java build and five HTTP integration tests passed locally. Actual public searches returned 20 songs for Blue Monday and 20 videos for Blender; no playback was started.
- BS5c: 75 focused Python tests passed, including HTTP flag propagation, voice flags, independent defaults, failure isolation, metadata, existing MASS behavior and Frame routing. The Frame test checks the same HA payloads as Home Media and confirms no MASS queue or BS3 source activation.
- 106 JavaScript tests passed, including persistence and independent switches. Inline scripts passed Node syntax checks. Cross-source/service import checks also passed.
- The v0.9.2 baseline has a pre-existing lint-ratchet failure involving `hlk.py`, `input.py` and `sources/news.py`; these files were not changed for this feature.

Prepared against the newer `codex/sync-upstream-v092` branch, preserving the Frame volume changes merged to main and the live UI's additional playback-target/browse-state handling. Source and deploy mirrors accompany this change.

The original audio bridge is installed and MA-verified. The search-enabled bridge update and BS5c changes are awaiting temporary SSH access. Deployment should compare and patch the runtime files, back them up, preserve existing device config and restart only the affected service when it is inactive. TV playback has not been started to verify this new BS5c route.

## Deployment completed — 6 October 2026

Installed bridge 0.1.1 and BS5c search while BS5c was idle. Both defaults remain off. Configured physical `media_player.the_frame`, playback `media_player.youtube_on_frame_65`, and explicit app `tUb3Xq7Lm9.Tube`. Preserved the running device's additional queue-routing and metadata-monitor fixes using a three-way merge. Backups: `/home/thomas/bs5c-youtube-backup-20261006`. MA and OpenHAB were not restarted. Live text search returned 51 results in seven groups, with both YouTube modes enabled. No TV or speaker playback was started.
