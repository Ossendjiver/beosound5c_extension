# iPod Video automatic sync

Install Rockbox for `ipodvideo` and enable **Playback Log**. Dock over USB mass storage. The worker only accepts Apple USB iPod hardware, a FAT music partition, and `.rockbox/rockbox-info.txt` identifying `ipodvideo`; it never formats drives or modifies the iTunes database.

Flow: import Rockbox playback/rotated logs with exact manifest recording identities → durably acknowledge completed listens and skips in the BS5c learning model → select favourites, configured playlists, recent listens and predicted song radios → fill remaining capacity with library tracks → sync managed music/playlists → flush, unmount and power off only the iPod. Streaming subscriptions are not offline downloads; unavailable local recordings are counted on the service page. Live room queues are never used for exporting audio.

Selection uses the existing pattern model at morning, afternoon and evening contexts, then the existing seed-based song-radio selector. Completion is modest familiarity evidence, not an automatic favourite. Skip outcomes enter the existing recent/repeated skip and transition models. Permanent receipt IDs make reconnect/retry safe; logs remain on the device.

Configuration: `/etc/beosound5c/ipod-sync.json`. Set `enabled`, `playlists` (explicit playlist names; empty uses the library's trusted playlist names), `local_roots`, `max_tracks` (up to 10000), and `reserve_bytes` (default 1 GiB). First verified iPod is paired by serial/UUID. Change `device_id` only when replacing the device. Other existing files are untouched; only files in the previous Home Media manifest under HomeMedia/Music are removed during refresh. A failed import/copy remains mounted and reports Error so it can be retried.

System → Services → IPOD SYNC shows progress, imported listening events, unmatched entries, streaming-only skips and the last completed sync. Enabling the timer does not wake a display or a speaker. Rockbox will scan copied files into its own database; generated M3U8 playlists can be played immediately through Files.

New installations have no managed manifest yet: unmanaged historical log entries are retained and shown as unmatched instead of guessed. Playback of the first synced selection subsequently imports automatically.
