# SmartTube → BS5c unified YouTube history (staged)

This folder contains a standalone Kodi service add-on, `service.bs5c.youtube.history`.
It reads **SmartTube for Kodi**'s `history.json` and sends changed playback
records to the opt-in BS5c library endpoint. No patch to SmartTube, LibreELEC
system image or Kodi media library is necessary.

## Status / preconditions

**Not deployed; do not install until the BS5c draft PR is reviewed and
its API deployed.** This is an initial one-way monitor, not yet bidirectional
resume sync or Google account history sync.

1. Deploy the BS5c history API from PR #27 after tests pass.
2. Set `BS5C_YOUTUBE_HISTORY_KEY` to a random 32+ character string in the
   BS5c library service's systemd environment and restart only that service.
   Keep the key out of Git, browser URLs and diagnostic logs.
3. Package the exact directory `service.bs5c.youtube.history/` as a Kodi
   add-on ZIP with that directory at the ZIP root.
4. Install the ZIP from Kodi's Add-ons → Install from zip file.
5. In **BS5c YouTube History Monitor** settings, configure:
   - Enable YouTube history upload (off by default)
   - BS5c private IPv4 address, normally `192.168.4.103`
   - Device ID: `bedroom`, `lounge`, or `dining` (unique per instance)
   - Dedicated key matching the BS5c environment variable
6. Play a video **from SmartTube's own listings**, wait for SmartTube's first
   30-second history save, then check the BS5c history read API using the
   same key. Kodi logs expose only errors by class, not credentials.

The monitor polls every 30 seconds and stores acknowledgements in its own
add-on profile. It sends only changed history records, oldest first.
Failures do not acknowledge delivery and are retried on the next poll;
a successful record is acknowledged immediately so subsequent failures do
not replay earlier records. BS5c rejects duplicate or older timestamps.

**Limits and caveats:** SmartTube keeps at most 500 local history records.
The first run forwards those records where available, but cannot recover
older erased entries. Videos launched externally via SmartTube's
`/play/?video_id=` route are not recorded by SmartTube's current history
service, so this monitor cannot see those sessions. Enhancing that coverage
requires a SmartTube-specific playback event adapter.

The key is a shared secret transmitted over local HTTP; do not expose port
8788 to the internet or untrusted Wi-Fi. Use a VPN or authenticated HTTPS
proxy for remote clients. Kodi's add-on settings are local files and should
not be included in public backups.
