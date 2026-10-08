# BS5c live Mood Mix deployment — 9 October 2026

Installed the shared Mood Mix backend, continuous discovery wheel, current-song preservation, local Pattern Play refill, duration/version filters and shared wheel queue overlay from `a82123f16fd94761f7a9a42b1f43bea3f7686384`.

The installer verified each existing target against its GitHub baseline before modification. Backup: `/home/thomas/bs5c-live-mood-backup-20261009-064552`. A separate complete snapshot of the modified device source was retained as `/home/thomas/bs5c-source-before-mood-20261009.tar.gz` with private permissions.

Only `beo-source-mass.service` and `beo-library.service` were restarted. Both reported active. Read-only requests to `/library/mix` and `/library/mood` succeeded; the latter correctly reported insufficient learned mood history. Neither service logged errors during startup. No playback commands or Home Assistant changes were issued.

## Device source reconciliation

The device checkout's base commit `4d0a4361bf35b4eed8ac79d4567740fe098ded9d` is already an ancestor of the feature branch. All modified active Python/JavaScript/HTML/CSS files matched existing main-branch source before installation. Their updated feature versions therefore preserve those changes.

Remaining additions incorporated:
- `tools/beacon-map.py`, the untracked beacon display utility.
- Removal of the unused legacy `library` default block, matching the device's local configuration. Runtime fallback behavior is retained.

The device's private Guardian API key remains local. Old `.pre_*`/`.bak` files, backup directories, generated artwork and Kodi caches, and `device_id` are preserved on the device rather than published as active source. This distinction avoids committing credentials or generated state while preserving all active local code.

Home Media v0.7.43 build64 is the companion APK. Offline validation of the updated backend and frontends is documented in `live-mood-mix-20261008.md`.
