# Unified YouTube chronology, Samsung capture, and local phone verification

Status: **staged feature branch; not yet installed or live-validated**.
The existing Bedroom SmartTube ↔ BS5c sync and Home Media → Bedroom Kodi routing
already passed manual device tests. This update extends them without changing
the main BS5c `services/library.py`, music database, hardware routing, or Kodi monitor.

## Changes and boundaries

1. **Samsung Frame in Lounge:** `services/lib/youtube_samsung.py` polls
   Home Assistant `media_player.youtube_on_frame_65` every 30 seconds.
   A watch is accepted only when the entity is `playing` or `paused`,
   provides an actual 11-character YouTube video ID (or canonical YouTube URL),
   a measured `media_position >= 1 second`, and state last_updated within
   five minutes. No guessed elapsed time, TV power change, app-launch
   acknowledgement, or generic SamsungTV Smart status is recorded. The
   server-side observer operates even when the phone app is closed.
   If the entity cannot expose that data, the watch remains unobserved.

2. **Takeout timestamps and repeated views:** Android JSON and HTML importers
   retain `watchedAtMs`, including multiple views of the same video at different
   timestamps. Legacy records without dates remain dated `0` and sort *after*
   dated records. Reimport the original Takeout ZIP/JSON/HTML to recover lost
   dates; they cannot be reconstructed from the previous phone library.

3. **Chronological central history:** `youtube_history` still stores the
   newest resume position *per video*, so older Kodi monitor clients work
   unchanged. New `youtube_watch_events` holds dated viewing sessions from
   SmartTube, phone, Samsung, and historical Takeout. Periodic progress
   updates from one device with advancing position within six hours roll into
   the current session; a later replay or ended prior session creates a new
   event. Takeout timestamps remain individually distinct and idempotent.
   The new `GET /library/youtube/history?events=1&limit=500` returns events
   with the same authentication header as before. The legacy GET without
   `events=1` returns the same latest-per-video format.

4. **Recommendations:** Phone Recommendations seeds are drawn from up to
   eight distinct videos, prioritising the last seven days, then the rest of
   the last 30 days, followed by older favourites and undated legacy history.
   Duplicated views are preserved but do not consume all seed slots.

5. **Local phone playback:** `PhonePlaybackService` persists its YouTube
   playback event into `YouTubeSyncOutbox` after at least one second of
   *ready* video playback, updates approximately every 30 seconds of progress,
   and records completion. Outbox flush makes authenticated POST
   `/library/youtube/history`; failure retains the record for retry.
   Local video files and the launch-only buffering state are not reported.

## Compatibility and safe BS5c deployment

**Do not replace the live `services/library.py`** with the staging copy.
Your live BS5c library has extra radio, watchdog and listener features that
were not in the old packaging baseline. The already-installed
`youtube_history.install(app)` automatically imports the new Samsung module.

From the successful `BS5c-YouTube-history-v2-staging` Action artifact,
extract `bs5c-youtube-history-v2-modules.zip` to a temporary directory and
copy only:

- `services/lib/youtube_history.py`
- `services/lib/youtube_samsung.py`

`youtube_collections.py` is included for reference and **does not need to
replace the working live copy**. It is unchanged. Back up the live
`youtube_history.py` and SQLite database first. The SQLite schema is additive,
but older code will not understand the new dated-events and import routes.

Example commands **on BS5c**, after staging files at
`~/bs5c-youtube-history-v2/services/lib/`:

```sh
STAMP=$(date +%Y%m%d-%H%M%S)
mkdir -p "$HOME/bs5c-youtube-v2-backup-$STAMP"
cp -a ~/beosound5c/services/lib/youtube_history.py "$HOME/bs5c-youtube-v2-backup-$STAMP/"
cp -a /media/local/cache/youtube_history.sqlite3* "$HOME/bs5c-youtube-v2-backup-$STAMP/" 2>/dev/null || true
cp -a ~/bs5c-youtube-history-v2/services/lib/youtube_history.py ~/beosound5c/services/lib/
cp -a ~/bs5c-youtube-history-v2/services/lib/youtube_samsung.py ~/beosound5c/services/lib/
python3 -m py_compile ~/beosound5c/services/lib/youtube_history.py ~/beosound5c/services/lib/youtube_samsung.py
sudo systemctl restart beo-library.service
systemctl is-active beo-library.service
journalctl -u beo-library.service -n 50 --no-pager
```

For a consistent SQLite backup, prefer `sqlite3 /media/local/cache/youtube_history.sqlite3
".backup '/path/to/youtube_history-backup.sqlite3'"` while service runs,
rather than merely copying its WAL files. The example copy is a last-resort
file-level snapshot and should be made with the service stopped if used as
a complete database backup.

The Samsung observer needs a real HA long-lived token in the library service's
EnvironmentFile (`/etc/beosound5c/secrets.env` by default), in addition to
`BS5C_YOUTUBE_HISTORY_KEY`. Reuse existing `HA_URL`/`HA_TOKEN` if available,
or configure:

```ini
BS5C_YOUTUBE_HA_URL=http://homeassistant.local:8123
BS5C_YOUTUBE_HA_TOKEN=<PRIVATE_HOME_ASSISTANT_LONG_LIVED_TOKEN>
BS5C_YOUTUBE_SAMSUNG_ENTITY=media_player.youtube_on_frame_65
```

Restrict that file to 0600 and never post credentials into chat, GitHub, or
Kodi diagnostics. The observer only permits a private IP or `.local` HA host
and follows no redirects.

## Validation

- **Browser/SmartTube regression:** Bedroom watch history and Watch Later
  still synchronise. Unmodified Kodi monitor (v0.1.2) uses the old history
  GET/POST contract, which remains supported.
- **Phone**: Install the *new* Home Media signed APK, keep BS5c sync enabled,
  play a YouTube video on **This phone** for ~65 seconds, then stop it.
  On BS5c, call authenticated
  `/library/youtube/history?events=1&limit=30` and confirm
  `device_id=phone`, the video ID, positive `position_ms`, and an updated
  `watched_at_ms`. Playing locally is not proven by an Android unit test
  alone; check this live once both packages are installed.
- **Samsung**: Open YouTube on the Samsung itself, not through Home Media,
  play for ~60 seconds, then confirm a `device_id=lounge` event through the
  same authenticated endpoint. Absence means the HA entity is not reporting
  verifiable identity or position (or credentials/entity configuration are missing).
- **Takeout**: Reimport original export after updating the APK, allow background
  backfill, then check multiple original dates for a video watched twice.
  New uploads use `POST /library/youtube/history/import` in ≤100-event
  idempotent batches, queued in Android SQLite for offline retry. Historic
  events do not overwrite newer resume positions.
- **Recommendations**: Refresh recommendations after the reimport and confirm
  that recent seeds dominate while older interests remain represented.

Neither server files nor Android updates are deployed by GitHub commits:
copy/installation and live tests must be performed on your own devices.
