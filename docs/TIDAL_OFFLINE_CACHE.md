# Personal Tidal offline cache

`beo-tidal-download.timer` checks every 30 minutes. A completed selection refreshes
once per 24 hours. The job only runs when all available MA players are verified
idle/off/stopped; paused, buffering and unknown states defer it. It rechecks during
transfers. It never sends player, queue or power commands.

Selection: 250 Tidal-mapped library tracks ranked by personal MA/provider counts
and BS5c listening history, plus explicit Tidal tracks from the 20 highest-ranked albums
(sum of those track scores). Existing most-played playlist credits count through
MA counts. This is personal ranking, not Tidal's public charts. Favourites break
otherwise equal scores.

Requests HI_RES_LOSSLESS (Tidal Max). Unencrypted direct FLAC and finite static
DASH/FLAC assets are accepted; the highest-resolution FLAC representation is used.
The worker downloads each HTTPS segment itself and verifies all audio frames.
Protected, unknown-protection and lossy assets are skipped. Explicit metadata must
be true in the library selection and in a fresh Tidal track response. Clean or
unlabelled tracks are excluded, even when an album is selected.
No DRM keys, decryption, public download proxies or account passwords are used.
Music Assistant owns login renewal; expired authentication defers the worker.
The access token is read transiently from MA's encrypted settings via its container;
it is never written to this cache, status file or journal.

Files retain the highest lossless bit depth/sample rate Tidal supplies (up to
24-bit/192 kHz),
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
without waiting for a filesystem-provider library refresh. Higher-quality upgrades
use a new filename and preserve previous files. Each daily run requests maximum
quality again before retaining a cached version. Existing unmanaged iPod
music is preserved.

Limitations: the cache cannot download protected assets; the selection is limited
to music known to your MA library; phone USB sync is a separate Android feature.


## Track count and library folders

System → Services → TIDAL DOWNLOADS: GO enters track-count editing, the wheel
changes the count in steps of 50, and GO saves. The −/+ and Save buttons provide
the same control. Range: 50–10,000. Changes affect the next pass, preserving the
current download. `beo-tidal-settings` serves this control on port 8789; mutations
are accepted only from the BS5c itself with a matching local browser origin.
Settings are atomic SSD JSON, not a new SQLite database. Default remains 250 tracks
and 20 albums; the queued 1,000-track expansion has been cancelled.

`beo-tidal-organise.timer` checks every five minutes. It holds the downloader's
shared lock and only acts after a completed pass and verified idle playback. It
reuses exact filesystem-provider directories first, then matching artist/album
folders (preferring an existing FLAC album folder). Otherwise it creates a normal
artist/album folder. Track names include their track/disc numbers. Existing files
are never overwritten: collisions receive a Tidal/quality suffix. Moves are atomic
no-overwrite renames on the SSD, with a recoverable JSON intent journal. Cache and
iPod references are updated after each move. Missing metadata or ambiguous unsafe
paths leave the original staged download intact for review. A move interrupted
before index update is verified by SHA256 and recovered on the next check.
