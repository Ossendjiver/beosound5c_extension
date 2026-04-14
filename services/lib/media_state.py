"""Media state management for BeoSound 5c router.

Owns the cached media metadata, WebSocket client connections, and all
push/validation logic.  The router's single WebSocket endpoint flows
through this module — it is the sole channel for UI state events.
"""

import asyncio
import json
import logging

from aiohttp import web

logger = logging.getLogger("beo-router")

# Per-client send timeout: a hung or dropped TCP connection must not be
# able to block the broadcast loop (and therefore every other WS client).
_WS_SEND_TIMEOUT = 2.0

_IDLE_MEDIA = {
    "state": "idle", "title": "", "artist": "", "album": "",
    "artwork_url": "", "canvas_url": "",
}


class MediaState:
    """Single source of truth for media metadata and UI WebSocket clients."""

    def __init__(self):
        self._state: dict | None = None
        self._ws_clients: set[web.WebSocketResponse] = set()

    # ── Public state access ──

    @property
    def state(self) -> dict | None:
        return self._state

    @state.setter
    def state(self, value: dict | None):
        self._state = value

    @property
    def client_count(self) -> int:
        return len(self._ws_clients)

    # ── WebSocket broadcast ──

    async def _send_all(self, msg: str) -> None:
        """Send ``msg`` to every WS client with a per-client timeout.

        A hung or slow client is dropped rather than allowed to block the
        broadcast loop.  Iterates a snapshot so concurrent add/discard from
        handle_ws() cannot mutate the set mid-loop.
        """
        if not self._ws_clients:
            return
        dead: set[web.WebSocketResponse] = set()
        for ws in list(self._ws_clients):
            try:
                await asyncio.wait_for(ws.send_str(msg), timeout=_WS_SEND_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning("WS client send timed out — dropping client")
                dead.add(ws)
            except Exception as e:
                logger.debug("WS client send failed: %s — dropping client", e)
                dead.add(ws)
        if dead:
            self._ws_clients -= dead
            # Best-effort close so the underlying socket is released.
            for ws in dead:
                try:
                    await asyncio.wait_for(ws.close(), timeout=1.0)
                except Exception:
                    pass

    async def broadcast(self, event_type: str, data: dict):
        """Push any event to all connected UI WebSocket clients."""
        if not self._ws_clients:
            return
        await self._send_all(json.dumps({"type": event_type, "data": data}))

    async def push_media(self, media_data: dict, reason: str = "update"):
        """Push a media update to all connected clients."""
        if not self._ws_clients:
            return
        await self._send_all(json.dumps(
            {"type": "media_update", "data": media_data, "reason": reason}))

    async def push_idle(self, reason: str = "source_deactivated"):
        """Clear cached state and push idle media to UI."""
        self._state = None
        await self.push_media(_IDLE_MEDIA, reason)

    # ── Trace log ──

    @staticmethod
    def _trace(**fields) -> None:
        """One structured line per media update decision.

        All fields on one line in ``key=value`` form so a single ``grep
        media_trace`` reconstructs the full history of accept/drop
        decisions.  This is the canonical observability hook for the
        "stale media" bug family (c76a0bc, 632a947, aab93cb, 4b34d3c):
        when a user reports wrong/stale artwork, search for the source_id
        and you'll see the exact action_ts ordering that caused it.
        """
        parts = []
        for k, v in fields.items():
            if v is None:
                v = "-"
            elif isinstance(v, str):
                # Quote strings with spaces so ``title=foo bar`` doesn't
                # split into two tokens.
                if " " in v or "=" in v:
                    v = '"' + v.replace('"', '\\"') + '"'
            parts.append(f"{k}={v}")
        logger.info("media_trace %s", " ".join(parts))

    # ── Media validation ──

    def validate_update(self, payload: dict,
                        active_source_id: str | None,
                        latest_action_ts: float) -> dict | None:
        """Validate an incoming media update.

        Extracts and removes internal fields (_reason, _source_id, _action_ts)
        from ``payload`` (mutates it).

        Returns a rejection dict if the update should be dropped, or None
        if accepted.  On acceptance the caller should store and push the
        payload.
        """
        reason = payload.pop("_reason", "update")
        source_id = payload.pop("_source_id", None)
        action_ts = payload.pop("_action_ts", 0)
        is_active = source_id and source_id == active_source_id
        title = payload.get("title", "")[:40] or "-"

        # Rejection policy:
        #   - Media tagged with _source_id must come from the active source;
        #     anything else is dropped as inactive_source.
        #   - Player-originated media (_source_id is None) is always accepted;
        #     the player owns metadata once external playback is running.
        #   - Stale-timestamp rejection is intentionally NOT applied here.
        #     A previous branch combined `source_id and action_ts < latest_ts
        #     and not is_active`, but that conjunction is unreachable — the
        #     inactive-source check above has already returned for every
        #     `not is_active` case.  Stale-ordering prevention lives upstream
        #     in SourceRegistry.update() (which compares ts on source
        #     activation, not on media push).
        if source_id and not is_active:
            self._trace(
                decision="drop",
                drop_reason="inactive_source",
                source_id=source_id,
                active=active_source_id,
                action_ts=action_ts,
                latest_ts=latest_action_ts,
                update_reason=reason,
                title=title,
            )
            return {"status": "ok", "dropped": True, "reason": "inactive_source"}

        self._trace(
            decision="accept",
            drop_reason=None,
            source_id=source_id,
            active=active_source_id,
            action_ts=action_ts,
            latest_ts=latest_action_ts,
            update_reason=reason,
            title=title,
        )

        # Ensure canvas_url always present to clear stale canvas
        payload.setdefault("canvas_url", "")

        # Preserve context for caller (canvas injection, TTS)
        payload["_validated_source_id"] = source_id
        payload["_validated_reason"] = reason

        return None  # accepted

    async def accept_and_push(self, payload: dict, reason: str = "update"):
        """Store validated media and push to clients."""
        self._state = payload
        await self.push_media(payload, reason)

    # ── WebSocket endpoint ──

    async def handle_ws(self, request: web.Request,
                        get_source_snapshot,
                        get_volume) -> web.WebSocketResponse:
        """GET /router/ws — unified WebSocket for all UI state events.

        ``get_source_snapshot`` returns (active_source, volume) for replay.
        """
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        self._ws_clients.add(ws)
        logger.info("WS client connected (%d total)", len(self._ws_clients))
        try:
            # Replay current state to new client
            active = get_source_snapshot()
            if active:
                await ws.send_str(json.dumps({
                    "type": "source_change",
                    "data": {
                        "active_source": active.id,
                        "source_name": active.name,
                        "player": active.player,
                    },
                }))
            await ws.send_str(json.dumps({
                "type": "volume_update",
                "data": {"volume": round(get_volume())},
            }))
            if self._state:
                await ws.send_str(json.dumps({
                    "type": "media_update",
                    "data": self._state,
                    "reason": "client_connect",
                }))
            # Push-only — keep alive until client disconnects
            async for _msg in ws:
                pass
        finally:
            self._ws_clients.discard(ws)
            logger.info("WS client disconnected (%d remaining)",
                         len(self._ws_clients))
        return ws

    async def close_all(self):
        """Close all WebSocket clients (shutdown)."""
        for ws in list(self._ws_clients):
            try:
                await asyncio.wait_for(ws.close(), timeout=1.0)
            except Exception as e:
                logger.debug("Error closing WS during shutdown: %s", e)
        self._ws_clients.clear()
