# Staged MA continuity repair — 25 October 2026

Status: review only. No deployment, service restart, queue mutation or APK build.

## Failure and scope

Link stopped on Janine's “Don't Love Me” at 11:04 Melbourne time with ten upcoming items still present. MA logged HTTP 404 from its own `/tidal-dash/...` manifest proxy for that track beforehand. Speakers stayed on LINK and no sleep timer was active. The BS5c refill service was running; refilling a queue cannot repair a failed transport handoff.

The installed Tidal implementation gives stream details the model's default 600-second cache lifetime, but deletes an unused proxy after track duration + 300 seconds. For the 211-second track, that is 511 seconds: cached details can still advertise an already-deleted endpoint. This mismatch is reproduced offline. The logs establish the missing endpoint; they do not prove every internal timing step leading to the final idle report.

This package patches the inspected MA implementation, rather than teaching BS5c to start arbitrary idle rooms. It is stored here alongside the deployment tooling because this installation has an existing customized MA container. It is not a change to upstream MA, Home Media Android or the HA bridge.

## Changes

- DASH details expire after 120 seconds; their local route remains available for at least that lifetime plus 300 seconds, or track duration plus 300 seconds, whichever is longer. Each GET renews the route. Cleanup checks a lease token so an old callback cannot delete a renewed route. Manager namespaces avoid collisions across reloads. Route count and manifest size are bounded without evicting active routes. Direct non-DASH streams keep their previous 600-second setting.
- A real Tidal stream error can arm a session/item-specific recovery ticket. A successful refresh of that stream cancels the old evidence. Merely finding a player idle never arms recovery.
- Recovery waits for the failed item to be current and reported idle at its known end. It never cuts off playing or buffering audio, replays the completed song, or replaces the remaining queue. It freshly resolves the unchanged immediate successor via MA's existing play-index path.
- MA stop, pause and shutdown cancel pending recovery. Changed sessions, sources, successors, inactive/unavailable/unpowered players, repeats, flow queues, any sleep timer or unreadable timer state all prevent recovery. Early/ambiguous stops also fail closed. Guards are rechecked under MA's group/player lock and after asynchronous buffer cleanup.
- At most three distinct failed items per queue in fifteen minutes may be recovered; each ticket waits at most fifteen minutes. Failed command acknowledgements are not replayed. Recovery failure is logged without stream URLs or credentials.

A native-device stop that supplies no stop/pause/source/power indication and happens exactly at a confirmed failed track's EOF cannot be perfectly distinguished from failed EOF using these APIs. That residual device-feedback ambiguity must be checked in the planned live validation. This deliberately does not add mid-track or unexplained-idle auto-resume.

## Prepare for a later deployment

1. Use Python 3.14 and the exact MA package source matching `preimages.json`. Run `python prepare.py --source <copied-music_assistant-package> --output <separate-empty-directory>`. It checks all source hashes before copying, dry-runs the patch, compiles the resulting code, preserves its input and refuses existing repair helpers. Changed upstream/custom files require a rebase and fresh tests, never a forced patch.
2. Run the tests below and inspect all five prepared files. No credentials, provider settings or databases need editing.
3. At a separately authorized deployment window, back up source and databases to SSD using the established stopped-writer/verified-backup procedure. Incorporate the prepared files into the existing persistent MA custom-image/override mechanism. Do not patch only a disposable container layer or put backup/build scratch on the SD card. Keep the last two verified backups.
4. Activating MA Python changes requires a planned MA restart/recreation; this package does not restart anything and is not suitable for hot deployment during playback.
5. Verify proxy lifetime through prefetch, pause, seek and transition; simulate a failed stream followed by idle; test explicit stop/pause, native-device stop, sleep timer, source change and queue transfer. Confirm no room is woken and no stale ticket survives a successful refresh. Observe several normal transitions and successful BS5c refills before considering deployment complete.

## Validation

```sh
python -m pytest repairs/music-assistant/continuity-20261025/tests -q
python repairs/music-assistant/continuity-20261025/tests/validate_patched_source.py --prepared <prepared-package>
```

35 offline tests pass. Tests cover DASH expiry/renewal/callback races and capacity, prefetch failures, confirmed EOF recovery, explicit and ambiguous stops, timer/power/source/session/queue races, successful refresh, bounded consecutive failures, lost acknowledgements and the source-preserving preparer. The actual patched Tidal method and MA stop/pause/shutdown hooks were additionally validated against the inspected installed source; all prepared files compile on Python 3.14. No live playback tests were performed.
