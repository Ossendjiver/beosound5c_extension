"""Focused tests for lib.source_base HTTP command dispatch.

The _handle_command_route method is the single entry point for every
source service's command handling — button presses from the router,
direct commands from the UI, activation events, and raw action
forwarding.  It has ~6 branches that interact with action_ts, the
action_map, correlation IDs, and exception handling.  test_queue.py
touches it indirectly but doesn't pin the branch logic.

Historical bugs it's been the site of:
  * aac5b60 — concurrent overwrite of _action_ts during intra-source play
  * df5605e — authority chain in action_ts system
  * 4b34d3c — stale action_ts blocking user-initiated playback
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from lib.source_base import SourceBase, _action_ts_ctx


class TestResyncMediaCanvas:
    """_resync_media re-posts media after a resync request. It fetches
    fresh title/artist/album from the player but must NOT pair that
    fresh metadata with a stale canvas_url from the previous track.

    Regression: Kitchen showed "Nothing Compares 2 U" with a canvas
    video from an entirely different earlier track. Root cause was
    _resync_media blindly reusing _last_media["canvas_url"] even
    when the fresh media was a different song (external Sonos skip
    advanced the track, spotify source didn't see it, its _last_media
    still held the previous track's canvas). Match is by Spotify
    track id so that URI normalization differences between player
    services and sources don't cause false mismatches.
    """

    def _make_source(self, fresh_media, last_media):
        src = _FakeSource()
        src._registered_state = "playing"
        src._last_media = last_media
        src._player_get = AsyncMock(return_value=fresh_media)
        src.post_media_update = AsyncMock()
        return src

    def test_same_track_id_reuses_cached_canvas(self):
        src = self._make_source(
            fresh_media={
                "title": "Fields Of Gold", "artist": "Sting",
                "album": "Ten Summoner's Tales", "artwork": "a://x.jpg",
                "uri": "spotify:track:3JOVTQ5h8HGFnDdp4VT3MP",
            },
            last_media={
                "title": "Fields Of Gold", "artist": "Sting",
                "track_uri": "spotify:track:3JOVTQ5h8HGFnDdp4VT3MP",
                "canvas_url": "https://canvaz.scdn.co/upload/.../goldmatch",
            },
        )
        _run(src._resync_media())

        kwargs = src.post_media_update.await_args.kwargs
        assert kwargs["canvas_url"] == "https://canvaz.scdn.co/upload/.../goldmatch"
        assert kwargs["title"] == "Fields Of Gold"

    def test_different_track_id_clears_stale_canvas(self):
        """External track advance: _last_media still holds the previous
        track's canvas, but the player now has a different track id.
        Must NOT carry the old canvas into the new track's broadcast."""
        src = self._make_source(
            fresh_media={
                "title": "Nothing Compares 2 U",
                "artist": "Sinéad O'Connor",
                "album": "I Do Not Want What I Haven't Got",
                "artwork": "a://new.jpg",
                "uri": "spotify:track:5GHY1DFWKz3Prg2V0Iodqo",
            },
            last_media={
                "title": "Kiss from a Rose", "artist": "Seal",
                "track_uri": "spotify:track:3YKptz29AsOlm7WAVnztBh",
                "canvas_url": "https://canvaz.scdn.co/upload/.../roseSTALE",
            },
        )
        _run(src._resync_media())

        kwargs = src.post_media_update.await_args.kwargs
        assert kwargs["title"] == "Nothing Compares 2 U"
        assert kwargs["canvas_url"] == "", (
            f"stale canvas from previous track leaked into resync: "
            f"{kwargs['canvas_url']!r}")

    def test_track_id_match_across_sonos_wrapped_uri(self):
        """The player service may report the track uri in a
        Sonos-wrapped form (x-sonos-spotify:spotify%3atrack%3a<id>)
        while the source stored the canonical spotify:track:<id>.
        extract_spotify_track_id normalizes both to the same 22-char
        id, so the cached canvas must still match."""
        track_id = "5GHY1DFWKz3Prg2V0Iodqo"
        src = self._make_source(
            fresh_media={
                "title": "Nothing Compares 2 U",
                "artist": "Sinéad O'Connor",
                "album": "I Do Not Want What I Haven't Got",
                "artwork": "a://art.jpg",
                "uri": f"x-sonos-spotify:spotify%3atrack%3a{track_id}?sid=9",
            },
            last_media={
                "title": "Nothing Compares 2 U",
                "artist": "Sinéad O'Connor",
                "track_uri": f"spotify:track:{track_id}",
                "canvas_url": "https://canvaz.scdn.co/upload/.../matching",
            },
        )
        _run(src._resync_media())

        kwargs = src.post_media_update.await_args.kwargs
        assert kwargs["canvas_url"] == "https://canvaz.scdn.co/upload/.../matching"

    def test_no_cached_track_uri_clears_canvas(self):
        """Defensive: if _last_media has a canvas but no track_uri
        (pre-fix cache), don't blindly reuse the canvas — we can't
        prove it matches."""
        src = self._make_source(
            fresh_media={
                "title": "Crazy", "artist": "Gnarls Barkley",
                "album": "St. Elsewhere", "artwork": "a://gnarls.jpg",
                "uri": "spotify:track:7gHs73wELdeycvS48JfIos",
            },
            last_media={
                "title": "Crazy", "artist": "Gnarls Barkley",
                "canvas_url": "https://canvaz.scdn.co/upload/.../unknown",
            },
        )
        _run(src._resync_media())

        kwargs = src.post_media_update.await_args.kwargs
        assert kwargs["canvas_url"] == ""


class _FakeSource(SourceBase):
    id = "fake"
    name = "Fake"
    port = 9999
    action_map = {"play": "toggle", "next": "next_track", "stop": "stop_all"}

    def __init__(self):
        super().__init__()
        self._commands: list = []
        self._raw_overrides: dict = {}
        self._activate_result: dict | None = None
        self._activate_called = False
        self.register = AsyncMock()
        self.post_media_update = AsyncMock()

    async def handle_command(self, cmd: str, data: dict) -> dict:
        self._commands.append((cmd, data))
        return {"handled": cmd}

    async def handle_raw_action(self, action, data):
        return self._raw_overrides.get(action)

    async def handle_activate(self, data: dict):
        self._activate_called = True
        return self._activate_result

    async def activate_playback(self):
        pass


class _FakeRequest:
    """Minimal duck-typed aiohttp Request."""
    def __init__(self, body: dict, headers: dict | None = None):
        self._body = body
        self.headers = headers or {}

    async def json(self):
        return self._body


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _decode_json_response(resp) -> dict:
    """Extract the JSON body from an aiohttp Response for assertions."""
    return json.loads(resp.text)


# ── action_map lookup ────────────────────────────────────────────────


class TestActionMapDispatch:
    def test_known_action_dispatched_via_action_map(self):
        src = _FakeSource()
        resp = _run(src._handle_command_route(_FakeRequest({
            "action": "play",
        })))
        body = _decode_json_response(resp)
        assert body["status"] == "ok"
        assert body["command"] == "toggle"
        # Subclass saw the mapped command.
        assert src._commands[0][0] == "toggle"

    def test_unknown_action_returns_400(self):
        src = _FakeSource()
        resp = _run(src._handle_command_route(_FakeRequest({
            "action": "bogus",
        })))
        assert resp.status == 400
        body = _decode_json_response(resp)
        assert "Unmapped action" in body["message"]
        # Subclass handler NOT called.
        assert src._commands == []

    def test_raw_action_override_wins(self):
        """handle_raw_action can intercept before action_map lookup."""
        src = _FakeSource()
        src._raw_overrides = {"play": ("custom_cmd", {"extra": True})}
        resp = _run(src._handle_command_route(_FakeRequest({
            "action": "play",
        })))
        body = _decode_json_response(resp)
        assert body["command"] == "custom_cmd"
        assert src._commands[0][0] == "custom_cmd"
        assert src._commands[0][1]["extra"] is True

    def test_direct_command_bypasses_action_map(self):
        """UI sends ``{"command": "..."}`` directly, no ``action`` field."""
        src = _FakeSource()
        resp = _run(src._handle_command_route(_FakeRequest({
            "command": "direct_cmd",
            "payload": "x",
        })))
        body = _decode_json_response(resp)
        assert body["command"] == "direct_cmd"
        assert src._commands[0][1]["payload"] == "x"


# ── action_ts propagation ────────────────────────────────────────────


class TestActionTimestampFlow:
    def test_action_ts_from_router_forwarded_event(self):
        """Router sends ``action_ts`` in the forwarded event — the
        source must adopt it into both the instance field and the
        ContextVar."""
        src = _FakeSource()
        _run(src._handle_command_route(_FakeRequest({
            "action": "play",
            "action_ts": 123.456,
        })))
        assert src._action_ts == 123.456
        # handle_command saw it in data["_action_ts"]
        _, data = src._commands[0]
        assert data["_action_ts"] == 123.456

    def test_direct_ui_command_stamps_fresh(self):
        """UI direct commands have no action_ts of their own — the
        source stamps a fresh monotonic timestamp so downstream sees
        the latest watermark."""
        import time
        src = _FakeSource()
        before = time.monotonic()
        _run(src._handle_command_route(_FakeRequest({
            "command": "direct",
        })))
        after = time.monotonic()
        assert before <= src._action_ts <= after
        _, data = src._commands[0]
        assert data["_action_ts"] == src._action_ts

    def test_missing_action_ts_falls_back_to_monotonic(self):
        """If a router event arrives without action_ts (older router
        or a hand-built curl request), we stamp one."""
        src = _FakeSource()
        _run(src._handle_command_route(_FakeRequest({
            "action": "play",  # no action_ts
        })))
        assert src._action_ts > 0

    def test_context_var_set_inside_handler(self):
        """_action_ts_ctx is set before handle_command runs so the
        subclass can use _action_ts_ctx.get() in anything it spawns."""
        src = _FakeSource()
        seen = []

        async def _capture(cmd, data):
            seen.append(_action_ts_ctx.get())
            return {}

        src.handle_command = _capture
        _run(src._handle_command_route(_FakeRequest({
            "action": "play", "action_ts": 42.0,
        })))
        assert seen[0] == 42.0


# ── Activation branch ────────────────────────────────────────────────


class TestActivation:
    def test_activate_calls_handle_activate(self):
        src = _FakeSource()
        _run(src._handle_command_route(_FakeRequest({
            "action": "activate", "action_ts": 10.0,
        })))
        assert src._activate_called

    def test_activate_response_has_activate_command(self):
        src = _FakeSource()
        resp = _run(src._handle_command_route(_FakeRequest({
            "action": "activate", "action_ts": 10.0,
        })))
        body = _decode_json_response(resp)
        assert body["command"] == "activate"
        assert body["status"] == "ok"

    def test_activate_merges_handle_activate_result_into_response(self):
        """If handle_activate returns a dict, its fields are merged
        into the response body (before the default ok/activate)."""
        src = _FakeSource()
        src._activate_result = {"extra": "value"}
        resp = _run(src._handle_command_route(_FakeRequest({
            "action": "activate", "action_ts": 10.0,
        })))
        body = _decode_json_response(resp)
        assert body["extra"] == "value"
        assert body["command"] == "activate"

    def test_activate_does_not_fall_through_to_action_map(self):
        """Activation must return early — action_map dispatch must NOT
        run for an 'activate' action (the subclass's handle_activate is
        authoritative)."""
        src = _FakeSource()
        _run(src._handle_command_route(_FakeRequest({
            "action": "activate", "action_ts": 10.0,
        })))
        # handle_command never called for activate path
        assert src._commands == []


# ── Exception path ───────────────────────────────────────────────────


class TestExceptionPath:
    def test_exception_in_handler_returns_500(self):
        src = _FakeSource()

        async def _boom(cmd, data):
            raise RuntimeError("kaboom")
        src.handle_command = _boom

        resp = _run(src._handle_command_route(_FakeRequest({
            "action": "play",
        })))
        assert resp.status == 500
        body = _decode_json_response(resp)
        assert "kaboom" in body["message"]

    def test_exception_in_handle_activate_returns_500(self):
        src = _FakeSource()

        async def _boom(data):
            raise RuntimeError("activate failed")
        src.handle_activate = _boom

        resp = _run(src._handle_command_route(_FakeRequest({
            "action": "activate", "action_ts": 10.0,
        })))
        assert resp.status == 500
        body = _decode_json_response(resp)
        assert "activate failed" in body["message"]


# ── Correlation ID propagation ──────────────────────────────────────


class TestCorrelationPropagation:
    def test_cid_header_adopted_inside_handler(self):
        """Router sends X-Correlation-ID — the source adopts it so log
        lines emitted inside handle_command share the ID.

        ContextVars are scoped per-task, so we can't read the value
        after the handler returns from a different context — instead
        we capture it *inside* the handle_command subclass hook.
        """
        from lib.correlation import get_id
        src = _FakeSource()
        observed: list[str] = []

        async def _capture(cmd, data):
            observed.append(get_id())
            return {}
        src.handle_command = _capture

        _run(src._handle_command_route(_FakeRequest(
            {"action": "play"},
            headers={"X-Correlation-ID": "routerID"},
        )))
        assert observed == ["routerID"]

    def test_missing_cid_header_does_not_overwrite(self):
        """Without the header, the handler must not reset the context
        var to some random value — it just leaves the current default
        (``-``) in place."""
        from lib.correlation import get_id
        src = _FakeSource()
        observed: list[str] = []

        async def _capture(cmd, data):
            observed.append(get_id())
            return {}
        src.handle_command = _capture

        _run(src._handle_command_route(_FakeRequest({"action": "play"})))
        # Whatever the default is, it must be stable (not "routerID").
        assert observed[0] != "routerID"
