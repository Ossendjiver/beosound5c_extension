"""Tests for LoopMonitor: does it actually catch blocking sync calls?

These tests double as regression coverage for the "blocking call in
async handler" bug class (commits 27cb774, ddd02ea, 39c1169).  The
monitor is the production-side detector; the tests prove it detects
obvious stalls while staying quiet under healthy async work.
"""

import asyncio
import logging
import time

import pytest

from lib.loop_monitor import LoopMonitor


@pytest.mark.asyncio
async def test_clean_async_workload_has_no_stall():
    """A clean async workload should register zero stalls and a tiny max lag."""
    async with LoopMonitor(interval_ms=20, warn_ms=100) as mon:
        for _ in range(10):
            await asyncio.sleep(0.01)
    assert mon.stalls == 0
    assert mon.max_lag_ms < 50  # generous for CI jitter


@pytest.mark.asyncio
async def test_detects_time_sleep_blocking_call(caplog):
    """``time.sleep`` inside an async context blocks the loop — the
    monitor must detect it and log a warning."""
    with caplog.at_level(logging.WARNING, logger="beo.loop"):
        async with LoopMonitor(interval_ms=20, warn_ms=100) as mon:
            await asyncio.sleep(0.05)   # give monitor a baseline sample
            time.sleep(0.25)            # <<< the blocking call
            await asyncio.sleep(0.05)   # let monitor run another sample
    assert mon.stalls >= 1, (
        f"monitor failed to detect 250ms time.sleep "
        f"(samples={mon.samples}, max_lag_ms={mon.max_lag_ms:.0f})"
    )
    assert mon.max_lag_ms >= 150  # clearly above the 100ms threshold
    assert any(
        "event loop stalled" in r.message for r in caplog.records
        if r.name == "beo.loop"
    )


@pytest.mark.asyncio
async def test_detects_sync_subprocess_style_block():
    """A 200ms synchronous sleep (our proxy for ``subprocess.run``)
    shows up as a stall."""
    async with LoopMonitor(interval_ms=20, warn_ms=50) as mon:
        await asyncio.sleep(0.05)
        time.sleep(0.2)
        await asyncio.sleep(0.05)
    assert mon.stalls >= 1


@pytest.mark.asyncio
async def test_monitor_stops_cleanly():
    mon = LoopMonitor(interval_ms=10, warn_ms=1000).start()
    await asyncio.sleep(0.05)
    await mon.stop()
    # stop() is idempotent
    await mon.stop()
    assert mon._task is None


@pytest.mark.asyncio
async def test_multiple_monitors_coexist():
    """Two monitors in the same loop must each produce their own samples."""
    async with LoopMonitor(interval_ms=15, warn_ms=1000) as m1:
        async with LoopMonitor(interval_ms=15, warn_ms=1000) as m2:
            await asyncio.sleep(0.1)
    assert m1.samples > 0
    assert m2.samples > 0
