# Home Assistant Integration

BeoSound 5c communicates with Home Assistant via **MQTT** (recommended) or **HTTP webhooks**. The transport is configured in the web UI (Home Assistant → Transport) or via `transport.mode` in `config.json`.

## MQTT (recommended)

Requires an MQTT broker — the [Mosquitto add-on](https://github.com/home-assistant/addons/tree/master/mosquitto) works well. Create a user for the BS5c in the add-on config, then configure the broker in the web UI. MQTT credentials go in `/etc/beosound5c/secrets.env`.

Topics follow the pattern `beosound5c/{device_slug}/out|in|status`:

```
beosound5c/living_room/out      → BS5c sends button events to HA
beosound5c/living_room/in       → HA sends commands to BS5c
beosound5c/living_room/status   → Online/offline (retained)
```

The device slug is derived from your device name (e.g. "Living Room" → `living_room`).

### Receiving events from BS5c

```yaml
trigger:
  - platform: mqtt
    topic: "beosound5c/living_room/out"
```

### Sending commands to BS5c

```yaml
action:
  - action: mqtt.publish
    data:
      topic: "beosound5c/living_room/in"
      payload: '{"command": "wake", "params": {"page": "now_playing"}}'
```

See [`config/homeassistant/example-automation.yaml`](../config/homeassistant/example-automation.yaml) for complete examples covering both MQTT and webhook transports.

## Webhooks

Set `transport.mode` to `"webhook"` and configure a webhook URL in the web UI. The BS5c will POST events to that URL. No broker required, but there is no inbound command path — HA can't send commands back to the BS5c.

For bidirectional control, use MQTT or `"both"`.

## HA configuration.yaml

Add the following if you want to embed Home Assistant pages in the BS5c UI (e.g. the Security camera view):

```yaml
http:
  cors_allowed_origins:
    - "http://<BEOSOUND5C_IP>"
  use_x_frame_options: false

homeassistant:
  auth_providers:
    - type: trusted_networks
      trusted_networks:
        - <BEOSOUND5C_IP>
      allow_bypass_login: true
    - type: homeassistant
```

**Security note**: These settings allow the BeoSound 5c to embed Home Assistant pages without authentication. Only add IPs you trust. This is intended for local network use.
