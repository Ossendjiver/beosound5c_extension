# Stock YouTube return watchdog

The existing `beo-library` service checks Samsung's read-only application-status
API every five minutes. Unreachable/unknown checks retry after 30 seconds, then
60, 120 and up to 300 seconds; a confirmed response resets that backoff. It probes only `9Ur5IzDKqV.TizenYouTube` and
`111299001912`. It does not treat Samsung's permanently listed Cobalt stub,
YouTube Kids, YouTube TV or the replacement `tUb3Xq7Lm9.Tube` as a reinstall.
A successful response must name YouTube. Unknown/error/offline responses never
mean the app was removed; two consecutive complete 404 passes clear an incident.

Enable in the device's private `library` configuration (replace the host):

```json
{
  "youtube_watchdog": {
    "enabled": true,
    "host": "FRAME_LAN_IP",
    "pir_entities": [
      "binary_sensor.desk_pir",
      "binary_sensor.dining_pir",
      "binary_sensor.lounge_pir"
    ],
    "poll_seconds": 300,
    "reminder_seconds": 1800
  }
}
```

The service uses its existing HA credentials to subscribe to state-change events.
Only a fresh off/unknown-to-on edge from one of the configured PIRs can request a
warning. Old retained on states, other rooms, unavailable sensors and repeated
on updates cannot trigger it. On reconnect, a recent on state is accepted only
if it changed after service startup and within 60 seconds. A recently triggered
PIR can also release an incident discovered during that same minute.

The prompt uses the existing music-prompt overlay with warning priority, one
**Got it** button and a 90-second timeout. GO/click acknowledges; RIGHT also
acknowledges. Dismissal is persistent for the current incident. If it expires
without acknowledgement, another fresh PIR may remind after 30 minutes.
Removal and a later reinstall create a new incident. Detection and dismissal
survive restarts in the existing SSD library database; routine polls do not
write it. Read-only status: `GET :8788/library/youtube-watchdog`.

Only the BS5c `display_on` command is issued. No navigation, TV power/app launch,
remote key, route, queue, amplifier or playback command is sent. HA requires no
new helpers or automations. WebSocket retries back off from five seconds to five
minutes; short PIR pulses are captured without polling all HA states repeatedly.
No root, package deletion or Samsung developer connection is required.

This warns about a detected reinstall; it does not prevent it or uninstall it.
Stock application identifiers can change across firmware. Do not broaden the
check to every app named YouTube, because the replacement deliberately shares
that name. Validate new identifiers against the actual OEM package first.
