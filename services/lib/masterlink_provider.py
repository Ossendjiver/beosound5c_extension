# BeoSound 5c
# Copyright (C) 2024-2026 Markus Kirsten
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Master Link "provider" role — BS5c presents itself as the N.MUSIC source
# centre (node 0xC2) for an external audio master like a BC2 or BS9000.
#
# The chain that latches BC2 onto our N.MUSIC stream came out of a long
# packet-capture session against a real BC2.  It's effectively a snapshot
# of what a B&O source-centre device emits at source-entry — local PC2
# routing/volume init, then the canonical DISTRIBUTION_REQUEST /
# DISPLAY_SOURCE / STATUS_INFO / TRACK_INFO_LONG burst on the bus.  The
# byte sequences here are protocol facts (not creative expression) but
# they are *not* in MLGW02 or libpc2 — they are reverse-engineered from
# the bus.  Treat as "verified against one BC2 in 04/2026" and revisit
# if we find more masters that don't latch.
#
# Listens to /router/ws for media_update events and reformats current
# track titles into the 17-char BC2 display window via _send_live_update.
# A small state machine (idle / session_active / display_only) covers the
# case where BC2 reprobes after we've drifted.

import asyncio
import json
import logging
import time

import aiohttp

from lib.config import cfg
from lib.endpoints import ROUTER_PORT
from lib.masterlink_common import ML_BEO4_TRANSPORT_ACTIONS, forward_to_router

logger = logging.getLogger('beo-masterlink')

# WS URL of the router's media stream — we listen for {"type": "media_update"}
# broadcasts so we can repaint the BC2 display when a new track starts.
ROUTER_WS_URL = f"ws://localhost:{ROUTER_PORT}/router/ws"

VOL_MAX = int(cfg("volume", "max", default=70))
VOL_DEFAULT = int(cfg("volume", "default", default=30))

# Provider session lifecycle.
ML_STATE_IDLE = "idle"
ML_STATE_SESSION_ACTIVE = "session_active"
ML_STATE_DISPLAY_ONLY = "display_only"

# PC2 session-mode tags — used as a cache key on PC2Device.
PC2_SESSION_AUDIO = "audio"
PC2_SESSION_ML = "masterlink"


def format_bc2_title(title_text):
    """Format a title for the BC2 display window.

    Live findings: <=17 chars renders reliably; 18+ truncates or behaves
    inconsistently.  Prefer a word boundary inside the budget."""
    text = " ".join(str(title_text or "").split()).strip()
    if not text:
        return "Unknown Track"
    if len(text) <= 17:
        return text
    words = text.split(" ")
    acc = []
    total = 0
    for word in words:
        extra = len(word) if not acc else len(word) + 1
        if total + extra > 17:
            break
        acc.append(word)
        total += extra
    if acc and total >= 8:
        return " ".join(acc)
    return text[:17]


def build_extended_info_payload(info_type, text):
    """EXTENDED_SOURCE_INFORMATION (payload type 0x0B) layout.

    payload[0]  = info_type (0x04 = title, others discovered empirically)
    payload[1..13] = zeros (struct fields not yet mapped)
    payload[14:14+N] = printable text, latin-1
    payload[-1] = 0x00 terminator"""
    encoded = str(text).encode("latin-1", "replace")[:48]
    return [info_type & 0xFF] + ([0x00] * 13) + list(encoded) + [0x00]


class ProviderRole:
    """N.MUSIC source-centre for an external audio master.

    The role is configured in config.json:
      {"masterlink": {"role": "provider",
                      "provider": {"nmusic_source": "SPOTIFY",
                                   "nradio_source": ""}}}

    Today we only claim N.MUSIC.  N.RADIO needs a separate capture against
    a real BC2/BS9000 to discover the source-burst shape — see
    docs/plan-masterlink-roles.md.
    """

    def __init__(self, pc2):
        self.pc2 = pc2  # back-reference to PC2Device
        self.session_state = ML_STATE_IDLE
        self._last_state_change = 0.0
        self._last_bc2_activity = 0.0
        self._last_source_assert = 0.0
        self._last_distribution_request = 0.0
        self._last_keyconfirm = 0.0
        self._pending_reassert = False
        self._pending_title = None
        self._last_media_key = None
        self._cached_media = None
        self._last_pushed_title = None
        self._media_ws_connected = False
        # Which N.* source we currently claim — set on assert, cleared on
        # standby.  Used by metadata pushes to address the right ML
        # source-id and by the transport-key gate to accept either's keys.
        # 0x7A = N.MUSIC, 0xA1 = N.RADIO, None = no active session.
        self._active_source_byte = None

    # ── lifecycle ─────────────────────────────────────────────────────

    def start(self, loop):
        """Schedule background tasks on the sender thread's event loop."""
        loop.create_task(self._media_event_listener_loop())
        loop.create_task(self._session_watchdog_loop())
        logger.info("ML provider role: tasks started "
                    "(media listener + session watchdog)")

    # ── public dispatch entrypoint ────────────────────────────────────

    def handle_telegram(self, ttype, ptype, src_node, dest_node,
                        src_src, payload):
        """Called from PC2Device._dispatch_ml when role == 'provider'.

        ``src_src`` is the ML source-id byte at frame[8].  For BC2's
        N.MUSIC traffic it's 0x7A.
        """
        self._note_bc2_activity(f"ttype=0x{ttype:02X} ptype=0x{ptype:02X}")

        # MASTER_PRESENT — acknowledge so the master keeps polling us.
        if ttype == 0x0B and ptype == 0x04:
            self._reply_master_present(src_node)
            return

        # DISTRIBUTION_REQUEST → assert the matching source.  Source byte
        # in src_src: 0x7A = N.MUSIC, 0xA1 = N.RADIO.  After asserting, also
        # tell beo-router to activate the configured local source so the
        # external master actually has audio to consume (PowerLink) or at
        # least metadata to display.
        if ttype == 0x0B and ptype == 0x6C:
            if src_src == 0x7A:
                self._last_distribution_request = time.monotonic()
                self._assert_nmusic_source("distribution_request")
                self._activate_configured_source("nmusic")
                return
            if src_src == 0xA1:
                self._last_distribution_request = time.monotonic()
                self._assert_nradio_source("distribution_request")
                self._activate_configured_source("nradio")
                return

        # STANDBY → tear down session.
        if ptype == 0x10:
            self._clear_session("standby")
            return

        # Beo4 keys forwarded over ML inside an active N.MUSIC / N.RADIO
        # session.  Filter by `src_src` matching the source we claimed —
        # otherwise stray broadcasts on the bus could trip the gate.
        if ttype == 0x0A and ptype == 0x0D:
            if (src_src in (0x7A, 0xA1)
                    and src_src == self._active_source_byte
                    and len(payload) >= 1 and payload[0] == src_src):
                keycode = payload[1] if len(payload) > 1 else None
                if keycode == 0x7E:
                    self._handle_nmusic_key_release(payload)
                    return
                if keycode in ML_BEO4_TRANSPORT_ACTIONS:
                    self._forward_transport_key(keycode, payload)
                    return
                logger.info(
                    "ML provider: BEO4_KEY accepted-shape but unmapped "
                    "keycode=0x%02X src_src=0x%02X payload=%s",
                    keycode if keycode is not None else 0,
                    src_src,
                    " ".join(f"{b:02X}" for b in payload))
                return
            # Near-miss — log so we can discover what BS9000 / other masters
            # actually send.  Without this, transport-key gating failures are
            # silent.  src_src + first payload bytes are usually enough to
            # spot the variant; full payload printed too because some masters
            # encode keys at a different offset than payload[1].
            logger.info(
                "ML provider: BEO4_KEY near-miss (gate rejected) "
                "src_src=0x%02X active=%s payload=%s",
                src_src,
                f"0x{self._active_source_byte:02X}"
                if self._active_source_byte else "None",
                " ".join(f"{b:02X}" for b in payload))
            return

        # Anything else that landed here passed the addressed-to-us check
        # but hit no handler.  Log so we can discover unknown telegrams
        # against real hardware (BS9000 N.RADIO probes, BC2 quirks, etc.).
        logger.info(
            "ML provider: unhandled telegram t=0x%02X p=0x%02X "
            "src=0x%02X dest=0x%02X src_src=0x%02X payload=%s",
            ttype, ptype, src_node, dest_node, src_src,
            " ".join(f"{b:02X}" for b in payload[:16])
            + ("…" if len(payload) > 16 else ""))

    # ── BC2 N.MUSIC source assertion ──────────────────────────────────

    def _assert_nmusic_source(self, reason):
        """Full N.MUSIC source-burst that latches BC2 audio.

        Sequence (in order):
          1. 0xE7 0x00      — local mute off
          2. 0xE3 vol …     — set initial volume/tone
          3. 0xE5 …         — enable distribution path
          4. ML DISTRIBUTION_REQUEST init
          5. 2× ML DISPLAY_SOURCE ("N MUSIC     ")
          6. ML STATUS_INFO with N.MUSIC source id
          7. ML TRACK_INFO_LONG (BC2 displays this)
        Then EXTENDED_SOURCE_INFORMATION with the title via _send_extended_info.
        """
        pc2 = self.pc2
        pc2._set_session_mode(PC2_SESSION_ML)
        self._last_source_assert = time.monotonic()
        display = b"N MUSIC     "[:12]
        confirmed = int(pc2.mixer_state.get('volume_confirmed', 0) or 0)
        live = int(pc2.mixer_state.get('volume', 0) or 0)
        init_volume = max(0, min(VOL_MAX, confirmed or live or VOL_DEFAULT))

        replies = [
            [0xE7, 0x00],
            [0xE3, init_volume & 0x7F, 0x00, 0x00, 0x00],
            [0xE5, 0x00, 0x01, 0x00, 0x00],
            [
                0xE0, 0xC1, 0xC2, 0x01, 0x14, 0x00, 0x7A, 0x00,
                0x6C, 0x01, 0x08, 0x01, 0x88, 0x00,
            ],
            [
                0xE0, 0x83, 0xC2, 0x01, 0x2C, 0x00, 0x7A, 0x00,
                0x06, 0x11, 0x00, 0x03, 0x01, 0x01, 0x00, 0x00,
                *display, 0xA5, 0x00,
            ],
            [
                0xE0, 0x83, 0xC2, 0x01, 0x2C, 0x00, 0x7A, 0x00,
                0x06, 0x11, 0x00, 0x03, 0x01, 0x01, 0x00, 0x00,
                *display, 0xA5, 0x00,
            ],
            [
                0xE0, 0x83, 0xC2, 0x01, 0x14, 0x00, 0x7A, 0x00,
                0x87, 0x18, 0x04, 0x7A, 0x01, 0x00, 0x00, 0xBF,
                0xFE, 0x01, 0x00, 0x00, 0x00, 0xFF, 0x02, 0x01,
                0x00, 0x03, 0x03, 0x01, 0x01, 0x03, 0x00, 0x02,
                0x00, 0x00, 0x00, 0xBF, 0x00,
            ],
            [
                0xE0, 0xC1, 0xC2, 0x01, 0x14, 0x00, 0x00, 0x00,
                0x82, 0x0A, 0x01, 0x06, 0x7A, 0x00, 0x02, 0x00,
                0x00, 0x00, 0x00, 0x00, 0x01, 0xA8, 0x00,
            ],
        ]
        for idx, payload in enumerate(replies, 1):
            pc2.send_message(payload)
            logger.info("N.MUSIC ASSERT[%s] TX %d: %s",
                        reason, idx,
                        " ".join(f"{b:02X}" for b in payload))
            time.sleep(0.1 if idx <= 4 else 0.12)

        self._active_source_byte = 0x7A
        self._send_extended_info(f"N.MUSIC ASSERT[{reason}] META")
        self._pending_reassert = False
        self._set_session_state(ML_STATE_SESSION_ACTIVE, reason)

    def _assert_nradio_source(self, reason):
        """Full N.RADIO source-burst — structural mirror of the BC2-verified
        N.MUSIC chain with source byte 0xA1 and display "N RADIO     ".

        UNVERIFIED: built by changing 0x7A → 0xA1 in the N.MUSIC template
        and recomputing checksums via send_ml_telegram.  Real-bus capture
        against a master serving N.RADIO is needed to confirm the master
        latches.  If it doesn't, the most likely culprits are: (a) the
        STATUS_INFO payload may need different bytes for radio-shape
        sources, (b) the TRACK_INFO_LONG layout differs.

        Sequence matches `_assert_nmusic_source`: PC2 local init, ML
        DISTRIBUTION_REQUEST, 2× DISPLAY_SOURCE, STATUS_INFO,
        TRACK_INFO_LONG, then EXTENDED_SOURCE_INFORMATION via
        _send_extended_info."""
        pc2 = self.pc2
        pc2._set_session_mode(PC2_SESSION_ML)
        self._last_source_assert = time.monotonic()
        SRC = 0xA1
        display = b"N RADIO     "[:12]
        confirmed = int(pc2.mixer_state.get('volume_confirmed', 0) or 0)
        live = int(pc2.mixer_state.get('volume', 0) or 0)
        init_volume = max(0, min(VOL_MAX, confirmed or live or VOL_DEFAULT))

        # Phase 1: PC2-local init bytes (mute off / volume / distribute on).
        # Same opcodes as N.MUSIC — these are PC2 commands, not ML telegrams.
        for raw in (
            [0xE7, 0x00],
            [0xE3, init_volume & 0x7F, 0x00, 0x00, 0x00],
            [0xE5, 0x00, 0x01, 0x00, 0x00],
        ):
            pc2.send_message(raw)
            logger.info("N.RADIO ASSERT[%s] PC2: %s", reason,
                        " ".join(f"{b:02X}" for b in raw))
            time.sleep(0.1)

        # Phase 2: ML telegrams.  Use send_ml_telegram (auto checksum)
        # rather than hardcoded raw frames — the verified N.MUSIC chain
        # has hand-rolled checksums but BC2 accepts standard ones too
        # (some N.MUSIC display frames have non-standard checksums and
        # the master accepts them anyway).
        ml_sequence = [
            # 1. DISTRIBUTION_REQUEST init.
            dict(dest_node=0xC1, telegram_type=0x14, payload_type=0x6C,
                 payload_version=0x08, payload=[0x01],
                 dest_src=0x00, src_src=SRC),
            # 2,3. DISPLAY_SOURCE x2.
            dict(dest_node=0x83, telegram_type=0x2C, payload_type=0x06,
                 payload_version=0x00,
                 payload=[0x03, 0x01, 0x01, 0x00, 0x00, *display],
                 dest_src=0x00, src_src=SRC),
            dict(dest_node=0x83, telegram_type=0x2C, payload_type=0x06,
                 payload_version=0x00,
                 payload=[0x03, 0x01, 0x01, 0x00, 0x00, *display],
                 dest_src=0x00, src_src=SRC),
            # 4. STATUS_INFO with source byte at payload[1].
            dict(dest_node=0x83, telegram_type=0x14, payload_type=0x87,
                 payload_version=0x04,
                 payload=[SRC, 0x01, 0x00, 0x00, 0xBF, 0xFE, 0x01, 0x00,
                          0x00, 0x00, 0xFF, 0x02, 0x01, 0x00, 0x03, 0x03,
                          0x01, 0x01, 0x03, 0x00, 0x02, 0x00, 0x00, 0x00],
                 dest_src=0x00, src_src=SRC),
            # 5. TRACK_INFO_LONG — src_src is 0x00 here (matches N.MUSIC).
            dict(dest_node=0xC1, telegram_type=0x14, payload_type=0x82,
                 payload_version=0x01,
                 payload=[0x06, SRC, 0x00, 0x02, 0x00, 0x00, 0x00, 0x00,
                          0x00, 0x01],
                 dest_src=0x00, src_src=0x00),
        ]
        for idx, kw in enumerate(ml_sequence, 1):
            pc2.send_ml_telegram(src_node=pc2.OUR_NODE_ID, **kw)
            logger.info("N.RADIO ASSERT[%s] TX %d ml: dest=0x%02X "
                        "ttype=0x%02X ptype=0x%02X",
                        reason, idx, kw['dest_node'],
                        kw['telegram_type'], kw['payload_type'])
            time.sleep(0.12)

        self._active_source_byte = SRC
        self._send_extended_info(f"N.RADIO ASSERT[{reason}] META")
        self._pending_reassert = False
        self._set_session_state(ML_STATE_SESSION_ACTIVE, reason)

    def _reply_nmusic_key_confirm(self):
        """Second-stage reply after master reasserts the source via
        BEO4_KEY {src}{0x7E}.  Branches on `_active_source_byte`:
          - 0x7A or None → verified BC2 N.MUSIC raw frames
          - 0xA1 → N.RADIO equivalent via send_ml_telegram

        Misnomer: "nmusic" in the function name is preserved to keep
        callers stable, but it now handles either source."""
        pc2 = self.pc2
        pc2._set_session_mode(PC2_SESSION_ML)
        active = self._active_source_byte
        if active == 0xA1:
            # N.RADIO key-confirm — DISPLAY_SOURCE + STATUS_INFO + TRACK_INFO_LONG
            display = b"N RADIO     "[:12]
            seq = [
                dict(dest_node=0x83, telegram_type=0x2C, payload_type=0x06,
                     payload_version=0x00,
                     payload=[0x03, 0x01, 0x01, 0x00, 0x00, *display],
                     dest_src=0x00, src_src=0xA1),
                dict(dest_node=0x83, telegram_type=0x14, payload_type=0x87,
                     payload_version=0x04,
                     payload=[0xA1, 0x01, 0x00, 0x00, 0xBF, 0xFE, 0x01, 0x00,
                              0x00, 0x00, 0xFF, 0x02, 0x01, 0x00, 0x03, 0x03,
                              0x01, 0x01, 0x03, 0x00, 0x02, 0x00, 0x00, 0x00],
                     dest_src=0x00, src_src=0xA1),
                dict(dest_node=0xC1, telegram_type=0x14, payload_type=0x82,
                     payload_version=0x01,
                     payload=[0x06, 0xA1, 0x00, 0x02, 0x00, 0x00, 0x00, 0x00,
                              0x00, 0x01],
                     dest_src=0x00, src_src=0x00),
            ]
            for idx, kw in enumerate(seq, 1):
                pc2.send_ml_telegram(src_node=pc2.OUR_NODE_ID, **kw)
                logger.info("N.RADIO KEYCONFIRM TX %d (auto-checksum)", idx)
                time.sleep(0.12)
            self._send_extended_info("N.RADIO KEYCONFIRM META")
        else:
            # N.MUSIC (default) — verified BC2 raw frames.
            display = b"N MUSIC     "[:12]
            replies = [
                [
                    0xE0, 0x83, 0xC2, 0x01, 0x2C, 0x00, 0x7A, 0x00,
                    0x06, 0x11, 0x00, 0x03, 0x01, 0x01, 0x00, 0x00,
                    *display, 0xA5, 0x00,
                ],
                [
                    0xE0, 0x83, 0xC2, 0x01, 0x14, 0x00, 0x7A, 0x00,
                    0x87, 0x18, 0x04, 0x7A, 0x01, 0x00, 0x00, 0xBF,
                    0xFE, 0x01, 0x00, 0x00, 0x00, 0xFF, 0x02, 0x01,
                    0x00, 0x03, 0x03, 0x01, 0x01, 0x03, 0x00, 0x02,
                    0x00, 0x00, 0x00, 0xBF, 0x00,
                ],
                [
                    0xE0, 0xC1, 0xC2, 0x01, 0x14, 0x00, 0x00, 0x00,
                    0x82, 0x0A, 0x01, 0x06, 0x7A, 0x00, 0x02, 0x00,
                    0x00, 0x00, 0x00, 0x00, 0x01, 0xA8, 0x00,
                ],
            ]
            for idx, payload in enumerate(replies, 1):
                pc2.send_message(payload)
                logger.info("N.MUSIC KEYCONFIRM TX %d: %s", idx,
                            " ".join(f"{b:02X}" for b in payload))
                time.sleep(0.12)
            self._send_extended_info("N.MUSIC KEYCONFIRM META")
        self._set_session_state(ML_STATE_SESSION_ACTIVE, "key_confirm")

    def _send_live_update(self, title_text):
        """Push an in-session title update for the currently active source.

        Branches on `_active_source_byte`: 0x7A → verified N.MUSIC raw
        frames (BC2-tested), 0xA1 → N.RADIO equivalent via send_ml_telegram
        (unverified — see `_assert_nradio_source` docstring)."""
        pc2 = self.pc2
        title_text = format_bc2_title(title_text or "MasterLink Test")
        if not self._ensure_session_active_for_metadata(title_text, "live_update"):
            return
        active = self._active_source_byte
        pc2._set_session_mode(PC2_SESSION_ML)
        try:
            if active == 0x7A:
                # Verified N.MUSIC live-update frames (raw, hand-rolled
                # checksums; some non-standard but BC2 accepts).
                replies = [
                    [
                        0xE0, 0x83, 0xC2, 0x01, 0x14, 0x00, 0x7A, 0x00,
                        0x87, 0x18, 0x04, 0x7A, 0x01, 0x00, 0x00, 0xBF,
                        0xFE, 0x01, 0x00, 0x00, 0x00, 0xFF, 0x02, 0x01,
                        0x00, 0x03, 0x03, 0x01, 0x01, 0x03, 0x00, 0x02,
                        0x00, 0x00, 0x00, 0xBF, 0x00,
                    ],
                    [
                        0xE0, 0xC1, 0xC2, 0x01, 0x14, 0x00, 0x00, 0x00,
                        0x82, 0x0A, 0x01, 0x06, 0x7A, 0x00, 0x02, 0x00,
                        0x00, 0x00, 0x00, 0x00, 0x01, 0xA8, 0x00,
                    ],
                ]
                for idx, payload in enumerate(replies, 1):
                    pc2.send_message(payload)
                    logger.info("N.MUSIC LIVEUPDATE TX %d: %s", idx,
                                " ".join(f"{b:02X}" for b in payload))
                    time.sleep(0.12)
            elif active == 0xA1:
                # N.RADIO live-update — same telegram shape with source
                # byte 0xA1.  Auto-checksum via send_ml_telegram.
                pc2.send_ml_telegram(
                    dest_node=0x83, src_node=pc2.OUR_NODE_ID,
                    telegram_type=0x14, payload_type=0x87,
                    payload_version=0x04,
                    payload=[0xA1, 0x01, 0x00, 0x00, 0xBF, 0xFE, 0x01, 0x00,
                             0x00, 0x00, 0xFF, 0x02, 0x01, 0x00, 0x03, 0x03,
                             0x01, 0x01, 0x03, 0x00, 0x02, 0x00, 0x00, 0x00],
                    dest_src=0x00, src_src=0xA1,
                )
                time.sleep(0.12)
                pc2.send_ml_telegram(
                    dest_node=0xC1, src_node=pc2.OUR_NODE_ID,
                    telegram_type=0x14, payload_type=0x82,
                    payload_version=0x01,
                    payload=[0x06, 0xA1, 0x00, 0x02, 0x00, 0x00, 0x00, 0x00,
                             0x00, 0x01],
                    dest_src=0x00, src_src=0x00,
                )
                logger.info("N.RADIO LIVEUPDATE TX (auto-checksum)")
                time.sleep(0.12)
            else:
                logger.warning("Live update with no active source — skipping")
                return

            payload = build_extended_info_payload(0x04, title_text)
            src_label = "N.MUSIC" if active == 0x7A else "N.RADIO"
            logger.info("%s LIVEUPDATE META: 0x0B info_type=0x04 text=%r",
                        src_label, title_text)
            pc2.send_ml_telegram(
                dest_node=0x83,
                src_node=pc2.OUR_NODE_ID,
                telegram_type=0x2C,
                payload_type=0x0B,
                payload_version=0x01,
                payload=payload,
                dest_src=0x00,
                src_src=active,
            )
            time.sleep(0.12)
            self._pending_title = None
            self._last_pushed_title = title_text
        finally:
            self._restore_audio_session_if_needed()

    def _send_extended_info(self, label_prefix):
        """Append album/artist/title metadata frames for the active source.

        Source byte from `_active_source_byte` (0x7A N.MUSIC or 0xA1
        N.RADIO).  Falls back to N.MUSIC if no session is active — keeps
        callers like the BC2 reprobe path working without a precondition
        check."""
        pc2 = self.pc2
        src_src = self._active_source_byte or 0x7A
        try:
            title_text = format_bc2_title(self._current_title_text())
            fields = [
                (0x04, title_text),
            ]
            for idx, (info_type, text) in enumerate(fields, 1):
                payload = build_extended_info_payload(info_type, text)
                logger.info("%s %d: 0x0B info_type=0x%02X text=%r src=0x%02X",
                            label_prefix, idx, info_type, text, src_src)
                pc2.send_ml_telegram(
                    dest_node=0x83,
                    src_node=pc2.OUR_NODE_ID,
                    telegram_type=0x2C,
                    payload_type=0x0B,
                    payload_version=0x01,
                    payload=payload,
                    dest_src=0x00,
                    src_src=src_src,
                )
                time.sleep(0.12)
        finally:
            self._restore_audio_session_if_needed()

    # ── replies ───────────────────────────────────────────────────────

    def _reply_master_present(self, requesting_node):
        self.pc2.send_ml_telegram(
            dest_node=requesting_node,
            src_node=self.pc2.OUR_NODE_ID,
            telegram_type=0x14,        # STATUS
            payload_type=0x04,         # MASTER_PRESENT
            payload_version=4,
            payload=[0x01, 0x01, 0x01],
        )

    def _handle_nmusic_key_release(self, payload):
        """7A 7E — BC2 emitted source-confirm or just a held-key release.

        BC2 repeats this during an active N.MUSIC session including while
        turning volume.  Treat it as a source-entry handshake only when it
        lands inside 1.5s of a fresh DISTRIBUTION_REQUEST; otherwise this
        is the running session ticking and we ignore it (or, if we drifted
        into display-only, schedule a re-assert)."""
        now = time.monotonic()
        since_distribution = now - self._last_distribution_request
        since_keyconfirm = now - self._last_keyconfirm

        # After a service restart or dropped session, BC2 can keep probing
        # the source while we believe the ML session is idle.  If local
        # audio is already active, allow one key-confirm style recovery
        # from the reprobe stream so BC2 re-latches without a manual
        # source re-entry.
        mixer = self.pc2.mixer_state
        if (
            self.session_state == ML_STATE_IDLE
            and mixer.get('speakers_on')
            and mixer.get('local')
            and since_keyconfirm > 0.75
        ):
            self._last_keyconfirm = now
            logger.info(
                "Recovering idle ML session from BC2 reprobe: "
                "since_distribution=%.3fs since_keyconfirm=%.3fs payload=%s",
                since_distribution, since_keyconfirm,
                " ".join(f"{b:02X}" for b in payload),
            )
            self._reply_nmusic_key_confirm()
            return

        if since_distribution <= 1.5 and since_keyconfirm > 0.75:
            self._last_keyconfirm = now
            self._reply_nmusic_key_confirm()
            return

        if (self.session_state == ML_STATE_SESSION_ACTIVE
                and since_distribution > 1.5):
            self._mark_display_only("bc2_reprobe")
        logger.info(
            "Ignoring redundant N.MUSIC key confirm trigger: "
            "since_distribution=%.3fs since_keyconfirm=%.3fs payload=%s",
            since_distribution, since_keyconfirm,
            " ".join(f"{b:02X}" for b in payload),
        )

    def _activate_configured_source(self, slot):
        """Translate the configured menu key for the N.* slot into a
        router source-button event.

        ``slot`` is "nmusic" or "nradio".  The Config UI saves uppercase
        menu keys (e.g. "SPOTIFY", "APPLE MUSIC"); we map them to the
        per-menu source id ("spotify", "apple_music") via cfg("menu") and
        post that as the action.  beo-router resolves the action through
        its source-button map (or directly by source id) and activates the
        matching source service.

        No-op when the slot is empty or the menu key isn't enabled — the
        ML protocol assert still fired, so the master will still see us;
        we just don't have a local source to start.
        """
        from lib.config import cfg as _cfg
        slot_key = "nmusic_source" if slot == "nmusic" else "nradio_source"
        configured = ((_cfg("masterlink", default={}) or {}).get("provider")
                      or {}).get(slot_key, "") or ""
        if not configured:
            logger.info("Provider %s: no source configured — protocol "
                        "assert only, local audio not started", slot)
            return

        menu = _cfg("menu", default={}) or {}
        source_id = menu.get(configured)
        if not source_id:
            logger.warning("Provider %s: configured source %r not in "
                           "menu — cannot activate locally",
                           slot, configured)
            return

        pc2 = self.pc2
        if not pc2.session or not pc2.loop:
            logger.warning("Provider %s: router session not ready, "
                           "skipping local source activation", slot)
            return

        logger.info("Provider %s: activating local source %r (menu key %r)",
                    slot, source_id, configured)
        asyncio.run_coroutine_threadsafe(
            forward_to_router(pc2.session, source="masterlink",
                              action=source_id, device_type="Audio",
                              link="ML"),
            pc2.loop,
        )

    def _forward_transport_key(self, keycode, payload):
        action = ML_BEO4_TRANSPORT_ACTIONS[keycode]
        logger.info(
            "Forwarding ML transport key to router: keycode=0x%02X action=%s "
            "payload=%s",
            keycode, action,
            " ".join(f"{b:02X}" for b in payload),
        )
        pc2 = self.pc2
        if not pc2.session or not pc2.loop:
            logger.warning("Skipping ML transport key; router session not "
                           "initialized")
            return
        asyncio.run_coroutine_threadsafe(
            forward_to_router(pc2.session, source="masterlink",
                              action=action, device_type="Audio", link="ML"),
            pc2.loop,
        )

    # ── session state machine ────────────────────────────────────────

    def _set_session_state(self, state, reason):
        if self.session_state == state:
            return
        self.session_state = state
        self._last_state_change = time.monotonic()
        logger.info("ML session state -> %s (%s)", state, reason)
        if state == ML_STATE_SESSION_ACTIVE:
            self._push_cached_media_if_needed(f"state_{reason}")

    def _note_bc2_activity(self, reason):
        self._last_bc2_activity = time.monotonic()

    def _mark_display_only(self, reason):
        self._pending_reassert = True
        self._last_pushed_title = None
        self._set_session_state(ML_STATE_DISPLAY_ONLY, reason)

    def _clear_session(self, reason):
        self._pending_reassert = False
        self._pending_title = None
        self._active_source_byte = None
        self._set_session_state(ML_STATE_IDLE, reason)

    def _push_cached_media_if_needed(self, reason):
        media = self._cached_media or {}
        title = str(media.get("title") or "").strip()
        if not title:
            return
        formatted = format_bc2_title(title)
        if self._last_pushed_title == formatted:
            logger.info("Cached media already on BC2 display; skip push (%s): %r",
                        reason, formatted)
            return
        logger.info("Pushing cached media on session activation (%s): %r",
                    reason, title)
        self._send_live_update(title)

    def _current_title_text(self, fallback="MasterLink Test"):
        if self._pending_title:
            return str(self._pending_title)
        media = self._cached_media or {}
        title = str(media.get("title") or "").strip()
        if title:
            return title
        return str(fallback)

    def _ensure_session_active_for_metadata(self, title_text, reason):
        if self.session_state == ML_STATE_IDLE:
            logger.info("Skipping metadata push while ML session is idle (%s)",
                        reason)
            return False
        if self.session_state == ML_STATE_DISPLAY_ONLY:
            logger.info("Metadata requested while display-only; "
                        "re-latching first (%s)", reason)
            if self._active_source_byte == 0xA1:
                self._assert_nradio_source(f"{reason}_relatch")
            else:
                self._assert_nmusic_source(f"{reason}_relatch")
        if self.session_state != ML_STATE_SESSION_ACTIVE:
            logger.info("Skipping metadata push; ML session not active "
                        "after ensure (%s)", reason)
            return False
        self._pending_title = title_text
        return True

    def _restore_audio_session_if_needed(self):
        """Return PC2 to audio mode after transient ML metadata bursts.

        BC2 display updates require the ML session params, but leaving the
        PC2 there can disrupt the local audio path.  When local speakers
        are active, restore the audio session immediately."""
        mixer = self.pc2.mixer_state
        if mixer.get('speakers_on') and mixer.get('local'):
            self.pc2._set_session_mode(PC2_SESSION_AUDIO)

    # ── watchdog ─────────────────────────────────────────────────────

    def _session_watchdog_tick(self):
        if self.session_state != ML_STATE_DISPLAY_ONLY:
            return
        if not self._pending_reassert:
            return
        now = time.monotonic()
        if now - self._last_source_assert < 1.5:
            return
        # Reassert whichever source we last latched.  Active byte falls
        # back to N.MUSIC when no session was ever opened (defensive).
        if self._active_source_byte == 0xA1:
            logger.info("ML watchdog reasserting N.RADIO source")
            self._assert_nradio_source("watchdog")
        else:
            logger.info("ML watchdog reasserting N.MUSIC source")
            self._assert_nmusic_source("watchdog")

    async def _session_watchdog_loop(self):
        while self.pc2.running:
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self._session_watchdog_tick)
            except Exception as e:
                logger.warning("ML session watchdog failed: %s", e,
                               exc_info=True)
            await asyncio.sleep(1.0)

    # ── /router/ws media listener ────────────────────────────────────

    def _handle_media_update(self, media_data):
        if not isinstance(media_data, dict):
            return
        title = str(media_data.get("title") or "").strip()
        if not title:
            return
        self._cached_media = dict(media_data)

        artist = str(media_data.get("artist") or "").strip()
        album = str(media_data.get("album") or "").strip()
        state = str(media_data.get("state") or "").strip().upper()
        media_key = (title, artist, album, state)
        if media_key == self._last_media_key:
            return
        self._last_media_key = media_key

        mixer = self.pc2.mixer_state
        if self.session_state == ML_STATE_IDLE:
            if (self._last_pushed_title
                    and mixer.get("speakers_on")
                    and mixer.get("local")):
                logger.info("Recovering stale BC2 display from media update "
                            "while idle: %r", title)
                self._reply_nmusic_key_confirm()
                return
            if state == "PLAYING":
                logger.info("Marking ML session display-only from idle "
                            "media update: %r", title)
                self._mark_display_only("media_update_idle")
                return
            logger.info("Ignoring media update while ML session is idle: %r",
                        title)
            return

        logger.info("ML media update -> title=%r artist=%r album=%r state=%s",
                    title, artist, album, state or "?")
        self._send_live_update(title)

    async def _media_event_listener_loop(self):
        pc2 = self.pc2
        while pc2.running:
            try:
                if not pc2.session or pc2.session.closed:
                    await asyncio.sleep(1.0)
                    continue

                async with pc2.session.ws_connect(
                    ROUTER_WS_URL,
                    heartbeat=20,
                    timeout=aiohttp.ClientTimeout(total=None),
                ) as ws:
                    self._media_ws_connected = True
                    logger.info("Connected to router media stream: %s",
                                ROUTER_WS_URL)
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                payload = json.loads(msg.data)
                            except Exception:
                                continue
                            if payload.get("type") != "media_update":
                                continue
                            media_data = payload.get("data") or {}
                            loop = asyncio.get_running_loop()
                            await loop.run_in_executor(
                                None, self._handle_media_update, media_data)
                            continue

                        if msg.type in (
                            aiohttp.WSMsgType.CLOSE,
                            aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.CLOSING,
                        ):
                            logger.warning("Router media stream closed by peer")
                            break

                        if msg.type == aiohttp.WSMsgType.ERROR:
                            logger.warning("Router media stream error frame: %s",
                                           ws.exception())
                            continue
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("Router media stream disconnected: %s", e)
            finally:
                if self._media_ws_connected:
                    self._media_ws_connected = False
                    logger.info("Router media stream disconnected")
            await asyncio.sleep(2.0)


