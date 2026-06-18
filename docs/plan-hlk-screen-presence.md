# HLK Screen Presence Plan

## Goal

Install an HLK / Hi-Link LD2410-class mmWave sensor on the BS5c main display so the screen:

- wakes when somebody is close to the screen
- blanks when nobody is near
- keeps the control loop local to the BS5c
- broadcasts full serial telemetry to MQTT for Home Assistant

This document is a build plan for the BS5c repo. It is not a claim that the feature already exists.

## What We Already Have

- `services/input.py` already owns screen/backlight control and the `screen_on`, `screen_off`, `wake`, and `status` commands.
- `services/lib/transport.py` already manages MQTT connectivity on `beosound5c/{device_slug}/out|in|status`.
- The install stack already grants `dialout` and `tty` group access, which helps with UART access.
- MQTT is already a first-class transport in the project.

## Important Constraint

The current `screen_off` command in `services/input.py` also powers off the configured audio output through `ROUTER_OUTPUT_OFF`.

That is fine for a deliberate "turn the system off" action, but it is the wrong behavior for automatic presence-based display blanking. The HLK feature therefore needs a display-only off path rather than reusing `screen_off` as-is.

## Lessons Pulled From The Existing HLK Work

From `C:\Users\hpdemo\Desktop\HA Audit\v4\ld2410_mqtt_bridge_20260602`:

- LD2410 serial framing is already understood and working.
- The sensor wants `256000 8N1` on a real hardware UART.
- Engineering mode matters because it exposes distance, per-gate energy, and light-level data.
- The sensor can fall back to normal reports at runtime, so the bridge should automatically re-enable engineering mode.
- Publishing `payload_length` is useful for debugging report-shape regressions.
- The existing helper thresholds are a good starting point, but the BS5c screen use-case should use a much tighter distance band than whole-room presence.

## Recommended Architecture

### 1. Add A Dedicated Native HLK Service

Create a new service, for example:

- `services/hlk.py`
- `services/system/beo-hlk.service`

Reasoning:

- keeps high-baud serial parsing separate from the already-large `beo-input` service
- keeps the screen-presence loop local to the BS5c
- makes restart/failure boundaries clearer
- lets us reuse the same telemetry model later if more than one sensor is added

### 2. Extract Shared Display Power Helpers

Move the direct screen/backlight logic from `services/input.py` into a shared helper, for example:

- `services/lib/display_power.py`

That helper should provide:

- `set_display_awake(on: bool)`
- `is_display_awake()`
- `toggle_display_awake()`

It should support two policies:

- `display_only`
  Used by HLK auto-wake / auto-sleep. Must not power audio outputs down.
- `system_off`
  Used by the existing manual `screen_off` behavior when the audio-output power-off side effect is wanted.

### 3. Add An HLK Protocol Module

Create a reusable parser/helper module, for example:

- `services/lib/hlk_protocol.py`

This should carry over the audited LD2410 work:

- report parser
- command frame builder
- engineering-mode enable / re-enable
- decoded fields such as presence, moving/still distance, energies, gates, and light level

### 4. Reuse Existing MQTT Settings

The HLK service should use the existing BS5c MQTT broker settings from the transport config and secrets. It should not introduce a second broker config model.

Practical implication:

- HLK screen presence requires `transport.mode` to include MQTT (`mqtt` or `both`)

### 5. Extend MQTT Publishing Beyond `out|in|status`

The current transport abstraction is oriented around one command topic and one event topic. HLK needs arbitrary publish topics and retained availability/discovery topics.

Recommended change:

- extend `services/lib/transport.py` with a generic MQTT publish helper
- or add a small shared MQTT publisher alongside it that reuses the same broker credentials

## Local Presence Policy

The screen policy should be local, conservative, and tuned for "person standing at the display", not "someone exists somewhere in the room".

Recommended starting defaults:

- close presence range: `25-150 cm`
- moving energy threshold: `>= 18`
- still energy threshold: `>= 12`
- auto-sleep delay: `20 s`
- minimum-on time after wake: `10 s`
- boot grace before auto-sleep starts: `30 s`

Recommended derived state:

- `raw_presence`
  Direct decode from LD2410 target-state bytes.
- `close_presence`
  Presence that also passes the tighter screen-distance / energy gates.
- `display_should_be_awake`
  Final local decision after debounce, minimum-on time, and manual override rules.

## Manual Override Rules

Without an override, the user could press the BS5c power button while standing in front of the screen and have the HLK service immediately wake it again.

Recommended rule set:

- manual screen-off sets a temporary override
- the override is cleared by either:
  - the next explicit user interaction, or
  - a timeout such as `300 s`
- manual screen-on clears any override immediately

## MQTT Contract

Keep the current transport topics unchanged:

- `beosound5c/{device_slug}/out`
- `beosound5c/{device_slug}/in`
- `beosound5c/{device_slug}/status`

Add HLK-specific topics:

- `beosound5c/{device_slug}/hlk/serial`
  One message per UART frame, including both TX and RX frames. This is the "all serial information" topic.
- `beosound5c/{device_slug}/hlk/state`
  Latest decoded report plus derived local fields such as `close_presence`.
- `beosound5c/{device_slug}/hlk/availability`
  Retained `online` / `offline`.

Recommended `hlk/serial` payload shape:

```json
{
  "timestamp": 1760000000,
  "direction": "rx",
  "frame_family": "report",
  "hex": "f4f3f2f1...",
  "bytes": 37
}
```

Recommended `hlk/state` payload shape:

```json
{
  "timestamp": 1760000000,
  "report_type": "engineering",
  "report_type_raw": 1,
  "payload_length": 31,
  "target_state": "moving",
  "target_state_raw": 1,
  "presence": true,
  "moving": true,
  "still": false,
  "moving_distance_cm": 82,
  "moving_energy": 31,
  "still_distance_cm": 0,
  "still_energy": 0,
  "detection_distance_cm": 82,
  "moving_target_gate": 2,
  "still_target_gate": null,
  "moving_gate_energies": [0, 3, 31, 9, 0, 0, 0, 0, 0],
  "still_gate_energies": [0, 0, 0, 0, 0, 0, 0, 0, 0],
  "light_level": 74,
  "close_presence": true,
  "display_should_be_awake": true
}
```

Use `beosound5c/{device_slug}/out` for higher-level events such as:

- `hlk_presence_enter`
- `hlk_presence_exit`
- `display_wake`
- `display_sleep`

## Home Assistant Discovery

Publish MQTT discovery directly from the BS5c so HA can consume the sensor without extra YAML.

Recommended discovered entities:

- `binary_sensor.{device_slug}_hlk_presence`
- `binary_sensor.{device_slug}_hlk_moving`
- `binary_sensor.{device_slug}_hlk_still`
- `binary_sensor.{device_slug}_hlk_close_presence`
- `binary_sensor.{device_slug}_display_awake_by_presence`
- `sensor.{device_slug}_hlk_target_state`
- `sensor.{device_slug}_hlk_target_state_raw`
- `sensor.{device_slug}_hlk_detection_distance_cm`
- `sensor.{device_slug}_hlk_moving_distance_cm`
- `sensor.{device_slug}_hlk_still_distance_cm`
- `sensor.{device_slug}_hlk_moving_energy`
- `sensor.{device_slug}_hlk_still_energy`
- `sensor.{device_slug}_hlk_light_level`
- `sensor.{device_slug}_hlk_payload_length`
- optional diagnostic gate-energy sensors if they prove useful

For this screen-specific deployment, discovery should expose the local `close_presence` decision so HA can mirror the same behavior the BS5c uses.

## Proposed Config Shape

Add a new top-level config block:

```json
"hlk": {
  "enabled": true,
  "port": "/dev/serial0",
  "baudrate": 256000,
  "sensor_name": "screen_hlk",
  "engineering_mode": true,
  "availability_timeout_s": 10,
  "publish_heartbeat_s": 30,
  "publish_raw_frames": true,
  "mqtt_discovery": true,
  "close_presence_min_cm": 25,
  "close_presence_max_cm": 150,
  "moving_energy_min": 18,
  "still_energy_min": 12,
  "sleep_delay_s": 20,
  "min_on_s": 10,
  "boot_grace_s": 30,
  "manual_override_timeout_s": 300
}
```

The current repo defaults assume the lounge-style primary UART wiring:

- `/dev/serial0`
- `GPIO14 TXD0` on header pin `8`
- `GPIO15 RXD0` on header pin `10`
- `dtoverlay=miniuart-bt`

## Hardware Recommendation For BS5c

Preferred UART strategy on BS5c:

1. USB-UART adapter inside the cabinet
   Safest option because it avoids Bluetooth/UART pin conflicts.
2. Spare secondary hardware UART on free GPIO pins
   Good option if we confirm the actual pin map on the target build.
3. Reclaim primary GPIO14/GPIO15 UART
   Only if we knowingly accept the Bluetooth tradeoff or confirm the target build does not depend on it.

For the current BS5c HLK rollout, the default config and setup UI now target this third option so they match the existing lounge deployment pinout.

Why this matters:

- the HLK audit packages were staged around reclaiming UART pins on LibreELEC hosts
- BS5c already has Bluetooth remote support in the repo
- we should not casually steal the primary UART if it risks the BeoRemote One path

## Files To Add Or Update

Recommended scope:

- `services/lib/display_power.py`
- `services/lib/hlk_protocol.py`
- `services/hlk.py`
- `services/system/beo-hlk.service`
- `services/system/install-services.sh`
- `services/system/service-registry.sh`
- `install/requirements.txt`
- `tests/requirements.txt`
- `config/default.json`
- `docs/config.schema.json`
- optional installer / web-config wiring if we want HLK exposed in setup UI

## Test Plan

Add unit tests for:

- LD2410 frame parsing
- engineering-mode auto-recovery when normal reports appear
- close-range presence gating
- auto-sleep debounce / minimum-on-time behavior
- manual override behavior
- MQTT topic serialization for `hlk/serial`, `hlk/state`, and availability

Field verification checklist:

1. sensor enumerates on the chosen serial device
2. engineering reports stay active (`report_type=engineering`, `payload_length=31`)
3. screen wakes within one sample window when a person approaches
4. screen sleeps after the configured idle delay
5. audio keeps playing when the display blanks
6. manual-off override prevents immediate re-wake
7. HA sees both raw serial telemetry and discovery entities

## Recommended Delivery Order

1. Extract display-only power control from `beo-input`
2. Add the HLK parser + local service
3. Add raw/parsed MQTT publishing
4. Add HA discovery
5. Add config schema and install/UI hooks
6. Tune live thresholds on the actual BS5c screen
