# Contributing to BeoSound 5c

This fork is based on **BeoSound 5c by Markus Kirsten**. Please preserve upstream credit, keep the attribution notice required by `LICENSE` Section 7(b), and avoid removing documentation or UI references that identify the upstream project.

## Reporting Issues

Open a GitHub issue with steps to reproduce and relevant logs (`journalctl -u beo-* -f`). If a bug appears to come from upstream rather than this fork-specific layer, include the upstream commit or release you were testing against.

## Suggesting Features

Open a GitHub issue or discussion with your use case, any hardware assumptions, and whether the change belongs in upstream `mkirsten/beosound5c`, this fork, or a Home Assistant automation.

## Submitting Code

This project is built for my personal setup, but contributions should be **as generic as possible**:

- **Setup-specific logic** (e.g., what happens when a button is pressed) belongs in Home Assistant automations, not the codebase
- **User-specific values** belong in configuration files (`/etc/beosound5c/config.json`, `/etc/beosound5c/secrets.env`)
- **Generic features** that work across different setups are welcome in the project

When adding features:
- Ensure they work in emulator mode — add mocks where needed so others can test without hardware
- Keep changes minimal and focused

### Code Style

Using AI for code assistance is fine. Please:
- Sanity check generated code
- Keep changes minimal — don't refactor unrelated code
- Match the existing style

## License

By contributing, you agree that your contributions will be licensed under the repository's **GPL-3.0-or-later** terms, including the attribution requirement in `LICENSE`.

## Local Development

The web UI includes built-in hardware emulation — no physical BS5 required:

```bash
cd web && python3 -m http.server 8000
# Open http://localhost:8000
```

Controls: mouse wheel = laser, arrow up/down = nav wheel, PageUp/PageDown = volume, arrow left/right + Enter = buttons.

To add live Sonos artwork and metadata, set `player.ip` in `config/default.json` and run:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install soco pillow websockets aiohttp
cd services && python3 players/sonos.py
```

## Repo Layout

```
config/                     # Per-device configuration
├── default.json            #   Fallback for local dev / fresh install
├── secrets.env.example     #   Credentials template
└── <device>.json           #   One per device (deployed to /etc/beosound5c/)
services/                   # Backend Python services
├── sources/                #   Music sources (Spotify, Plex, CD, USB, Radio, News…)
├── players/                #   Playback backends (Sonos, BlueSound, Local/mpv)
├── lib/                    #   Shared libs (player_base, source_base, volume_adapters…)
├── router.py               #   Event router (beo-router)
├── input.py                #   USB HID input (beo-input)
├── bluetooth.py            #   BeoRemote BLE (beo-bluetooth)
├── masterlink.py           #   MasterLink IR (beo-masterlink)
└── system/                 #   Systemd service templates
web/                        # Web UI (HTML, CSS, JavaScript)
├── js/                     #   UI logic, hardware emulation
├── softarc/                #   Arc-based navigation subpages
└── sources/                #   Source view presets
install/                    # Installer
tools/                      # Spotify OAuth, BLE testing, publish script
docs/                       # Documentation
```

## Deploying to a Device

`deploy.sh` syncs files and restarts services without touching device-specific data (playlists, config.json):

```bash
./deploy.sh                              # Sync + restart beo-http and beo-ui
./deploy.sh beo-player-sonos             # Restart a specific service
./deploy.sh beo-*                        # Restart all beo-* services
./deploy.sh --no-restart                 # Sync files only
BEOSOUND5C_HOSTS="my-device.local" ./deploy.sh  # Target a specific device
```

Device hostnames are configured in `my-hosts.env` (see `my-hosts.env.example`).

## Deploy Mirror

This fork also keeps a mirrored `deploy/` tree with the current deployable copies of the changed source, player, install, and frontend files. When you update deployable runtime files, keep the matching `deploy/` copy in sync in the same commit.
