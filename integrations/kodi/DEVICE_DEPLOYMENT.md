> **v2 chronology update (20 November 2026):** For the Samsung/history extension,
> use [the safe module-only deployment](../../docs/youtube-chronology-samsung.md).
> Do **not** replace your live `services/library.py`; it contains later device
> features absent from the original staged ZIP. Existing Bedroom Kodi monitor
> v0.1.2 remains compatible and needs no changes.

# BS5c / LibreELEC YouTube — safe device deployment checklist

**The GitHub PR is not an on-device deployment.** This procedure must be run
from an authenticated shell on the BS5c after comparing the checked-out
branch against the installed service and taking a backup.

## 1. Establish the actual installed service path

```sh
ssh YOUR_BS5C_USER@192.168.4.103
systemctl show beo-library.service -p WorkingDirectory -p ExecStart
systemctl cat beo-library.service
systemctl is-active beo-library.service
```

`beo-library.service` normally runs
`~/beosound5c/services/library.py`, with environment variables loaded from
`/etc/beosound5c/secrets.env`. Verify this on the actual unit; don't assume
a different service name or location.

## 2. Stage and review

Download the `BS5c-SmartTube-installation-staging` GitHub Actions artifact
from the BS5c PR. It contains:
- `bs5c-youtube-server-staging.zip`: the changes to `services/library.py`,
  `services/lib/youtube_history.py`,
  `services/lib/youtube_collections.py`,
  `services/lib/speaker_start.py`
- `service.bs5c.youtube.history-0.1.2.zip`: independent Kodi service

Unzip the server staging files into a **temporary folder** on BS5c.
Before replacing `library.py`, compare it with the live copy using
`diff -u`; preserve any device-specific changes. The speaker_start fix
only eliminates untracked async tasks and was CI tested.

Make a dated backup of the four existing files before copying any code,
not only the SQLite database. Leave the other BS5c services and hardware
configurations untouched.

## 3. Configure the central-history key

Run `openssl rand -hex 32` **locally on the BS5c**, and set the generated
64-character value as `BS5C_YOUTUBE_HISTORY_KEY=` in
`/etc/beosound5c/secrets.env`. Keep permissions restrictive (0600), and
never include the key in Git, photos, log output or public Drive uploads.

Check that the `beo-library.service` unit actually loads that EnvironmentFile.
The history database defaults to
`/media/local/cache/youtube_history.sqlite3`, separate from existing
music listening history. Back it up regularly.

## 4. Activate only the library service

Once source files have been reviewed and copied into the actual installed
service directory, check their syntax:

```sh
python3 -m py_compile /PATH/TO/INSTALLED/services/library.py \
  /PATH/TO/INSTALLED/services/lib/youtube_history.py \
  /PATH/TO/INSTALLED/services/lib/youtube_collections.py \
  /PATH/TO/INSTALLED/services/lib/speaker_start.py
sudo systemctl restart beo-library.service
systemctl is-active beo-library.service
journalctl -u beo-library.service -n 80 --no-pager
```

A successful unit restart is not by itself proof of endpoint health: perform
an authenticated LAN GET of `/library/youtube/history?limit=1` using the
dedicated key, check a 200 response, then test a POST of an ordinary
YouTube playback event. Do not expose port 8788 publicly.

## 5. Install and test Bedroom Kodi first

- Install `plugin.video.smarttube` on Bedroom Kodi.
- In Kodi Add-ons, install `service.bs5c.youtube.history-0.1.2.zip`.
- Set monitor device ID `bedroom`, BS5c IP, the matching key, and Enable.
- In Pellucid, install the staged 3.1.3 skin ZIP; test the YouTube main-menu
  entry and every navigation tab.
- In Home Media settings, set Bedroom's Kodi HTTP/JSON-RPC URL and access
  credentials; install the **same production-certificate-signed** Android
  APK only after its Android build and tests pass.
- Play via SmartTube browser, then play an externally selected video from
  Home Media, and verify that both appear in BS5c history.

After Bedroom works, repeat Kodi monitor installation with distinct device
IDs `lounge` and `dining`. Each Kodi device retains local stream manifests,
cache and settings; no SMB/NFS shared SmartTube directory is needed.

## Rollback

Before changing anything, store the prior files and previous working Kodi
skin/add-on archives. If `beo-library` fails, restore the four saved server
files (and the original EnvironmentFile if changed), restart only
`beo-library.service` and re-check `journalctl`. Do **not** delete the
new YouTube SQLite file during rollback; it contains central viewing history
and can be backed up separately. On Kodi, disable the independent monitoring
add-on and revert to the previous Pellucid ZIP. On Android, retain a copy of
the previously signed stable APK and a settings backup.
