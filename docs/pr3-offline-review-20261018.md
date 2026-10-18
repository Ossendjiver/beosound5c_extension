# Offline review of draft PR #3

Reviewed draft head `1cd5c0cc24c5e3235040e075f9ac9ff8b7001107` against main `82d4e7216f6674abc440b67605a8a4d89de8f19a`. This is a selective port; the old branch is not merged wholesale.

## Incorporated

- Unload external webpage iframes on navigation, including legacy camera pages already rescued into the hidden preload container. Internal preloaded source views remain alive. New external frames do not use the internal preload ID prefix.
- Combine repeated Chromium enable/disable feature switches, retaining every existing requested feature and each launcher's other options. The lifecycle/flag fixes originate in the earlier upstream review of `mkirsten/beosound5c` at `bd40caaed9cf7e26cedff14f86ba8e1bc9b0bc65`.
- Restore the missing YouTube search, channel/podcast, Frame volume and independent-search-switch tests. Search/channel backend code and Frame volume dispatch already match the draft or have newer changes, so they are not replaced.
- Restore the YouTube settings schema and missing deployment copies; retain current root/deploy MASS source and UI. Existing main CI already runs the complete Python and JavaScript suites; a second branch-specific workflow is unnecessary.
- Preserve current queue-overlay suppression and now-playing refresh. The old draft's versions would remove newer behavior.

Two invariants from an earlier routing prototype were also recovered in the current durable playback coordinator: reject Kodi behind renamed/grouped/protocol players before any mutation, and discover an unambiguous group owner from group_members when MA omits child pointers. Conflicting owners fail closed. No alternate routing engine, automatic source takeover, queue replay or automatic leader-transfer policy was imported.

## Validation

Baseline regressions reproduced the missing iframe/flag behavior and routing safeguards. After the selective port:

- Full Python unit suite: 1,221 passed, 14 skipped, 9 subtests passed; 8 warnings in existing router/task fixtures.
- Full JavaScript unit suite: 127 passed.
- Original routing prototype: 41 offline coordinator/HA framework tests passed.
- Both Chromium launcher files pass bash syntax checks.
- Repeated page entry/exit, legacy hidden-page cleanup, internal source preservation, current queue overlay behavior, Kodi source/target/group/protocol rejection, and ambiguous membership are covered.

No live device was accessed, no playback commands were issued, and no APK was built for this review. Deployment is a separate action.
