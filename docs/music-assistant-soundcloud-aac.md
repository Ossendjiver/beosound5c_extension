# SoundCloud AAC stream compatibility (MA 2.10.5)

The 16 October 2026 app log reports `The requested media item could not be found`
for tracks that still resolve through `music/item_by_uri`. The MA server log gives
the actual cause: `No stream URL available for Soundcloud track ...`.

Read-only checks confirmed that affected tracks are streamable, have valid track
authorization, and offer AAC 160k / AAC 96k HLS rather than MP3. MA 2.10.5's
`_get_stream_url` rejects every preset that does not start with `mp3`.

The accompanying narrow patch backports the stream-resolution methods from the
[upstream SoundCloud provider](https://github.com/music-assistant/server/blob/dev/music_assistant/providers/soundcloud/__init__.py):

- Accept AAC alongside MP3, retaining progressive stream preference.
- Prefer full streams over 30-second previews.
- Recognize `.m3u8` HLS URLs on other CDN hosts.
- Recognize lowercase `expires` as well as the existing expiry parameters.

This does not bypass DRM, geographical or account restrictions, or change queues,
routing, recommendations, source quality settings or the MA database. Upstream's
preview-only fallback is preserved; a preview must not displace an available full
stream. AAC 160k is the highest stream offered for the inspected tracks, not
lossless audio.

## Validation

Six isolated contract tests pass against the staged provider:

```sh
MA_SOUNDCLOUD_SOURCE=/path/to/staged/soundcloud/__init__.py \
  python -m pytest tests/unit/python/test_soundcloud_aac_backport.py -q
```

Read-only live resolution for four affected tracks returned HTTP 200 HLS
playlists. No playback commands were issued. This confirms URL resolution, not a
full listening or seeking test.

## Deployment requirement

The patch is staged, not installed in the running MA process. Apply it only to a
copy of the installed **2.10.5** provider, with `patch --dry-run -p1` first, and
verify the tests above. Do not replace the entire provider with the moving dev
branch. Keep the patched module at a stable SSD runtime path, outside rolling
backup directories, and mount it read-only at the existing provider module path.
A later MA version should use its native fix instead if these hunks no longer
match.

Loading the repair requires a provider reload or MA restart. Schedule that while
playback and profiling are idle; preserve current Docker configuration and the
existing player-queue repair mount. Never issue test play commands to home rooms
without explicit authorization. Back up any configuration changed before
recreating the container and verify health, mounted sources and queue state after.

An inspection also found the existing player-queue repair mount referring to a
cleaned-up deployment-backup path. The identical running module was recovered to
`/media/local/cache/musicassistant-patches/player_queues-controller.py`, with an
identical compatibility copy at the old mount source (the SSD is FAT and does not
support symlinks). A runtime-dependency marker protects that directory from
ordinary backup pruning. This restores the missing restart
dependency without restarting MA. During deployment, migrate that bind to the
stable path before removing the compatibility path.
