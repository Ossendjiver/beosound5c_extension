# Unified YouTube library — BS5c, SmartTube and Home Media

**Status:** The first Bedroom Kodi ↔ BS5c history and Watch Later sync
has been manually verified on the actual devices (20 November 2026).
The dated-events and Samsung extension is separately staged, not yet deployed.
See [dated history and Samsung observer](../../docs/youtube-chronology-samsung.md).

BS5c library service on port 8788 is the central source of truth. The
independent Kodi add-on in `service.bs5c.youtube.history/` keeps SmartTube's
existing local JSON lists in sync without modifying SmartTube. It leaves
`cache/`, stream manifests, temporary files, `settings.xml` and other
playback configuration **local** to each Kodi device.

## Server storage and authentication

The BS5c library service exposes authenticated endpoints:

- `GET/POST /library/youtube/history` — watch history and progress, 20,000 videos
- `GET/POST /library/youtube/collections/follows` — subscriptions, 200 channels
- `GET/POST /library/youtube/collections/watch_later` — Watch Later, 500 videos
- `GET/POST /library/youtube/collections/playlists` — SmartTube saved YouTube
  playlist references, 200 bookmarks
- `GET/POST /library/youtube/collections/home_playlists` — Home Media local
  custom video playlists, 200 lists; deliberately **not** injected as
  YouTube playlist references into SmartTube

All modifications are authenticated with `X-HomeMedia-YouTube-Key` matching
`BS5C_YOUTUBE_HISTORY_KEY` on BS5c. The routes are **disabled** if this
environment variable is absent or shorter than 32 characters.
The database is `/media/local/cache/youtube_history.sqlite3` by default
(separate from music-learning history), override using
`BS5C_YOUTUBE_HISTORY_DB`. Back up this database regularly.

## Kodi installation (after review)

1. Install the BS5c server feature and configure a random 32+ character
   `BS5C_YOUTUBE_HISTORY_KEY` in the BS5c library systemd environment.
   Do not put it in Git or exposed configuration archives.
2. Package `service.bs5c.youtube.history/` as a Kodi add-on ZIP with the
   containing directory at the ZIP root.
3. Install the ZIP through Kodi **Add-ons → Install from zip file**.
4. In BS5c YouTube History Monitor settings, set Enable, BS5c LAN address
   (typically `192.168.4.103`), a distinct device ID (`bedroom`,
   `lounge`, `dining`) and the same dedicated key.
5. Test a video played from SmartTube's own listings, another device's
   Watch Later edit, and a local playlist update. The monitor scans every
   30 seconds.

The monitor first uploads local history records that were not acknowledged,
then pulls the latest 500 central records without overwriting the actively
playing video or re-uploading imported records. For follows, Watch Later and
saved playlist references, it uses versioned compare-and-swap. Local and
remote edits to the **same** item that conflict are preserved locally, with
the affected IDs written into `<monitor-profile>/<collection>.conflicts.json`
for review. Edits to different items can continue. Deleted entries have
server-side tombstones to prevent offline devices from resurrecting them.

## Android

Home Media's YouTube settings include an optional BS5c sync switch and
dedicated key (stored with the app's encrypted settings). The phone reports
playback progress to BS5c and retains undelivered updates locally for retry.
Its subscriptions and custom playlists synchronise while on the home LAN.
Home Media's Watch later playlist is mapped to the shared SmartTube Watch Later
collection; other custom playlists remain separate to preserve their format.

## Limits and safety

- This is **not** a Google sign-in / Google account history synchronisation.
- SmartTube writes history for its own lists; the independent Kodi monitor
  separately reports externally started `play/?video_id=` playback.
  The Samsung observer never treats an unverified TV app launch as a watch.
- Each Kodi device keeps its own local cache, settings, DASH manifests and
  download data. Do not point multiple Kodi instances at a shared add-on
  profile via SMB/NFS.
- The LAN protocol uses HTTP plus a shared key. Never expose port 8788
  publicly or over untrusted networks. Remote access needs an authenticated
  HTTPS proxy/VPN and a separately audited forwarding implementation.
- The Android bridge currently works on local Wi-Fi; canonical
  `/channel/UC...` subscription URLs sync directly. Unresolved
  `@handle` or `/user/` addresses remain local rather than being
  lost or assigned an incorrect channel ID.
- Google Takeout's historical timestamps can now be backfilled after the v2
  BS5c and Android updates, retaining multiple original views. Full conflict
  resolution UI and the live Samsung / phone v2 acceptance tests remain pending.
