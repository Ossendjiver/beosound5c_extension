# Music browse acceleration and voice search

## Interaction design

The three large sorted library views (`Artists`, `Albums`, and `Titles`) retain
normal item-by-item wheel movement at ordinary speeds. A deliberate fast spin
switches to letter groups:

- speed below 24: normal list movement;
- speed 24–55: advance one letter group;
- speed 56–87: advance two letter groups;
- speed 88 and above: advance three letter groups.

Letter jumps apply only at the top level of a sorted section with at least 40
items. Nested albums, playlists, search results, and short lists remain precise.
Articles (`A`, `An`, and `The`) are ignored for the displayed letter, and titles
beginning with a number share the `#` group.

`Voice Search` is a MUSIC section rather than an always-listening wake word. Open
it and press GO or LEFT to start one capture. The screen shows `Listening…`, then
opens results grouped as Tracks, Albums, Artists, Playlists, Radio, Podcasts, and
Audiobooks. Selecting a playable result uses the existing MASS queue commands.

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

Service health reports voice readiness at `GET /status`. `GET /search?q=...`
provides a typed diagnostic for the same all-provider MASS search; the UI uses
`POST /voice_search` for capture plus search.

## Library architecture decision

Keep the hybrid model. Running Music Assistant on the same machine makes live
queries cheap, but it does not make a 10,000-title top-level response small or
guarantee that it is available during a MASS restart. The local snapshot still
provides deterministic alphabetical ordering, immediate cold-start display,
letter boundaries, and graceful degraded browsing.

New discovery paths should be live: voice search is live now, and text search
should use the same endpoint. The next architectural simplification should be a
shallower stale-while-revalidate browse cache, not removal of caching:

- cache root summaries and precomputed letter boundaries;
- fetch artist/album/playlist children lazily from MASS when opened;
- retain recently opened children for a short time;
- invalidate from MASS library events and refresh in the background;
- keep the last good snapshot for startup and outages.

This removes duplicated expanded track metadata from several branches without
making the wheel UI dependent on a large live response for every navigation.
