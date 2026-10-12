# Personal Tidal offline cache

`beo-tidal-download.timer` checks every 30 minutes. A completed selection refreshes
once per 24 hours. The job only runs when all available MA players are verified
idle/off/stopped; paused, buffering and unknown states defer it. It rechecks during
transfers. It never sends player, queue or power commands.

Selection: 250 Tidal-mapped library tracks ranked by personal MA/provider counts
and BS5c listening history, plus all Tidal tracks from the 20 highest-ranked albums
(sum of those track scores). Existing most-played playlist credits count through
MA counts. This is personal ranking, not Tidal's public charts. Favourites break
otherwise equal scores.

Only subscriber-authenticated, explicitly unencrypted, direct lossless FLAC assets
are accepted. DASH, protected, unknown-protection and lossy assets are skipped.
No DRM keys, decryption, public download proxies or account passwords are used.
Music Assistant owns login renewal; expired authentication defers the worker.
The access token is read transiently from MA's encrypted settings via its container;
it is never written to this cache, status file or journal.

Files are standard lossless 16-bit/44.1 kHz FLAC where Tidal supplies that quality,
with title/artist/album/track tags, in `/media/local/music/HomeMedia-Tidal`.
The worker verifies the complete FLAC and saves a SHA256 receipt. Completed music
is never deleted or overwritten. SSD JSON state is atomic; no new SQLite database
is introduced. Free space reserve defaults to 10 GiB. Only unfinished `.part`
files belonging to the current transfer are cleaned up.

`/etc/beosound5c/tidal-download.json`: `enabled`, `tracks` (250), `albums` (20),
`reserve_bytes` (10737418240). Changes are read at the next check.
System → Services → TIDAL DOWNLOADS displays progress. This read-only card does not
restart unrelated services. Rockbox docking sync resolves exact MA/Tidal identities
against the cache, so downloaded music becomes eligible for predictive iPod sync
without waiting for a filesystem-provider library refresh. Existing unmanaged iPod
music is preserved.

Limitations: the cache cannot download protected assets; the selection is limited
to music known to your MA library; phone USB sync is a separate Android feature.
