"""Tests for router background task tracking."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

SERVICES_DIR = Path(__file__).resolve().parents[3] / "services"
sys.path.insert(0, str(SERVICES_DIR))


def make_router():
    """Create an EventRouter with mocked dependencies (no I/O).

    Patches cfg to return sensible defaults so the constructor doesn't fail
    on missing config, and patches the module-level router_instance creation.
    """
    def fake_cfg(*keys, default=None):
        # Return enough to satisfy __init__
        return default

    with patch("lib.config.cfg", side_effect=fake_cfg), \
         patch("lib.transport.Transport"), \
         patch("lib.volume_adapters.create_volume_adapter"), \
         patch("lib.volume_adapters.infer_volume_type", return_value="sonos"), \
         patch("lib.lydbro.LydbroHandler"):
        # Force re-import to pick up patches
        import importlib
        if "router" in sys.modules:
            import router as router_mod
            # Patch module-level instance creation
            with patch.object(router_mod, "router_instance", MagicMock()):
                router = router_mod.EventRouter()
        else:
            import router as router_mod
            router = router_mod.EventRouter()
    return router


class TestSpawn:
    def test_spawn_adds_to_tracking_set(self):
        router = make_router()

        async def run():
            task = router._spawn(asyncio.sleep(999), name="test")
            assert task in router._background_tasks
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(run())

    def test_completed_task_auto_removed(self):
        router = make_router()

        async def run():
            task = router._spawn(asyncio.sleep(0), name="quick")
            await task
            # done callback removes it from the set
            assert task not in router._background_tasks

        asyncio.run(run())

    def test_spawn_names_task(self):
        router = make_router()

        async def run():
            task = router._spawn(asyncio.sleep(999), name="canvas_inject")
            assert task.get_name() == "canvas_inject"
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(run())

    def test_multiple_tasks_tracked(self):
        router = make_router()

        async def run():
            t1 = router._spawn(asyncio.sleep(999), name="a")
            t2 = router._spawn(asyncio.sleep(999), name="b")
            t3 = router._spawn(asyncio.sleep(999), name="c")
            assert len(router._background_tasks) == 3
            for t in (t1, t2, t3):
                t.cancel()
            await asyncio.gather(t1, t2, t3, return_exceptions=True)

        asyncio.run(run())


class TestStopCancellation:
    def test_stop_cancels_all_tracked_tasks(self):
        router = make_router()

        cancelled = []

        async def tracked_coro(name):
            try:
                await asyncio.sleep(999)
            except asyncio.CancelledError:
                cancelled.append(name)
                raise

        async def run():
            router._spawn(tracked_coro("a"), name="a")
            router._spawn(tracked_coro("b"), name="b")
            router._spawn(tracked_coro("c"), name="c")
            # Let tasks start running before we stop
            await asyncio.sleep(0)
            # Mock out the parts of stop() that need I/O
            router.transport = MagicMock()
            router.transport.stop = AsyncMock()
            router.media = MagicMock()
            router.media.close_all = AsyncMock()
            router._session = MagicMock()
            router._session.close = AsyncMock()
            await router.stop()

        asyncio.run(run())
        assert sorted(cancelled) == ["a", "b", "c"]

    def test_stop_with_no_tasks_is_clean(self):
        router = make_router()

        async def run():
            router.transport = MagicMock()
            router.transport.stop = AsyncMock()
            router.media = MagicMock()
            router.media.close_all = AsyncMock()
            router._session = MagicMock()
            router._session.close = AsyncMock()
            await router.stop()  # should not raise

        asyncio.run(run())
