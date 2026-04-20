This `deploy/` tree mirrors the current deployable files from the repo.

It replaces the older flat `_cdx*` snapshot files from `C:\Users\hpdemo\Desktop\coding\deploy` with source-of-truth copies that match this branch.

This fork remains based on **BeoSound 5c by Markus Kirsten**. The mirrored deploy files preserve the upstream licensing and attribution requirements while carrying the fork-specific MASS, Kodi, OTA, and install updates.

Included here:
- Current MASS and Kodi source/player files
- Current service/unit files
- Current frontend files needed by the source UIs
- Current install/config helpers and config templates that affect compatibility
- Current upstream counterparts for older deploy snapshots such as `router.py`, `hardware-input.js`, `ui-store.js`, `local.py`, `powerlink.py`, and `system.html`

Intentionally excluded:
- Real secrets such as `service_secrets_cdx1.env`
- Device-local `/etc/beosound5c/config.json` snapshots such as `service_etc_config_cdx1.json`
- Historical flat snapshot variants that no longer match the repo
- `service_usb_export_cdx2.py`, because there is no current repo file with that role in upstream `main`

Compatibility notes:
- MASS source now runs on port `8783` so it does not collide with upstream Radio on `8779`.
- Kodi source runs on port `8782`.
- Existing installs should ensure `/etc/beosound5c/config.json` includes menu entries for `MUSIC -> mass` and `KODI -> kodi` if they are not regenerated from the latest `config/default.json`.
- `MASS_TOKEN` is required. `MASS_QUEUE_ID` and `MASS_PLAYER_ID` are optional because the services now attempt discovery, but pinning them is still recommended for deterministic behavior.
- OTA update checks use GitHub Releases. Leave the default upstream feed in place, or set `system.update_repo` / `BS5C_UPDATE_REPO` if a deployed fork should follow its own releases instead.
