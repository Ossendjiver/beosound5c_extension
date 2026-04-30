# Audio Setup Options

Each BeoSound 5c is configured with a **player** (how audio is played) and a **volume adapter** (how volume is controlled). Both are set in the web UI or directly in `config.json`.

## Which setup is right for me?

- **Sonos speakers?** Use Sonos as your player. The Sonos speaker handles playback natively — the BS5c sends commands and monitors what's playing but does not produce audio itself. All streaming sources (Spotify, Apple Music, TIDAL, Plex) work. Set `player.type` to `"sonos"` and `volume.type` to `"sonos"`.

- **BluOS player?** Use BlueSound as your player. Plex, CD, and USB work. Spotify, Apple Music, and TIDAL do not — they send share links that only Sonos handles via ShareLink. Set `player.type` to `"bluesound"` and `volume.type` to `"bluesound"`.

- **B&O PowerLink speakers?** Use PowerLink for volume. Local sources (CD, USB) play on the Pi and output to PowerLink speakers via the MasterLink bus. Streaming sources need a Sonos or BlueSound player. Set `volume.type` to `"powerlink"`.

- **Other speakers or amplifier?** Connect via HDMI, optical/Toslink, or RCA (with the appropriate HAT). Local sources play directly. Streaming sources need a Sonos or BlueSound player. Pick whichever output matches your cable.

## Player Types

The player service handles network-based playback. Sources send play commands to the player, which talks to the actual speaker.

| Player | Capabilities | How It Plays |
|---|---|---|
| Sonos | `spotify`, `url_stream` | ShareLink (Spotify, Apple Music, TIDAL) or `play_uri` (URLs) |
| BlueSound | `url_stream` | BluOS HTTP API with stream URLs |
| MASS | `url_stream` | Music Assistant player subscription + transport bridge |
| Local | `spotify`, `url_stream` | mpv via PipeWire/PulseAudio; Spotify via go-librespot |

Only one player is active — determined by `player.type` in config.json. The type guard in PlayerBase ensures only the matching player service starts.

## Source Compatibility

Sources check the player's capabilities at startup to determine how to play content.

| Source | Sonos | BlueSound | No Player |
|---|---|---|---|
| **Spotify** | Yes — ShareLink queues Spotify URIs natively | No | No |
| **Apple Music** | Yes — ShareLink handles Apple Music share URLs | No | No |
| **TIDAL** | Yes — ShareLink handles TIDAL share URLs | Yes — direct stream URLs | No |
| **Plex** | Yes — `play_uri` with direct stream URLs | Yes — direct stream URLs | No |
| **CD** | Yes — plays on Pi via mpv | Yes — plays on Pi via mpv | Yes |
| **USB** | Yes — streams track URLs to Sonos | Yes — streams track URLs | Yes — falls back to local mpv |

**Key points:**
- Spotify and Apple Music send share links via the `uri` parameter. Only Sonos handles these (via its ShareLink plugin). BlueSound ignores `uri` — it only supports direct stream URLs via `url`.
- TIDAL works with both players: on Sonos it uses ShareLink (player manages queue); on BlueSound it resolves direct stream URLs via tidalapi and manages its own queue (like Plex)
- Plex works with both players because it sends direct stream URLs (via `url`), not share links
- Plex and TIDAL (on BlueSound) manage their own queues (next/prev build new stream URLs) while Spotify and Apple Music let the player handle queue advancement after the initial share link is queued
- CD always plays locally via mpv — it doesn't use the player service
- USB auto-detects: if the player supports `url_stream`, it streams track URLs to the player; otherwise falls back to local mpv

### MASS

Music Assistant can also be the configured `player.type`. In that mode BS5c monitors a target MASS player directly, exposes transport controls through the MASS websocket API, and can use the `hass` volume adapter for Home Assistant-driven zone volume.

**Config:**
```json
"player": { "type": "mass", "ip": "musicassistant.local" },
"volume": { "type": "hass", "output_name": "Music Assistant", "mlgw_step_multiplier": 2.0 }
```

**Secrets / env:**
- `MASS_TOKEN` is required
- `MASS_WS_URL` overrides the websocket endpoint if needed
- `MASS_QUEUE_ID` and `MASS_PLAYER_ID` are optional pins when auto-discovery is not deterministic
- `volume.mlgw_step_multiplier` is optional when the `hass` adapter is bridged to MLGW. Default is `2.0` so each BS5c volume gesture sends a stronger MLGW step burst than the HA fallback path.

### Sonos

The Sonos speaker handles playback natively. The BS5c sends commands and monitors what's playing (track info, artwork, volume) but does not produce the audio itself. Works with any Sonos speaker — S1 or S2, any generation.

**Config:**
```json
"player": { "type": "sonos", "ip": "192.168.1.100" },
"volume": { "type": "sonos", "host": "192.168.1.100", "max": 70 }
```

### BlueSound

The BluOS player handles playback via its HTTP/XML API. The BS5c sends commands and monitors playback via long-polling. Works with any BluOS device (Node, PowerNode, Vault, etc.).

**Config:**
```json
"player": { "type": "bluesound", "ip": "192.168.1.100" },
"volume": { "type": "bluesound", "host": "192.168.1.100", "max": 70 }
```

### PowerLink

Uses the original B&O PowerLink bus via a PC2/MasterLink USB interface. The BS5c sends volume and power commands through `masterlink.py`, which controls the speakers over the bus. Works with any B&O PowerLink speaker (BeoLab 6000, BeoLab 8000, etc.) or a BeoLink Passive with passive speakers.

**Config:**
```json
"volume": { "type": "powerlink", "max": 70 }
```

### HDMI

Uses the Pi's second micro-HDMI port (HDMI1) as a digital audio output. HDMI0 drives the BS5 display. Volume is controlled via ALSA software mixer (`amixer`). Connect to an amplifier, soundbar, or any device with HDMI audio input.

**Config:**
```json
"volume": { "type": "hdmi", "max": 70 }
```

### Optical / Toslink (S/PDIF)

Requires an S/PDIF HAT such as the HiFiBerry Digi or InnoMaker Digi One. Outputs bit-perfect digital audio via coaxial RCA or optical TOSLINK. Volume is controlled via ALSA software mixer.

**Setup:**
1. Add `dtoverlay=hifiberry-digi` to `/boot/firmware/config.txt`
2. Reboot and verify with `aplay -l`

**Config:**
```json
"volume": { "type": "spdif", "max": 70 }
```

### RCA

Requires a DAC HAT with RCA analog output (e.g. HiFiBerry DAC+, IQaudIO DAC). Volume is controlled via ALSA software mixer.

**Setup:**
1. Add the appropriate dtoverlay to `/boot/firmware/config.txt` (e.g. `dtoverlay=hifiberry-dacplus`)
2. Reboot and verify with `aplay -l`

**Config:**
```json
"volume": { "type": "rca", "max": 70 }
```

### BeoLab 5 (via BeoLab 5 Controller)

A custom option for controlling a pair of BeoLab 5 speakers via their sync port. Requires the BeoLab 5 Controller — a dedicated ESP32 board that sends serial commands to both speakers.

**Config:**
```json
"volume": { "type": "beolab5", "host": "beolab5-controller.local", "max": 70 }
```

## How Playback Works

There are two playback paths depending on the source:

**Remote playback** — The source sends a play command to the player service (port 8766), which forwards it to the Sonos or BlueSound speaker. The speaker fetches and plays the audio. This is how Spotify, Apple Music, TIDAL, and Plex work. USB also uses this path when the player supports `url_stream`.

**Local playback** — The source plays audio directly on the Pi using mpv. For wired outputs (PowerLink, HDMI, Optical, RCA) audio goes directly to the hardware. CD always plays locally. USB falls back to this mode when no player with `url_stream` is available.

**Source-managed playback** — Some sources talk to their backend directly and do not rely on the shared player abstraction for the actual play call. MASS controls its Music Assistant queue/player directly, and Kodi controls Kodi's JSON-RPC player directly. These sources still register with the router so the PLAYING screen and transport controls follow the active source correctly.

## Sources

Sources provide content to the BS5c. Each source registers with the router and appears in the menu. The remote's media keys (play, pause, next, prev) are forwarded to whichever source is currently active.

| Source | Playback Method | Queue Management |
|---|---|---|
| Spotify | Sends Spotify share URLs to player via `player_play(uri=...)`. Sonos uses ShareLink to queue natively. Sonos only. | Player manages queue |
| Apple Music | Sends Apple Music share URLs to player via `player_play(uri=...)`. Sonos uses patched ShareLink. Sonos only. | Player manages queue |
| TIDAL | Sonos: sends TIDAL share URLs via `player_play(uri=...)` (ShareLink). BlueSound: resolves direct stream URLs via tidalapi `track.get_url()`, sends via `player_play(url=...)`. | Sonos: player manages queue. BlueSound: source manages queue (next/prev play new stream URLs) |
| Plex | Builds direct stream URLs from Plex server. Sends to player via `player_play(url=...)`. Works with Sonos and BlueSound. | Source manages queue (next/prev build new URLs) |
| MASS | Browses Music Assistant library data and sends play commands directly to the configured MASS queue/player. | Source manages queue and now-playing handoff |
| Kodi | Browses Kodi / LibreELEC video and live-TV libraries via JSON-RPC and opens items directly in Kodi. | Kodi manages queue / playlist playback |
| CD | Local mpv playback from USB CD/DVD drive. Metadata from MusicBrainz. No player service needed. | Source manages tracks (mpv chapters) |
| USB | Auto-detects: streams track URLs to player if `url_stream` available, otherwise local mpv. Supports BeoMaster 5 library databases and plain USB drives. Works with both players or standalone. | Source manages queue |

## Volume Adapters

The router sends volume commands through whichever adapter matches the configured output. Each adapter handles debouncing and power management independently.

| Adapter | Debounce | Power On/Off | Balance | Host Required |
|---|---|---|---|---|
| `sonos` | 50ms | No | No | `player.ip` (default) |
| `bluesound` | 50ms | No | No | `player.ip` (default) |
| `beolab5` | 100ms | Yes | Yes | `beolab5-controller.local` (default) |
| `powerlink` | 50ms | Yes | Yes | `localhost:8768` (default) |
| `c4amp` | 50ms | Yes | No | Required (`volume.host`) |
| `hdmi` | 50ms | No | No | N/A (local ALSA) |
| `spdif` | 50ms | No | No | N/A (local ALSA) |
| `rca` | 50ms | No | No | N/A (local ALSA) |

Adapters are pluggable — write a custom one to control your amplifier over HTTP, IR, or anything else. See [`services/lib/volume_adapters/`](../services/lib/volume_adapters/) for all adapters and the base class.

### Config Reference

The `volume` section in `config.json`:

```json
"volume": {
  "type": "sonos",          // "sonos", "bluesound", "beolab5", "powerlink", "c4amp", "hdmi", "spdif", or "rca"
  "host": "192.168.1.100",  // Target IP/hostname (sonos, bluesound, beolab5, c4amp)
  "max": 70,                // Maximum volume percentage
  "step": 3,                // Volume step per wheel click
  "mlgw_step_multiplier": 2.0, // Optional: scales MLGW-only step bursts when volume.type is "hass"
  "output_name": "Sonos"    // Name shown in the UI
}
```

## Spotify Setup

1. **Create a Spotify Developer App** (free) at [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard):
   - Click "Create App" — name it anything (e.g. "BeoSound 5c")
   - Add this Redirect URI: `https://<device-ip>:8772/callback` (the setup page on the device shows the exact URI)
   - Select "Web API"
   - Copy the Client ID

2. **Configure**: Enter the Client ID in the web UI under Sources → Spotify, or add it to `config.json`:
   ```json
   { "spotify": { "client_id": "your-client-id-here" } }
   ```

3. **Authenticate**: Navigate to SPOTIFY on the BS5 display and scan the QR code with your phone.

**Notes:**
- Spotify apps in "Development" mode allow up to 25 users. Add your Spotify account email under **User Management** in the developer dashboard.
- A self-signed SSL certificate is generated during install (required for Spotify OAuth). Your phone must accept the certificate warning when scanning the QR code.

### Spotify Canvas (Optional)

Canvas shows looping video backgrounds behind tracks in immersive mode — the same videos you see in the Spotify mobile app. Not all tracks have a Canvas.

To enable Canvas, add your `sp_dc` cookie to `/etc/beosound5c/secrets.env`:

1. Log into [open.spotify.com](https://open.spotify.com) in a browser
2. Open DevTools → **Application** → **Cookies** → `open.spotify.com`
3. Copy the value of `sp_dc`
4. On the device: add `SPOTIFY_SP_DC="<your-cookie>"` to `/etc/beosound5c/secrets.env`
5. Restart: `sudo systemctl restart beo-source-spotify`

The cookie is valid for ~1 year.
