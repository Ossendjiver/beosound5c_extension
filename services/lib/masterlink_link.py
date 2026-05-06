# BeoSound 5c
# Copyright (C) 2024-2026 Markus Kirsten
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Master Link "link" role — BS5c presents itself as a link speaker (node
# 0xC2) under another audio master like a BC2 / BeoSound 9000.  We:
#
#   * probe for the master at startup with broadcast MASTER_PRESENT
#     REQUEST and capture its node id from the reply
#   * reply to inbound MASTER_PRESENT so the master's per-link probes
#     keep us in its topology
#   * decode incoming STATUS_INFO (0x87) and EXTENDED_SOURCE_INFORMATION
#     (0x0B) frames into a now-playing dict, push to /router/media so
#     the existing UI renders without LINK-specific code
#   * expose POST /link/source on the masterlink mixer port to send an
#     outbound GOTO_SOURCE (0x45) to the discovered master.
#
# Inbound Beo4 keys arrive separately: the PC2 surfaces them as USB
# msg_type 0x02 frames, which masterlink.py's `process_beo4_keycode`
# already decodes role-agnostically and forwards to the router.  Outbound
# transport-key TX from our local remote *back* to the master is parked
# in docs/plan-masterlink-roles.md (it surfaces from local Beo4 input,
# not from ML).
#
# Byte offsets and info_type meanings come from ml-debug/const.py
# (https://gitlab.com/masterdatatool/software/ml-tools), which credits
# https://github.com/Lele-72 for the reverse-engineering work.

import asyncio
import logging

import aiohttp

from lib.endpoints import ROUTER_MEDIA

logger = logging.getLogger('beo-masterlink')

# How often we re-broadcast MASTER_PRESENT REQUEST when no master is
# known yet.  Once master_node_id is set we stop probing.
_DISCOVERY_INTERVAL = 30.0

# EXTENDED_SOURCE_INFORMATION info_type meanings depend on which source
# is currently playing.  Tables from ml-debug/const.py:60-64.  Keys are
# our internal source-id labels (matching _ML_SOURCES values in
# masterlink.py); values map info_type → field name we want to surface
# to /router/media.
#
# Skip info_type=5 ("Beo4 button") and 6 ("Unknown") — not user-facing.
_EXT_INFO_AUDIO = {       # A.MEM / A.MEM2 / CD / N.MUSIC etc.
    0x01: "genre",
    0x02: "album",
    0x03: "artist",
    0x04: "title",
}
_EXT_INFO_RADIO = {       # RADIO / N.RADIO
    0x02: "genre",
    0x03: "country",
    0x04: "title",        # RDS info — closest fit to a "now playing" line
}

# Source-id byte → display label and which info-type table to use.  The
# labels match what router.py's media_update consumers expect; the type
# selector picks the right info_type table above.
_SOURCE_KIND = {
    # video sources (we don't render these but decode anyway)
    0x0B: ("tv",      "audio"),    # placeholder — TV info_type table not mapped
    0x29: ("dvd",     "audio"),
    0x1F: ("dtv",     "audio"),
    # audio sources
    0x6F: ("radio",   "radio"),
    0x79: ("a.mem",   "audio"),
    0x7A: ("n.music", "audio"),
    0x8D: ("cd",      "audio"),
    0x97: ("a.aux",   "audio"),
    0xA1: ("n.radio", "radio"),
}


class LinkRole:
    """BS5c-as-link-speaker — discover the master, mirror its now-playing."""

    def __init__(self, pc2):
        self.pc2 = pc2
        self.master_node_id = None  # discovered via MASTER_PRESENT reply

        # Accumulated now-playing.  STATUS_INFO sets the source/state;
        # EXTENDED_SOURCE_INFORMATION fills in title/album/artist over a
        # series of frames.  We push to router whenever a field changes.
        self._media = {
            "_source_id": None,    # router-internal source label
            "source_byte": None,   # raw 0xnn from STATUS_INFO
            "title": "",
            "artist": "",
            "album": "",
            "genre": "",
            "country": "",
            "state": "",
            "track": None,
        }
        self._last_pushed = None   # tuple of pushable fields, dedupe gate

    # ── lifecycle ─────────────────────────────────────────────────────

    def start(self, loop):
        """Schedule discovery probe on the sender thread's event loop."""
        loop.create_task(self._discovery_loop())
        logger.info("ML link role: discovery loop started")

    # ── public dispatch entrypoint ────────────────────────────────────

    def handle_telegram(self, ttype, ptype, src_node, dest_node,
                        src_src, payload):
        """Called from PC2Device._dispatch_ml when role == 'link'."""

        # MASTER_PRESENT reply — capture master node id.  ttype=0x14
        # (STATUS) with our addressing; the address filter above already
        # ensured dest is us / a broadcast we listen to.
        if ttype == 0x14 and ptype == 0x04 and src_node not in (0x80, 0x81, 0x83):
            if self.master_node_id != src_node:
                logger.info("ML link role: master discovered at node 0x%02X",
                            src_node)
                self.master_node_id = src_node
            return

        # MASTER_PRESENT REQUEST from peer link devices — let the master
        # answer.  Replying ourselves here would confuse the topology.
        if ttype == 0x0B and ptype == 0x04:
            logger.debug("ML link role: ignoring peer MASTER_PRESENT request "
                         "from 0x%02X (master will reply)", src_node)
            return

        # Inbound BEO4_KEY arrives via PC2 USB msg_type 0x02, decoded by
        # masterlink.py's process_beo4_keycode and forwarded to the router
        # role-agnostically — nothing to do here.

        # STATUS_INFO — source identity + activity + track number.
        if ttype in (0x14, 0x2C) and ptype == 0x87:
            self._handle_status_info(payload)
            return

        # EXTENDED_SOURCE_INFORMATION — title/artist/etc bytes.
        if ttype in (0x14, 0x2C) and ptype == 0x0B:
            self._handle_extended_source_info(payload)
            return

        # DISPLAY_SOURCE — source name as shown on master display.
        # Useful as a fallback when STATUS_INFO source byte is unknown.
        if ttype == 0x2C and ptype == 0x06:
            self._handle_display_source(payload)
            return

        # Discovery logging — promoted to INFO so unknown telegrams
        # surface in default logs.  Helps figure out what a real BC2 or
        # BS9000 sends to a link speaker beyond the frames we already
        # decode.  Truncates long payloads to keep log volume bounded.
        logger.info(
            "ML link: unhandled telegram t=0x%02X p=0x%02X "
            "src=0x%02X dest=0x%02X src_src=0x%02X payload=%s",
            ttype, ptype, src_node, dest_node, src_src,
            " ".join(f"{b:02X}" for b in payload[:16])
            + ("…" if len(payload) > 16 else ""))

    # ── master discovery ─────────────────────────────────────────────

    def _send_master_present_request(self):
        """Broadcast a MASTER_PRESENT REQUEST.

        REQUEST telegram_type=0x0B, payload_type=0x04, dest=0x83
        (ALL_LINK_DEVICES).  Empty payload, pver=1.  An audio master on
        the bus replies with telegram_type=0x14 + payload [0x01,0x01,0x01]
        per libpc2 telegram.cpp."""
        try:
            self.pc2.send_ml_telegram(
                dest_node=0x83,
                src_node=self.pc2.OUR_NODE_ID,
                telegram_type=0x0B,
                payload_type=0x04,
                payload_version=1,
                payload=[],
            )
            logger.info("ML link role: sent MASTER_PRESENT REQUEST broadcast")
        except Exception as e:
            logger.warning("ML link role: MASTER_PRESENT REQUEST failed: %s", e)

    async def _discovery_loop(self):
        """Probe for the master until we see one, then go quiet."""
        # Initial delay — let the PC2 finish its init before we shout.
        await asyncio.sleep(2.0)
        while self.pc2.running:
            if self.master_node_id is None:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    None, self._send_master_present_request)
            await asyncio.sleep(_DISCOVERY_INTERVAL)

    # ── inbound decode ───────────────────────────────────────────────

    def _handle_status_info(self, payload):
        """Decode STATUS_INFO (0x87) into source/activity/track.

        Byte layout (relative to payload, ml-debug/const.py:78-91 with
        the libpc2 header offset of 10 subtracted):
          payload[0]  = source byte (e.g. 0x6F RADIO, 0x8D CD)
          payload[9]  = channel/track number
          payload[11] = activity (0x01 stop, 0x02 playing, …)
          payload[12] = 0x01 audio source, 0x02 video source"""
        if len(payload) < 13:
            logger.debug("ML link: STATUS_INFO too short (len=%d)", len(payload))
            return
        source_byte = payload[0]
        track = payload[9]
        activity = payload[11]
        source_id, _kind = _SOURCE_KIND.get(source_byte, (None, None))

        # If the source changed, blank out the metadata accumulator —
        # otherwise a stale title from the previous source bleeds onto
        # the new one until the master sends fresh 0x0B frames.
        if source_byte != self._media.get("source_byte"):
            for f in ("title", "artist", "album", "genre", "country"):
                self._media[f] = ""
            self._media["source_byte"] = source_byte
            self._media["_source_id"] = source_id

        self._media["track"] = track if track else None
        self._media["state"] = self.pc2._ML_SOURCE_ACTIVITY.get(
            activity, f"0x{activity:02X}").lower()

        logger.info(
            "ML link STATUS_INFO: source=0x%02X (%s) track=%d activity=%s",
            source_byte, source_id or "?", track,
            self._media["state"])
        self._maybe_push("status_info")

    def _handle_extended_source_info(self, payload):
        """Decode EXTENDED_SOURCE_INFORMATION (0x0B) into named fields.

        Layout (matching `lib.masterlink_provider.build_extended_info_payload`):
          payload[0]   = info_type
          payload[1:14] = zeros (struct fields not yet mapped)
          payload[14:] = printable text, latin-1, NUL-terminated

        Field meaning depends on the active source — radio uses a
        different info_type table than A.Mem/CD."""
        if len(payload) < 15:
            logger.debug("ML link: EXTENDED_SOURCE_INFORMATION too short "
                         "(len=%d)", len(payload))
            return
        info_type = payload[0]
        # Strip trailing NULs / padding.
        text_bytes = bytes(payload[14:])
        text = text_bytes.split(b"\x00", 1)[0].decode("latin-1", "replace").strip()
        if not text:
            return

        source_byte = self._media.get("source_byte")
        _id, kind = _SOURCE_KIND.get(source_byte or 0, (None, "audio"))
        table = _EXT_INFO_RADIO if kind == "radio" else _EXT_INFO_AUDIO
        field = table.get(info_type)
        if not field:
            logger.debug("ML link: EXTENDED_SOURCE_INFORMATION unknown "
                         "info_type=0x%02X (kind=%s) text=%r",
                         info_type, kind, text)
            return

        if self._media.get(field) == text:
            return
        logger.info("ML link EXTENDED_SOURCE_INFO: %s = %r", field, text)
        self._media[field] = text
        self._maybe_push(f"ext_info_{field}")

    def _handle_display_source(self, payload):
        """Decode DISPLAY_SOURCE (0x06) — the human-readable source label.

        Layout matches our provider's emit and Sean's BC2-tested decoder:
        a 5-byte prefix (subtype + flags), then the 12-char display text.
        ml-debug mentions "subtype 3, name at byte 10 of the payload" but
        that offset isn't what real B&O hardware uses for the format we
        see on the bus — defer to Sean's tested offset (payload[5:])."""
        if len(payload) < 6:
            return
        text = bytes(payload[5:]).split(b"\x00", 1)[0].decode(
            "latin-1", "replace").strip()
        if text and not self._media.get("_source_id"):
            self._media["_source_id"] = text.lower().replace(" ", ".")
            logger.info("ML link DISPLAY_SOURCE: %r (source byte unknown)", text)
            self._maybe_push("display_source")

    # ── push to router ───────────────────────────────────────────────

    def _maybe_push(self, reason):
        """Push the assembled now-playing dict to /router/media if it
        has actually changed, deduping at the field level so we don't
        repeat-spam the WS broadcast."""
        snapshot = (
            self._media.get("_source_id"),
            self._media.get("title"),
            self._media.get("artist"),
            self._media.get("album"),
            self._media.get("state"),
            self._media.get("track"),
        )
        if snapshot == self._last_pushed:
            return
        self._last_pushed = snapshot

        pc2 = self.pc2
        if not pc2.session or not pc2.loop:
            return
        payload = {
            "title": self._media.get("title", ""),
            "artist": self._media.get("artist", ""),
            "album": self._media.get("album", ""),
            "state": self._media.get("state") or "playing",
            "_source_id": self._media.get("_source_id") or "ml",
            "_origin": "masterlink_link",
        }
        if self._media.get("track"):
            payload["track"] = self._media["track"]
        if self._media.get("genre"):
            payload["genre"] = self._media["genre"]
        if self._media.get("country"):
            payload["country"] = self._media["country"]

        asyncio.run_coroutine_threadsafe(
            self._post_router_media(payload, reason), pc2.loop)

    async def _post_router_media(self, payload, reason):
        try:
            async with self.pc2.session.post(
                ROUTER_MEDIA, json=payload,
                timeout=aiohttp.ClientTimeout(total=1.5),
            ) as resp:
                if resp.status != 200:
                    logger.warning("Router /media returned HTTP %d "
                                   "(link push: %s)", resp.status, reason)
        except Exception as e:
            logger.warning("Router /media unreachable (link push %s): %s",
                           reason, e)

    # ── outbound GOTO_SOURCE ─────────────────────────────────────────

    def request_source(self, source_byte):
        """Send a GOTO_SOURCE 0x45 to the discovered master.

        Raises RuntimeError if the master hasn't been discovered yet —
        callers should report this back to the user (the LINK menu can
        grey-out source picks until discovery completes).

        Payload shape: `[dest_selector, source_byte]`, pver=1.
        - payload[0] = destination selector — 0x01 = "Audio Source"
          (per ml-debug ml_destselectordict).  Other values: 0x00 video,
          0x0F all products, 0x05 V.TAPE/V.MEM.
        - payload[1] = source byte from `_ML_SOURCES`.

        Format mirrors what our own master's `_handle_goto_source` reads
        (payload[1] = source) — so a BS5c-link → BS5c-master handshake
        round-trips cleanly.  Master's reply is observed via the normal
        STATUS_INFO inbound path."""
        if self.master_node_id is None:
            raise RuntimeError("master not yet discovered — no GOTO_SOURCE "
                               "destination known")
        DEST_SELECTOR_AUDIO = 0x01
        self.pc2.send_ml_telegram(
            dest_node=self.master_node_id,
            src_node=self.pc2.OUR_NODE_ID,
            telegram_type=0x0B,        # REQUEST
            payload_type=0x45,         # GOTO_SOURCE
            payload_version=1,
            payload=[DEST_SELECTOR_AUDIO, source_byte & 0xFF],
        )
        logger.info("ML link role: GOTO_SOURCE 0x%02X -> master at 0x%02X",
                    source_byte, self.master_node_id)
