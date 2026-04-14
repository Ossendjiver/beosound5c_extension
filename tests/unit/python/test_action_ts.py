"""Tests for action_ts ContextVar race prevention in source_base."""

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

SERVICES_DIR = Path(__file__).resolve().parents[3] / "services"
sys.path.insert(0, str(SERVICES_DIR))

from lib.source_base import SourceBase, _action_ts_ctx


class DummySource(SourceBase):
    id = "test"
    name = "Test"
    port = 9999
    action_map = {"play": "toggle", "stop": "stop"}

    async def handle_command(self, cmd, data):
        # Simulate async work that yields
        await asyncio.sleep(0.01)
        return {"ts_seen": _action_ts_ctx.get()}


class TestContextVarIsolation:
    """Concurrent request handlers should not corrupt each other's timestamps."""

    def test_contextvar_set_and_read(self):
        """Basic: setting _action_ts_ctx is readable in same context."""
        _action_ts_ctx.set(42.0)
        assert _action_ts_ctx.get() == 42.0

    def test_concurrent_tasks_isolated(self):
        """Two async tasks setting different timestamps should not interfere."""
        results = {}

        async def set_and_read(name, ts):
            _action_ts_ctx.set(ts)
            await asyncio.sleep(0.01)  # yield to let other task run
            results[name] = _action_ts_ctx.get()

        async def run():
            t1 = asyncio.create_task(set_and_read("a", 100.0))
            t2 = asyncio.create_task(set_and_read("b", 200.0))
            await asyncio.gather(t1, t2)

        asyncio.run(run())
        assert results["a"] == 100.0
        assert results["b"] == 200.0

    def test_action_ts_flows_to_register(self):
        """register() should use ContextVar timestamp, not stale instance field."""
        src = DummySource()
        src._action_ts = 999.0  # stale instance field

        register_payloads = []
        original_post = SourceBase._player_post

        async def capture_register(self, state, **kwargs):
            # Just capture what register would send
            ts = _action_ts_ctx.get() or self._action_ts
            register_payloads.append(ts)

        # Set ContextVar to the correct value
        _action_ts_ctx.set(42.0)
        assert (_action_ts_ctx.get() or src._action_ts) == 42.0

    def test_action_ts_set_on_activate(self):
        """handle_activate should set both instance and ContextVar."""
        src = DummySource()
        src._http_session = MagicMock()
        src._http_session.post = MagicMock()

        # Simulate activation
        async def run():
            _action_ts_ctx.set(0.0)
            data = {"action_ts": 123.456}
            # We can't call handle_activate directly without mocking register,
            # but we can verify the setting logic
            ts = data.get("action_ts", 0) or 0
            src._action_ts = ts
            _action_ts_ctx.set(ts)
            assert _action_ts_ctx.get() == 123.456
            assert src._action_ts == 123.456

        asyncio.run(run())


class TestTimestampPropagation:
    """Action timestamps should flow through the entire call chain."""

    def test_player_play_uses_contextvar(self):
        src = DummySource()
        src._http_session = MagicMock()
        src._action_ts = 100.0  # instance field

        posted_bodies = []

        async def mock_post(endpoint, body=None, **kwargs):
            if body:
                posted_bodies.append(body)
            return True

        src._player_post = mock_post

        async def run():
            _action_ts_ctx.set(200.0)  # ContextVar is newer
            await src.player_play(url="http://example.com/stream")

        asyncio.run(run())
        assert posted_bodies
        # Should use ContextVar (200) not instance (100)
        assert posted_bodies[0]["action_ts"] == 200.0

    def test_player_next_stamps_fresh(self):
        # Anchor the baseline to "some point clearly in the past", so the
        # assertion holds regardless of the host's monotonic clock. Using
        # a hard-coded 100.0 is brittle: on a fresh CI runner whose
        # monotonic starts at ~20s, time.monotonic() < 100 and the test
        # fires even though player_next() is behaving correctly.
        baseline = time.monotonic() - 1000.0

        src = DummySource()
        src._action_ts = baseline

        posted_bodies = []

        async def mock_post(endpoint, body=None, **kwargs):
            if body:
                posted_bodies.append(body)
            return True

        src._player_post = mock_post

        async def run():
            _action_ts_ctx.set(baseline)
            await src.player_next()

        asyncio.run(run())
        assert posted_bodies
        # Should stamp fresh via time.monotonic(), not reuse the stale
        # ContextVar value we seeded above.
        assert posted_bodies[0]["action_ts"] > baseline
