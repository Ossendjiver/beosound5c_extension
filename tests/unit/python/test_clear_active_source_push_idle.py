"""Regression tests for ``SourceRegistry.clear_active_source(push_idle=...)``
and the ``push_idle`` field on ``POST /router/playback_override``.

Background: the Sonos player's eager-broadcast-on-external-start path
calls the override first, then immediately posts fresh media. Before
this fix, ``clear_active_source`` always broadcast an idle
media_update, which landed after the real media had been cached and
wiped ``MediaState._state`` back to None — leaving the UI on the
playing view with empty mediaInfo.

These tests lock the contract so the idle-push can be suppressed on
demand, and the HTTP handler plumbs the flag end-to-end.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import router as router_module
from lib.source_registry import SourceRegistry


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _FakeRequest:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


class _FakeRouter:
    """Minimal router stand-in that records media broadcasts."""

    def __init__(self):
        self.media = MagicMock()
        self.media.broadcast = AsyncMock()
        self.media.push_idle = AsyncMock()


class TestClearActiveSource:
    def test_push_idle_true_default(self, tmp_path, monkeypatch):
        reg = SourceRegistry()
        reg._active_id = "spotify"
        reg._persist_active = lambda: None  # disable persistence I/O
        fake = _FakeRouter()
        _run(reg.clear_active_source(fake))
        assert fake.media.push_idle.await_count == 1
        assert reg._active_id is None

    def test_push_idle_false_suppresses_idle(self, tmp_path):
        """With push_idle=False the registry still clears the source
        and broadcasts source_change, but does NOT push idle media.
        This is what keeps MediaState._state intact so the player's
        eager broadcast stays visible to the UI."""
        reg = SourceRegistry()
        reg._active_id = "spotify"
        reg._persist_active = lambda: None
        fake = _FakeRouter()
        _run(reg.clear_active_source(fake, push_idle=False))
        assert fake.media.push_idle.await_count == 0
        # source_change was still broadcast — the UI needs to know
        # the active source has changed even if media stays intact.
        assert fake.media.broadcast.await_count == 1
        args, _ = fake.media.broadcast.call_args
        assert args[0] == "source_change"
        assert reg._active_id is None

    def test_no_active_source_noop(self):
        reg = SourceRegistry()
        reg._active_id = None
        fake = _FakeRouter()
        result = _run(reg.clear_active_source(fake, push_idle=False))
        assert result is False
        assert fake.media.push_idle.await_count == 0
        assert fake.media.broadcast.await_count == 0


class TestPlaybackOverrideHandler:
    """POST /router/playback_override must forward push_idle to registry."""

    def test_push_idle_false_forwarded(self, monkeypatch):
        from router import handle_playback_override

        fake = MagicMock()
        fake.registry = MagicMock()
        fake.registry.active_source = MagicMock(
            id="spotify", manages_queue=False)
        fake.registry.clear_active_source = AsyncMock(return_value=True)
        fake._latest_action_ts = 0.0
        monkeypatch.setattr(router_module, "router_instance", fake)

        resp = _run(handle_playback_override(_FakeRequest({
            "force": True, "action_ts": 100.0, "push_idle": False,
        })))
        body = json.loads(resp.text)
        assert body["cleared"] is True
        _, kwargs = fake.registry.clear_active_source.call_args
        assert kwargs.get("push_idle") is False

    def test_push_idle_defaults_to_true(self, monkeypatch):
        """Backwards-compat: clients that don't pass push_idle must
        still get the original (idle-pushing) behavior."""
        from router import handle_playback_override

        fake = MagicMock()
        fake.registry = MagicMock()
        fake.registry.active_source = MagicMock(
            id="spotify", manages_queue=False)
        fake.registry.clear_active_source = AsyncMock(return_value=True)
        fake._latest_action_ts = 0.0
        monkeypatch.setattr(router_module, "router_instance", fake)

        _run(handle_playback_override(_FakeRequest({
            "force": True, "action_ts": 100.0,
        })))
        _, kwargs = fake.registry.clear_active_source.call_args
        assert kwargs.get("push_idle") is True

    def test_manages_queue_not_cleared(self, monkeypatch):
        """If the active source manages its own playback, override
        is rejected regardless of push_idle — that path is untouched
        by this fix."""
        from router import handle_playback_override

        fake = MagicMock()
        fake.registry = MagicMock()
        fake.registry.active_source = MagicMock(
            id="plex", manages_queue=True)
        fake.registry.clear_active_source = AsyncMock()
        fake._latest_action_ts = 0.0
        monkeypatch.setattr(router_module, "router_instance", fake)

        resp = _run(handle_playback_override(_FakeRequest({
            "force": True, "push_idle": False,
        })))
        body = json.loads(resp.text)
        assert body["cleared"] is False
        assert fake.registry.clear_active_source.await_count == 0
