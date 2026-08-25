# Music browse acceleration and voice search

## Interaction design

The three large sorted library views (`Artists`, `Albums`, and `Titles`) retain
normal item-by-item wheel movement at ordinary speeds. A deliberate fast spin
switches to letter groups:

- speed 1–5: normal list movement;
- three consecutive samples above speed 5: enter letter mode;
- while the spin is sustained, advance one letter every 800 ms at speed 5,
  interpolating linearly to every 200 ms at speed 11;
- an input at speed 2 or below returns immediately to item movement.

These thresholds are calibrated against the physical B5c wheel: careful and
ordinary movement reports speeds 1–2, while the recorded deliberate-spin range
reached 11. The centred letter overlay remains visible while letter mode is
active.

Letter jumps apply only at the top level of a sorted section with at least 40
items. Nested albums, playlists, search results, and short lists remain precise.
Articles (`A`, `An`, and `The`) are ignored for the displayed letter, and titles
beginning with a number share the `#` group.

`Search` is a MUSIC section. Open it with GO or LEFT to reach a wheel-native
QWERTY panel. Rotate the wheel to highlight a key and press GO to enter it. The
first row provides Voice, Search, Delete, Space, and Clear actions; RIGHT returns
to the MUSIC sections. Voice starts one push-to-talk capture rather than an
always-listening wake word. Both input methods open the same live results,
grouped as Tracks, Albums, Artists, Playlists, Radio, Podcasts, and Audiobooks.
Selecting a playable result uses the existing MASS queue commands.

Context menus and the QWERTY panel use a fractional speed ramp set halfway
between the original one-selection-per-event behaviour and the slower precision
curve. At speed 1 this is approximately one selection per two events, ramping up
smoothly with speed. Discrete touch and keyboard controls continue to move
exactly one selection per press.

## Voice data flow

1. The B5c captures mono 16 kHz PCM from the configured ALSA device with
   `arecord`.
2. The currently playing MASS player is paused for the capture and resumed
   afterward. Already-paused players are left unchanged.
3. Audio is streamed to the configured Home Assistant Assist pipeline from the
   B5c backend. No browser microphone permission or cloud browser speech API is
   required.
4. The transcript is cleaned of prefixes such as `play` or `search for`.
5. The B5c calls Music Assistant `music/search` with `library_only=false`, so the
   search covers all configured music providers rather than only the indexed
   library.

The long-lived Home Assistant and Music Assistant tokens remain in the systemd
service environment and are never sent to the browser.

## Configuration

```json
{
  "mass": {
    "voice_search": {
      "enabled": true,
      "microphone_device": "default",
      "language": "en-AU",
      "pipeline_id": "",
      "timeout_s": 15
    }
  }
}
```

Use `arecord -l` to find the capture card. If `default` does not resolve to the
USB microphone, set a stable ALSA name such as
`plughw:CARD=ReSpeaker,DEV=0`. Leaving `pipeline_id` empty uses Home Assistant's
preferred Assist pipeline.

Service health reports voice readiness at `GET /status`. The keyboard uses
`GET /search?q=...`; Voice uses `POST /voice_search` for capture plus search.
Both use the same all-provider MASS result normalization.

## Library architecture decision

Keep the hybrid model. Running Music Assistant on the same machine makes live
queries cheap, but it does not make a 10,000-title top-level response small or
guarantee that it is available during a MASS restart. The local snapshot still
provides deterministic alphabetical ordering, immediate cold-start display,
letter boundaries, and graceful degraded browsing.

New discovery paths are live: both voice and wheel-composed text bypass the
snapshot. The next architectural simplification should be a
shallower stale-while-revalidate browse cache, not removal of caching:

- cache root summaries and precomputed letter boundaries;
- fetch artist/album/playlist children lazily from MASS when opened;
- retain recently opened children for a short time;
- invalidate from MASS library events and refresh in the background;
- keep the last good snapshot for startup and outages.

This removes duplicated expanded track metadata from several branches without
making the wheel UI dependent on a large live response for every navigation.
