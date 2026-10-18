# Network music library

Optional ReadyMedia/DLNA service advertises **BS5c Music** in WiiM Home Music Share and other UPnP clients. It serves stored audio files from `/media/local/music` without transcoding or modifying the files. Virtual provider catalogues in Music Assistant are not exported; completed downloaded audio in the music tree is included.

Install on the BS5c with `sudo python3 tools/install_music_share.py --install-package`. This adds Debian minidlna and its needed dependency without upgrading existing packages or restarting players. The optional service is separate from the Music Assistant queue and library services.

The index and artwork cache live in a 1 GiB ext4 image on the SSD, `/media/local/cache/music-share-cache.ext4`, mounted at `/run/beo-music-share-cache`. This supports ReadyMedia’s artwork hard links and SQLite filesystem semantics even when the music SSD is formatted as FAT. The mount point is under `/run`, but the data resides persistently on the SSD. Startup requires the SSD mount and music folder. File monitoring discovers additions; startup uses a non-destructive rescan. CPU is capped at 20%, memory at 384 MB, with low scheduling/I/O priority. Music is read-only within the service sandbox; symlinks outside the music root are excluded. SSD loss does not fall back to SD-card indexing. The generated systemd network policy permits only the local IPv4 subnet, localhost and SSDP multicast.

Refresh the WiiM media-server list and select **BS5c Music**. Artist, album, folder and track navigation come from the audio files' tags. No APK is required. Existing SMB music access remains available independently.

Configuration snapshots are on the SSD in `music-share-config-backups`; only its newest two snapshots are retained after successful configuration. This installer does not prune unrelated backups.
