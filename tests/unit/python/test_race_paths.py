"""Race-path tests for code that has historically been bug-prone.

Each test here reproduces a concurrency scenario that previously caused —
or could plausibly cause — a user-visible regression.  The unit tests in
sibling files cover the happy path and explicit state machine rules; this
file covers the nasty interleavings.
"""

import asyncio
import json
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

SERVICES_DIR = Path(__file__).resolve().parents[3] / "services"
sys.path.insert(0, str(SERVICES_DIR))

from lib.background_tasks import BackgroundTaskSet
from lib.media_state import MediaState
from lib.source_registry import SourceRegistry


def _router_mock():
    r = MagicMock()
    r.media = MagicMock()
    r.media.broadcast = AsyncMock()
    r.media.push_idle = AsyncMock()
    r._latest_action_ts = 0.0
    r._forward_to_source = AsyncMock()
    r._wake_screen = AsyncMock()
    r._get_config_title = MagicMock(return_value=None)
    r._get_after = MagicMock(return_value=None)
    r._volume = None
    return r


# ── Media state ──

class TestMediaRaces:

    def test_media_from_deactivated_source_dropped(self):
        """After a source switch, pending media from the old source must drop.

        Flow: CD is active -> Spotify becomes active -> a late /media POST
        still tagged with _source_id=cd arrives.  It must be rejected as
        inactive_source rather than clobbering Spotify's metadata.
        """
        ms = MediaState()
        ms.state = {"title": "Spotify Song", "artist": "Spotify Artist"}

        stale = {"title": "CD Track", "artist": "CD Artist",
                 "_reason": "update", "_source_id": "cd", "_action_ts": 100}
        result = ms.validate_update(stale, active_source_id="spotify",
                                    latest_action_ts=200)
        assert result is not None and result["dropped"] is True
        assert result["reason"] == "inactive_source"
        # The cached state for the active source is untouched.
        assert ms.state["title"] == "Spotify Song"

    def test_broadcast_snapshots_client_set(self):
        """Adding a client mid-broadcast must not raise set-size-changed.

        We simulate a client that, while being sent to, causes another
        client to be added to the set from inside its send_str().  If the
        broadcast iterated the live set, Python would raise
        RuntimeError('Set changed size during iteration').
        """
        ms = MediaState()
        added_during_send = []

        class MutatingWS:
            async def send_str(self, msg):
                # Mutate the parent set while being sent to.
                class Quiet:
                    async def send_str(self, _msg):
                        added_during_send.append(1)

                    async def close(self):
                        pass
                ms._ws_clients.add(Quiet())

            async def close(self):
                pass

        ms._ws_clients.add(MutatingWS())
        # Should not raise — broadcast iterates a snapshot.
        asyncio.run(ms.broadcast("x", {"k": 1}))

    def test_close_pending_ws_during_broadcast(self):
        """A client that closes mid-send is dropped, not retried forever."""
        ms = MediaState()

        class ClosingWS:
            async def send_str(self, msg):
                raise ConnectionResetError("client went away")

            async def close(self):
                pass

        class FastWS:
            def __init__(self):
                self.received = 0

            async def send_str(self, msg):
                self.received += 1

            async def close(self):
                pass

        closing = ClosingWS()
        fast = FastWS()
        ms._ws_clients.add(closing)
        ms._ws_clients.add(fast)

        asyncio.run(ms.broadcast("x", {"k": 1}))
        assert closing not in ms._ws_clients
        assert fast in ms._ws_clients
        assert fast.received == 1


# ── Background tasks ──

class TestBackgroundTaskRaces:

    def test_cancel_all_tolerates_already_done_tasks(self):
        """cancel_all() must not raise when some tasks have already finished."""
        bts = BackgroundTaskSet(logging.getLogger("test"), label="t")

        async def run():
            done = bts.spawn(asyncio.sleep(0), name="done_fast")
            running = bts.spawn(asyncio.sleep(10), name="running")
            await done  # let the "done" task finish + be auto-removed
            await bts.cancel_all()
            # Running task cancelled.
            assert running.cancelled() or running.done()

        asyncio.run(run())

    def test_exception_in_spawned_task_logged_not_raised(self):
        """Background exceptions are logged, not propagated into the loop."""
        captured = []

        class CapturingLogger(logging.Logger):
            def warning(self, msg, *args, **kwargs):
                captured.append(msg % args if args else msg)

        bts = BackgroundTaskSet(CapturingLogger("test"), label="t")

        async def boom():
            raise RuntimeError("boom")

        async def run():
            task = bts.spawn(boom(), name="exploding")
            # Await the task's completion via the set's discard callback.
            # It won't propagate — but it also should not leave the task
            # in the set.
            for _ in range(20):
                if task not in bts:
                    break
                await asyncio.sleep(0.01)
            assert task not in bts

        asyncio.run(run())
        assert any("exploding" in msg and "boom" in msg for msg in captured)

    def test_failure_count_and_last_failure_tracked(self):
        """Exposed counters let /status endpoints report background-task health."""
        bts = BackgroundTaskSet(logging.getLogger("test"), label="t")

        async def boom():
            raise ValueError("kaboom")

        async def fine():
            return 42

        async def run():
            t1 = bts.spawn(boom(), name="first_failure")
            t2 = bts.spawn(fine(), name="clean")
            t3 = bts.spawn(boom(), name="second_failure")
            # Wait for all three to drain.
            for _ in range(50):
                if not bts:
                    break
                await asyncio.sleep(0.005)

        asyncio.run(run())
        assert bts.failure_count == 2
        assert bts.last_failure is not None
        last_name, last_repr = bts.last_failure
        assert last_name == "second_failure"
        assert "kaboom" in last_repr

    def test_concurrent_spawn_and_cancel(self):
        """Spawning while cancel_all() is running must not crash.

        cancel_all iterates a list copy, so spawns that arrive after it
        starts just end up in the next generation of the set — they are
        not cancelled, but they're also not lost or double-counted.
        """
        bts = BackgroundTaskSet(logging.getLogger("test"), label="t")

        async def run():
            for _ in range(10):
                bts.spawn(asyncio.sleep(10), name="early")

            async def late_spawn():
                await asyncio.sleep(0)
                bts.spawn(asyncio.sleep(10), name="late")

            await asyncio.gather(bts.cancel_all(), late_spawn())
            # `late` survives; clean it up.
            await bts.cancel_all()

        asyncio.run(run())


# ── Source registry ──

class TestRegistryRaces:

    def test_rapid_source_switch_is_serialised_by_await(self):
        """Two back-to-back activations must land the *later* source active.

        Historically a fire-and-forget stop of the old source allowed the
        new activation to proceed before the old one was really gone,
        which could corrupt `_active_id`.  With the atomic await in place,
        the final active_id matches the last call issued.
        """
        reg = SourceRegistry()
        router = _router_mock()

        async def run():
            await reg.update("cd", "available", router,
                             name="CD", command_url="http://localhost:8769/command")
            await reg.update("spotify", "available", router,
                             name="Spotify", command_url="http://localhost:8771/command")
            await reg.update("radio", "available", router,
                             name="Radio", command_url="http://localhost:8779/command")
            await reg.update("cd", "playing", router, action_ts=100)
            await reg.update("spotify", "playing", router, action_ts=200)
            await reg.update("radio", "playing", router, action_ts=300)

        asyncio.run(run())
        assert reg.active_id == "radio"

    def test_invalid_transition_does_not_mutate_active(self):
        """A rejected transition must not flip active_id as a side effect."""
        reg = SourceRegistry()
        router = _router_mock()

        async def run():
            await reg.update("cd", "available", router,
                             name="CD", command_url="http://localhost:8769/command")
            await reg.update("cd", "playing", router, action_ts=100)
            # Try an invalid self-transition on the existing source
            # (gone→gone is still rejected).
            result = await reg.update("radio", "gone", router,
                                       name="Radio",
                                       command_url="http://localhost:8779/command")
            assert result.get("rejected") == "invalid_transition"
            # CD is still the active source.
            assert reg.active_id == "cd"

        asyncio.run(run())
