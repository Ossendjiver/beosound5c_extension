# Mood wheel control and presentation refinement — 8 October 2026

The full-screen wheel uses the same computed body background as the main interface. The disc is centered independently of its controls, with a responsive diameter up to 560px (previously 400px, further scaled down on shorter displays). Static headings, compass labels, angle readouts and instructions are removed. Back and Play are icons with accessible names.

The navigation wheel still chooses angle. LEFT/RIGHT choose discrete Familiar, Blend and Discover layers. On the right half, RIGHT moves outward and LEFT inward; on the left half these directions reverse. Selection clamps at the innermost/outermost layer without changing mood angle. Laser events are ignored, and parent button routing gives the open wheel priority over generic context/player handling. GO plays; hold GO returns to the menu.

After one second with no selection movement, the mood description and discovery level slowly pulse over a four-second cycle. Moving immediately hides the labels and resets the delay. Closing cancels the pending dwell timer. Reduced-motion users receive a steady label after the same delay.

Validation: 102 JavaScript tests passed; real browser checks at 1024×768 and 800×600 confirm matching backgrounds, centered/enlarged geometry, hidden initial labels, dwell/pulse/reset behavior, GO and hold-GO. Playback callbacks were stubbed; no playback commands were sent. Browser regression: tests/browser/test_mood_wheel.cjs (requires Playwright).

Deployment is prepared, pending renewed SSH access. The earlier temporary key was removed after verification. Deployment patches existing live mass.html and hardware-input.js in place to preserve unrelated device-local edits, backs up all four UI files, and changes no HA/MA or music-backend services. Refresh the BS5c browser after deployment to load the revisioned mood assets.
