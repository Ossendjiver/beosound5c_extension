# Music menu missing after router restart

Live read-only inspection on 11 October 2026 found:

- All persistent BS5c services active, with no failed systemd units.
- Music Assistant source authenticated and responding on port 8783; 10,685
  songs in its cached library. The recommendation service responds on port 8788.
- The 11.7 MB music cache is valid JSON and its web URL responds successfully.
- The router's source registry contains News and Kodi, but no Music Assistant.
- `showing.exclusive_playing_fallback` is enabled. The router was restarted
  after the Music Assistant source last started.
- No kernel storage errors, and ample free space on the SSD. No service, queue,
  playback or configuration changes were made during inspection.

The `MassSource.handle_resync` guard correctly avoids adopting another room's
playback while BS5c is idle, but returned before registering source availability.
Following a router restart, the source therefore remains absent from the menu
even though its process and cache are healthy. Its metadata monitor also avoids
publishing while that source is unselected, so it does not repair the registration.

The repair registers an idle source as available before returning from this
guard, without querying other queues or publishing their metadata. An unreadable
router status must not demote an already playing/paused direct source. Existing
direct-source resync remains supported. Local and remote metadata resync no longer
requests speaker power-on. Router registration now retries non-success HTTP
responses instead of silently treating them as successful registrations.

Regression tests cover idle and unknown selection, the metadata ownership guard,
playing/paused state preservation, local/remote resync power flags, and HTTP error
retries. The complete Python unit suite passes (1005 passed, 2 skipped).
Existing mocked background-task tests produce eight warnings.

Separate observations: router logs contain music-video stream lookup failures
when Invidious/yt-dlp cannot resolve streams. Provider profiling reports 5,106
successful samples, 159 pending profiles and 139 sweep errors. These do not block
music-cache browsing; this patch does not claim to repair those upstream failures.

## Staged deployment plan

This repair is staged on a GitHub branch, not installed on the live device.
Back up the affected source and private configuration on SSD before installation.
Load the updated source by restarting only `beo-source-mass.service`; do not restart
Music Assistant, the player, router, input or profiling services. Verify source
registration, the music menu/cache, and unchanged existing MA queue/playback state.
