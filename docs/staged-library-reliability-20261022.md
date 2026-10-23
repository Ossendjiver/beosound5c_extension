# Staged queue, sampling and downloader reliability repairs

These changes are prepared for review; no live deployment, scan start, playback or queue edits are part of this revision.

## Queue refresh

MA's committed boundary includes the current item and every track through `index_in_buffer`. Radio and mood replacement exclude those recordings, preserve their IDs, and re-read that boundary after ranking. A boundary change causes a deferred refresh rather than a write against an old snapshot. The queue writer validates the expected boundary, filters exact duplicates against retained items, and checks again after disabling competing MA autoplay. An intentionally empty new tail deletes only uncommitted items. Snapshots that do not cover the buffered boundary fail closed.

## Sampling recovery

The existing half-hour provider-resume timer runs bounded local and provider passes in alternating order. This avoids starving either backlog during short idle windows. Both use the shared SSD profiler lock. Local sampling checks MA idleness before each recording and stops requesting permission again once a pass detects playback. Already-current profiles are skipped; failures retain earlier good descriptors. Provider retries use exponential backoff (15 minutes to six hours), retain HTTP retry delays, and use at least a day for inaccessible/not-found resources. Retryable records remain eligible even when an older useful profile survives. New provider inventory entries are also eligible. Pending totals include failures consistently in both the sidecar and status output.

There is no finite retry-count cutoff. Permanently unavailable provider audio can remain pending; it is reported rather than declared sampled. Local inaccessible files retain their retry records. Metadata sweep completion is separate from sampling completion.

## Downloads and library destinations

Each pass reconciles the saved selection against actual files before trusting a completed timestamp. Missing, size-mismatched or outdated-quality entries re-enter work, including legacy selections whose pending list was empty. Errors persist a sanitized reason and retry time. Protected/unavailable audio stays unresolved, while provider-confirmed non-explicit tracks are explicitly counted as policy exclusions. `complete` requires verified files with no unresolved work; selections containing policy exclusions use `complete_with_exclusions`. No media protection is bypassed.

New downloads go directly to the main music root using existing artist/album folders where matched, with primary album artist/composer tags preserved. Hidden download/tagging partials are written in that final folder. After FLAC probe/decode verification, the completed file is atomically published without replacing any existing file. A durable commit journal allows a finished file to be adopted after an interruption between publication and index update. Previous quality versions and unmanaged files are retained. Legacy files in HomeMedia-Tidal remain eligible for the existing organiser; new files do not require it.

## Deployment requirements (not yet performed)

Install changed source and existing deployment mirrors, the new `tools/resume_library_profiles.py`, and the updated `beo-provider-resume.service` and `beo-tidal-download.service`. The latter permits writes to the main music root. Use an SSD backup and validate private overlays before restarting library/source services and reloading systemd at a safe point. The existing timers suffice; no new daemon, HA changes, app changes or APK are needed. Do not restart an active download or profiler to install these units. The first subsequent downloader run reconciles the selection automatically, and the next recovery timer resumes eligible sampling.
