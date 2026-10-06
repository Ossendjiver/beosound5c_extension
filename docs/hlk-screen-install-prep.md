# HLK Screen Install Prep

This is the practical install-prep document for adding an HLK presence sensor to the BS5c main screen. It assumes the implementation described in [plan-hlk-screen-presence.md](./plan-hlk-screen-presence.md).

## Scope

The current planning assumes an LD2410-class HLK / Hi-Link UART radar sensor because the existing audited work is LD2410-specific.

If you choose a different HLK model:

- keep the mounting and integration guidance
- do not assume the serial framing or MQTT entity map will be identical

## What The Install Must Achieve

- wake the BS5c display when somebody is standing in front of it
- blank the display when nobody is near
- keep the decision local to the BS5c
- leave audio playback alone when the display blanks
- publish full serial telemetry to MQTT for Home Assistant

## Recommended Hardware Strategy

### Best Option

Use a small internal USB-UART adapter and present the sensor as `/dev/ttyUSB0`.

Why this is the safest default:

- avoids stealing the primary Pi UART
- avoids accidental interference with Bluetooth remote support
- keeps the GPIO overlay story simple

### Acceptable Option

Use a spare hardware UART on free GPIO pins after checking the final BS5c pin map and any installed HATs.

### Only Use If Deliberate

Reclaim the primary GPIO14/GPIO15 UART.

Do this only if:

- the target unit does not rely on Bluetooth remote support, or
- we have explicitly verified that the chosen UART remap does not break that path

Current repo defaults now assume this lounge-style wiring profile:

- `/dev/serial0`
- `GPIO14 TXD0` on header pin `8`
- `GPIO15 RXD0` on header pin `10`
- `dtoverlay=miniuart-bt`

## Wiring Notes

Minimum electrical connections:

- sensor `TX` -> BS5c `RX`
- sensor `RX` -> BS5c `TX`
- shared `GND`
- sensor power from the supply expected by the exact LD2410 board in hand

Do not assume the same pin order across all breakouts. Confirm the silkscreen or vendor documentation before power-up.

Serial settings:

- `256000 8N1`
- hardware UART only
- no software serial

If you use a GPIO UART, make sure Linux is not also trying to use that port for a serial console.

## Physical Placement

Goal:

- detect the person engaging with the BS5c
- avoid waking from general room movement

Recommended first-pass placement:

- mount the sensor behind or immediately adjacent to a non-metallic part of the main screen surround
- center it horizontally if possible
- aim it straight out or slightly downward toward a standing user
- keep the detection bubble tight to the screen interaction zone

Practical warnings:

- do not assume reliable operation through metal or metallised trim
- bench-test through the chosen fascia material before final reassembly
- leave enough cable slack to reposition the sensor during tuning

## BS5c-Specific OS Prep

Unlike the LibreELEC HLK package from the audit folder, BS5c uses the project's own Pi OS-style install flow.

That means:

- boot overlay changes belong in `/boot/firmware/config.txt`
- runtime dependencies belong in `install/requirements.txt`
- the service should be a normal `services/system/*.service` unit

Before field install, the repo work should add:

- `pyserial` to runtime and test dependencies
- an `hlk` block in `/etc/beosound5c/config.json`
- `beo-hlk.service`
- MQTT discovery publishing for HA

## Commissioning Checklist

1. Bench the sensor on the chosen UART before mounting it in the screen.
2. Confirm the BS5c sees the device path you intend to use.
3. Start the HLK service and confirm it connects cleanly.
4. Verify MQTT is enabled in BS5c transport config.
5. Confirm raw MQTT state shows:
   - `report_type: engineering`
   - `payload_length: 31`
6. Stand roughly `40-120 cm` in front of the screen and confirm immediate wake.
7. Step away and confirm the display blanks after the configured idle delay.
8. Confirm audio continues playing when the display blanks.
9. Press the BS5c power button while still present and confirm the manual-off override suppresses immediate re-wake.
10. Open Home Assistant and verify both raw telemetry and discovery entities are present.

## First Tuning Pass

Start with conservative screen-facing thresholds:

- close presence range: `25-150 cm`
- moving energy threshold: `>= 18`
- still energy threshold: `>= 12`
- auto-sleep delay: `20 s`
- minimum-on time after wake: `10 s`

Tune in this order:

1. reduce false wakes by shrinking the max distance
2. extend sleep delay before lowering energy thresholds
3. only widen the distance band if the screen misses genuine approach events

## Expected MQTT Outputs

### Full Serial Telemetry

`beosound5c/<device_slug>/hlk/serial`

One message per UART frame, including both commands sent to the sensor and reports received from it.

Example:

```json
{
  "timestamp": 1760000000,
  "direction": "tx",
  "frame_family": "command",
  "hex": "fdfcfbfa0200620004030201",
  "bytes": 12
}
```

### Latest Parsed State

`beosound5c/<device_slug>/hlk/state`

Carries the latest parsed report plus local decision fields such as `close_presence` and `display_should_be_awake`.

### Availability

`beosound5c/<device_slug>/hlk/availability`

Retained `online` / `offline`.

### High-Level BS5c Events

`beosound5c/<device_slug>/out`

Recommended HLK-related events:

- `hlk_presence_enter`
- `hlk_presence_exit`
- `display_wake`
- `display_sleep`

## Expected Home Assistant Entities

Recommended discovery entities:

- `binary_sensor.<device_slug>_hlk_presence`
- `binary_sensor.<device_slug>_hlk_moving`
- `binary_sensor.<device_slug>_hlk_still`
- `binary_sensor.<device_slug>_hlk_close_presence`
- `binary_sensor.<device_slug>_display_awake_by_presence`
- `sensor.<device_slug>_hlk_target_state`
- `sensor.<device_slug>_hlk_target_state_raw`
- `sensor.<device_slug>_hlk_detection_distance_cm`
- `sensor.<device_slug>_hlk_moving_distance_cm`
- `sensor.<device_slug>_hlk_still_distance_cm`
- `sensor.<device_slug>_hlk_moving_energy`
- `sensor.<device_slug>_hlk_still_energy`
- `sensor.<device_slug>_hlk_light_level`
- `sensor.<device_slug>_hlk_payload_length`

Optional diagnostic entities:

- per-gate moving/still energy sensors
- any raw-frame debug topic surfaced as a text sensor if desired

## Recommended HA Consumption Model

For this BS5c screen deployment:

- let the BS5c itself decide when the display wakes and sleeps
- use HA mainly for observability, dashboards, and optional higher-level automations
- build HA helpers from `close_presence`, not from ungated room-wide presence, if you want HA to reflect the same "close to the screen" behavior

## Practical Go / No-Go Checks Before Final Assembly

Proceed with permanent mounting only after all of these are true:

- the chosen UART path survives reboot
- Bluetooth behavior is unchanged if the unit depends on BeoRemote One pairing
- engineering mode remains stable for at least several minutes
- the screen wakes only for genuine near-screen presence
- the display can blank without muting or powering down audio
