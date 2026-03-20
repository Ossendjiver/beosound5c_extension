# Fix slow camera loading on Security dashboard

## Context
The SECURITY view on real devices loads an HA dashboard (`dashboard-cameras/home?kiosk`) in an iframe. That dashboard has 10 cameras set to `camera_view: live`, causing 10 simultaneous RTSP-to-HLS stream conversions — each taking 5-10s. No go2rtc is configured.

**Fix**: Replace the HA dashboard iframe with the existing `softarc/security.html`, enhanced to poll camera snapshots via the existing camera proxy (`localhost:8767/camera/snapshot`). Snapshots load in ~50ms each and refresh every 2s, giving near-live experience without the heavy HA frontend.

## Changes

### 1. `config/kitchen.json` — point SECURITY to local page + add camera list
Change `"SECURITY": { "url": "http://homeassistant.local:8123/..." }` to `"SECURITY": { "url": "softarc/security.html" }` and add:
```json
"security": {
  "cameras": [
    { "entity": "camera.doorbell_medium_resolution_channel", "name": "Door" },
    { "entity": "camera.garden_medium_resolution_channel", "name": "Garden" },
    { "entity": "camera.g3_flex_high_resolution_channel_6", "name": "Gate" },
    { "entity": "camera.driveway_high_resolution_channel", "name": "Playhouse" },
    { "entity": "camera.g4_doorbell_pro_poe_package_camera", "name": "Package" },
    { "entity": "camera.north_medium_resolution_channel_2", "name": "Back" },
    { "entity": "camera.192_168_1_203", "name": "Kitchen" },
    { "entity": "camera.192_168_1_204", "name": "Office" },
    { "entity": "camera.g3_flex_high_resolution_channel_7", "name": "Office 2" },
    { "entity": "camera.lab_medium_resolution_channel", "name": "Lab" }
  ]
}
```

### 2. `config/church.json` — same SECURITY URL change + camera list
Change SECURITY URL. Add `security.cameras` with the appropriate cameras for church.

### 3. `config/default.json` — add demo security cameras (no entities)
Add `"security": { "cameras": [...] }` with 9 demo names (no `entity` field) so dev mode uses existing demo images.

### 4. `web/softarc/security.html` — rewrite to dynamic snapshot polling
Replace static 9-camera demo grid with:

- **Include** `../js/config.js` for AppConfig defaults
- **Load config** via fetch to `../json/config.json` (same pattern as scenes.html line 57), fall back to default.json for dev
- **Dynamic grid**: Build camera cells from `config.security.cameras`. Adapt grid dimensions:
  - 1-4 cameras → 2x2
  - 5-9 cameras → 3x3
  - 10-12 cameras → 4x3
- **Snapshot polling**: Every 2s, fetch `http://localhost:8767/camera/snapshot?entity=<entity>&t=<cachebuster>` for each camera. Use Image preload trick (new Image → swap src on load) to avoid flicker.
- **Staggered initial load**: Space first fetches 100ms apart to avoid 10 simultaneous requests.
- **Demo fallback**: If camera has no `entity`, use matching demo image from `../images/demo/`.
- **Error handling**: Show "Unavailable" per-cell on failure, retry on next cycle.
- **Visibility optimization**: Pause polling when iframe is hidden (`visibilitychange` event).
- **Max width**: Reduce from 900px to 850px (wheel occlusion at ~870px).

### 5. No changes to `services/input.py` or `web/camera-proxy.py`
The existing `/camera/snapshot` endpoint handles everything needed.

## Verification
1. Run `cd web && python3 -m http.server 8000`, open `http://localhost:8000/softarc/security.html` — should show demo images in grid
2. Deploy to office device, navigate to SECURITY — should show live camera snapshots loading within 1-2s
3. Verify snapshots refresh (timestamps update, images change)
4. Verify grid adapts if cameras are added/removed from config
