"""Tests for players/bluesound.py startup guards.

Pins the missing-config exit convention: on_start() with no player.ip
must exit with code 0, not 1, AND notify systemd READY=1 first.  The
systemd unit is Type=notify with Restart=on-failure — an exit code of 1
would crash-loop the service forever, and even exit 0 without READY=1
crash-loops it (systemd records Result=protocol when a Type=notify unit
exits before signalling readiness).  Sending READY=1 then exiting 0
keeps the misconfigured service cleanly stopped (same convention as the
player-type guard in PlayerBase.start()).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

SERVICES_DIR = Path(__file__).resolve().parents[3] / "services"
sys.path.insert(0, str(SERVICES_DIR))

import players.bluesound as bluesound_module
from players.bluesound import BluesoundPlayer


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class TestMissingIpGuard:
    def test_on_start_without_ip_exits_zero(self, monkeypatch):
        import lib.watchdog as watchdog_module

        notifications = []
        monkeypatch.setattr(watchdog_module, "sd_notify", notifications.append)
        monkeypatch.setattr(bluesound_module, "BLUESOUND_IP", "")
        player = BluesoundPlayer()
        with pytest.raises(SystemExit) as excinfo:
            _run(player.on_start())
        # Exit 0 so Restart=on-failure does NOT crash-loop the service.
        assert excinfo.value.code == 0
        # READY=1 must be notified before exiting: the unit is
        # Type=notify, and exiting without READY=1 makes systemd record
        # Result=protocol — a failure that Restart=on-failure restarts.
        assert any(n.startswith("READY=1") for n in notifications)
        assert any("STOPPING=1" in n for n in notifications)

    def test_on_start_with_ip_starts_monitor(self, monkeypatch):
        monkeypatch.setattr(bluesound_module, "BLUESOUND_IP", "192.168.1.50")
        player = BluesoundPlayer()
        # running=False makes the monitor loop exit immediately.
        player.running = False

        async def _go():
            await player.on_start()
            assert player._monitor_task is not None
            await player._monitor_task

        _run(_go())
