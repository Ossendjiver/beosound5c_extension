# Suggestion prompts and playback controls

The context-aware music, news and yoga suggestion prompts support the physical
navigation wheel and GO button. The first choice is highlighted on arrival;
clockwise moves to the next choice and counter-clockwise to the previous choice,
clamping at either end. Wheel movement never submits an action. GO submits the
highlighted choice once. RIGHT selects the offered dismiss action, or closes a
prompt without one. UP and DOWN also move between choices. Mouse selection is
still supported.

While a prompt is visible it consumes navigation and button input before playback
or source controllers can handle it. Repeated broadcasts of the same prompt keep
the highlighted choice. Prompts retain their existing twelve-minute expiry and
do not wake the display or change its backlight.

## Shared-display fallback

Devices can opt into a single configured external fallback with
`showing.exclusive_playing_fallback: true` alongside their existing
`showing.entity_id` configuration. Both values come from the device's runtime
configuration; this feature does not embed room names or household entity IDs.

With no directly selected source, PLAYING accepts only the configured SHOWING
relay. Unrelated players cannot claim the source through passive Music Assistant
queue discovery. Idle fallback metadata clears old track titles and artwork.
A source selected directly on this device can still play and display normally.

PLAYING transport follows that ownership: a directly selected source takes
priority immediately, including while the previous fallback's metadata remains
cached. With no direct source, the configured fallback receives transport even
before metadata arrives. SHOWING transport always follows its configured target.
Local remote-control actions routed through the backend use the same fallback
when exclusive mode is enabled. Explicit PLAY and PAUSE remain distinct commands;
a failed fallback request does not resume an unrelated player.

## Verification

Run `python -m pytest tests/unit/python -q` and
`node --test tests/unit/js/*.js` from the repository root. Coverage includes prompt
selection, expiry, duplicate refreshes, input capture, direct/fallback handover,
metadata filtering and backend transport routing. The JavaScript suite also runs
in the existing GitHub Actions tests workflow.
