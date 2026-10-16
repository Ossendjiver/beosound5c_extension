# SoundCloud transfer/resume repair (MA 2.10.5)

The 16 October transfer log shows a phone -> Link move at 01:30:11 UTC.
MA stops the source and transfers the queue before preparing destination audio.
At 01:30:26 it reports `Timeout waiting for audio data`, while the app's ordinary
15-second command deadline expires almost simultaneously. No HA room activation
is sent after that failed call. This is an audio-preparation failure, not evidence
of a command to the wrong room.

The affected SoundCloud AAC/fMP4 HLS stream decodes from the beginning. FFmpeg
input-side `-ss 135 -i URL` failed to produce audio within 15 seconds (and also
stalled during a longer check). Output-side `-i URL -ss 135` decoded one second of
audio successfully in about 1.5 seconds on the same host. Checks decode to a null
sink and do not issue player, room, queue or audible playback commands.

The narrow patch in `tools/patches/music-assistant-2.10.5-soundcloud-hls-seek.patch`
uses output-side decode/discard seeking only for SoundCloud **track HLS** streams.
Other providers, progressive streams, live sources, disallowed seeking and starts
at zero retain their existing behavior. The original requested offset remains
available for duration bookkeeping. MP3/AAC stream selection is unchanged.

Five isolated tests execute the actual staged streaming method with a fake
decoder and inspect the resulting input/output arguments:

```sh
MA_STREAMS_AUDIO_SOURCE=/path/to/staged/music_assistant/controllers/streams/audio.py \
  python -m pytest tests/unit/python/test_soundcloud_hls_seek_backport.py -q
```

The full installed module requires Python 3.14; tests extract only the changed
method so they can run in the BS5c repository's Python 3.12 test runtime. Compile
the complete staged module with the container's Python before deployment.

Output-side seeking decodes preceding audio, so very late positions in long mixes
can require extra startup time. This patch fixes the reproduced transfer offset;
it is not a promise that every long seek completes within MA's preparation budget.
If long-position resumes remain too slow, a bounded HLS segment trim with correct
initialization and timestamp handling needs separate implementation and testing.

## App changes

The app gives queue transfers their own 45-second command deadline. It sends the
mutation once, then confirms the destination through reads even if the reply was
lost. It requires the same item IDs/order/current song and stable expected
playback state before activating an MLGW room. When MA has moved the queue but
could not start its audio, the error identifies that the destination queue was
retained. It does not resend, clear queues, replay the source, or turn on the room.

## Deployment

This patch is staged for a controlled MA restart while players and profiling are
idle. Preserve the installed SoundCloud AAC provider and player-queue routing
mounts. Add the patched audio module as a read-only bind from a stable SSD runtime
path, outside backup retention folders. Create and verify an encrypted SSD cold
backup with MA stopped, validate library/auth database integrity, and check queue
contents/positions, service health and mounted source files after restart.

The log also contains a separate Tidal preview authentication failure and repeated
HTTP 400 feedback submissions. These do not explain the reproduced SoundCloud
seek failure and are not changed by this repair.
