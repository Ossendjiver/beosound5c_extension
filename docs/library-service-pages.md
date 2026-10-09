# Library and Provider status

System → Services includes Library and Provider cards. Both use the read-only
`GET /library/status` endpoint on port 8788. Library reports session, listen and
relationship counts, plus music-prompt suppression. Provider reports the existing
SSD worker checkpoint: sweep coverage, completion, ready profiles, pending samples
and errors when available. No credentials, audio URLs or private configuration are
returned. Missing profile status is displayed as not profiled yet. GO retains the
usual service restart action: Library restarts `beo-library`; Provider starts the
ordinary `beo-provider-profile` job, which defers if the full sweep holds its lock.

Proactive music prompts are suppressed while any observed HA media player or the
router is playing/buffering, or for 30 minutes after entering paused state. HA
`last_changed` timestamps retain the pause age across service restarts. Router
states without a timestamp use the first observed pause time; repeated polls do
not extend the window. Stopping playback releases the block. Existing music
overlays are cleared without recording a user dismissal; news/yoga are unchanged.
