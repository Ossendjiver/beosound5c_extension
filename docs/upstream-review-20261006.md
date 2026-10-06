# mkirsten upstream review — 6 October 2026

Reviewed https://github.com/mkirsten/beosound5c at `bd40caaed9cf7e26cedff14f86ba8e1bc9b0bc65` (v0.11.0) against this project's v0.9.2 branch. A wholesale merge changes 315 files, removes custom MASS/Kodi view code and rewrites menu behavior, so selected fixes were ported while retaining local routing.

## Folded into this branch

- External webpage iframes are unloaded on navigation away, preventing hidden camera streams from accumulating resources. Internal preloaded source views remain preserved. Ported from bd40caa with seven upstream lifecycle tests.
- Combined repeated Chromium enable/disable feature flags so all requested features take effect. Retained this branch's remote debugging and shared-memory options. Four upstream flag tests and shell syntax checks pass.

These upstream UI changes are committed for review, but not installed on the running kiosk; they take effect after a later UI deployment/restart. The YouTube search deployment is separate and complete.

## Worth considering next

- UI watchdog and higher file-descriptor limit: helps recover Chromium freezes. Requires reviewing its automatic restart behavior and deployed unit configuration.
- Camera overlay names/defaults: clearer configured camera titles, optional show-camera parameters and two-camera input. Requires coordinated input, overlay and HA automation changes.
- Beo6 source-port lookup from the shared registry: fixes a stale private port table, useful if Beo6 is in use.
- AirPlay 2 source: optional local-player feature requiring shairport-sync, new units and installer changes; not a patch to your MA routing.
- Jellyfin: optional new media source needing credentials and source configuration.
- System update restart coverage and security/config-save changes: review against your deployed service list and custom local patches before adoption.

Validation: all 113 JavaScript tests passed, including iframe lifecycle; four Chromium flag tests passed. Source/deploy mirrors updated. No upstream player behavior or optional source was activated live.
