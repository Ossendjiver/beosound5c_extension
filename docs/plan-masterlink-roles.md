# Master Link service

## Four concerns

The masterlink service owns four orthogonal concerns. (1) is always on; (2)/(3)/(4) are mutually exclusive — pick one via `masterlink.role` (default `master`).

| #   | Concern                  | What it does                                                                                                                                                              | Module                                  |
|-----|--------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------|
| (1) | **Decode IR**            | Decode Beo4 keycodes from the PC2 (USB msg type 0x02) and forward to beo-router. Always on. Filtered by `ml.ir.{audio,video}`.                                             | `services/masterlink.py` (PC2Device)    |
| (2) | **Audio master role**    | Reply to `MASTER_PRESENT` / `AUDIO_BUS` / `GOTO_SOURCE`. Broadcast clock. Forward link-device Beo4 transport keys to router. Auto-engage ML distribute when a link is seen. | `services/lib/masterlink_master.py`     |
| (3) | **Provider role**        | N.MUSIC / N.RADIO source-center for an external audio master (BS9000 / BC2). Streams audio + metadata when the master activates the source.                                | `services/lib/masterlink_provider.py`   |
| (4) | **Link role**            | We're a link speaker; some other device is master. Receive sources, decode track metadata for the local UI.                                                                | `services/lib/masterlink_link.py`       |

Each role module exposes the same shape: `__init__(pc2)`, `start(loop)`, `handle_telegram(...)`. `services/masterlink.py:_dispatch_ml` filters addressing and hands off to whichever role is selected.

Source-label mappers (Spotify→N.MUSIC, internet radio→N.RADIO) are metadata-driven; the difference between the two protocol slots is the shape of the metadata struct, not the player.

## Status

### Done

- **Config UI + schema** (commit `0e65708`): Config UI card in `web/softarc/config.html`; default schema in `config/default.json` and `docs/config.schema.json`.
- **Provider role** (`services/lib/masterlink_provider.py`): BC2-verified N.MUSIC source-burst, session state machine (idle / active / display_only) with watchdog, BC2 display updates from `/router/ws` media stream, transport-key forwarding from ML→router.
- **Link role** (`services/lib/masterlink_link.py`): MASTER_PRESENT discovery, STATUS_INFO + EXTENDED_SOURCE_INFORMATION + DISPLAY_SOURCE decode → `/router/media`, outbound GOTO_SOURCE.
- **Master role** (`services/lib/masterlink_master.py`, May 2026): replies to MASTER_PRESENT / AUDIO_BUS / GOTO_SOURCE, broadcasts clock, forwards Beo4 transport keys from link devices, auto-engages ML audio distribute when a link device has been seen recently. **Verified end-to-end against a BeoLab 2000 on Office BS5c** — control telegrams + audio over ML pair both confirmed working. Required a USB-frame reassembly fix in the sniffer (BeoLab 2000 frames arrive as 7+7+3 chunks; were being dropped as "Short ML telegram").
- **Uniform role pattern**: all three roles are sibling modules with the same shape (`__init__(pc2)`, `start(loop)`, `handle_telegram(...)`). `_dispatch_ml` is a thin addressing filter with no role-specific logic.

### TODO

#### Provider role

- [ ] **N.RADIO source-burst is UNVERIFIED** against real hardware. The N.MUSIC chain was BC2-tested in 04/2026; the N.RADIO equivalent was built by swapping source bytes and recomputing checksums. Capture against a real BC2/BS9000 serving N.RADIO is needed to confirm latching.
- [ ] **Audio injection.** Open question: can the PC2 push audio onto ML when not in audio-master mode at the firmware level? `set_address_filter()` is left in audio-master mode for all roles; flipping it for provider may be needed if BC2 expects `dest=0xC2` traffic to actually reach us.
- [ ] **`_set_session_mode` is bookkeeping-only.** Provider's assert burst writes raw `0xE5` routing bytes that bypass `set_routing()`, so `mixer_state['local']` lies after the burst. Restoring local audio path post-burst would need to re-issue `set_routing(local=True)` but is gated behind hardware-side testing of the audio injection question above.
- [ ] **Race on `_active_source_byte`** between sniffer thread (sets) and WS listener executor (reads). Single-byte reads — probably benign but unlocked.
- [ ] **MDT cross-reference.** `ml-tools` repo's `ml-netprovide` Python service has its own telegram sequence — protocol-level findings transfer even though their hardware is HiFiBerry+UART vs our PC2 USB.

#### Link role

- [ ] **Address-filter flip.** PC2 firmware-side filter still set to audio-master (listens for `0xC1` + broadcasts). With `OUR_NODE_ID=0xC2`, link-role traffic addressed specifically to us at `0xC2` may be dropped before we see it. Most STATUS_INFO is broadcast to `0x83` (ALL_LINK_DEVICES) so the broadcast path works without a flip; verify against real master hardware.
- [ ] **Receive ML audio.** PC2-side path TBD; on PowerLink output it should "just work" since the bus already feeds the mixer. On Sonos/BlueSound — known structural limit (see below), no audio path.
- [ ] **LINK main-menu view.** New `web/sources/link/view.js` exposing the sources we've configured + a metadata strip (now-playing) sourced from the decoded TRACK_INFO. Reachable via a top-level `LINK` menu entry that should auto-add when `role == "link"`.

#### BS9000 red-LED metadata (display widths)

`TRACK_INFO_LONG` work is tracked under the master-role TODOs above; one extra display question:

- [ ] **Confirm 8-char vs 12-char display widths.** BeoWorld threads suggest BS9000 splits across the disc display; truncation policy needs deciding.

#### Master role

Verified May 2026 against a BeoLab 2000 (control + audio). Pending follow-ups:

- [ ] **Capture and map remaining BeoLab 2000 panel buttons** — PLAY, TIMER, STOP. PLAY currently doesn't fully start playback (likely re-emits GOTO_SOURCE without a separate `go` keycode); TIMER's wire bytes are unknown. Press them with `--ml-sniff` and add unmapped keycodes to `services/lib/masterlink_provider.ML_BEO4_TRANSPORT_ACTIONS` (shared by master + provider).
- [ ] **REQUEST_LOCAL_SOURCE (`p=0x30`) reply.** BeoLab 2000 polls this; we silently ignore. If a link-device behaviour breaks without it, decode and reply with current source.
- [ ] **TRACK_INFO_LONG (`0x82`) population.** `_handle_goto_source` sends the libpc2 boilerplate `TRACK_INFO` (`0x44`) with no title/artist bytes. BS9000 / BC2 displays read `TRACK_INFO_LONG`; payload encoding documented in MLGW02 §7.x. Wire current player metadata in from `/router/media`.
- [ ] **Audio path on Sonos/BlueSound homes.** Structural limit: `is_powerlink_device()` only routes audio to ML when `volume.type == powerlink`. On Sonos, link rooms get telegrams + metadata but no music — out of scope unless we add an MDT-style HiFiBerry side-path.

## Open questions

1. **Should `LINK` auto-appear in the menu when `role == "link"`, or be an explicit menu toggle?** Today it's not wired at all. Auto-add feels right; users won't think to enable it separately.
2. **Source-label mapping (Spotify→N.MUSIC vs N.RADIO):** today the user picks one source per slot. Do we want rule-based dispatch (e.g. radio-shaped metadata → N.RADIO regardless of source)? Defer until provider role is built.
3. **IR toggles default to both-on** — matches today's behaviour. Should "audio master" implicitly disable video IR? B&O's Option 2 doesn't, so leaving as user choice.
4. **Provider role + link speakers simultaneously:** B&O sees these as one-or-the-other. We currently surface them as one role pick. If a future setup needs both (BS5c as N.MUSIC source for BS9000 *and* serving a BeoLab link speaker off itself), revisit.

## Sonos / non-local player caveat

The role config is identical for all `player.type` values — Config UI saves, `masterlink.py` reads, restart picks it up. The structural caveat is at the audio path:

| `player.type` | Control telegrams (master/provider/link replies, IR forwarding, metadata) | Audio on ML bus |
|---------------|---|---|
| `local`       | ✓ | ✓ (mpv → PipeWire → PowerLink → ML) |
| `sonos`       | ✓ | ✗ (audio bypasses PC2) |
| `bluesound`   | ✓ | ✗ (same) |

So Provider mode will get metadata to a BS9000 from a Sonos-backed BS5c, but the BS9000 won't hear music. Goal-1-style master with link speakers needs PowerLink. The Config UI hint surfaces this.
