"""Tests for EventRouter: canvas generation, media handling."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

SERVICES_DIR = Path(__file__).resolve().parents[3] / "services"
sys.path.insert(0, str(SERVICES_DIR))


def make_router():
    """Create an EventRouter with mocked dependencies (no I/O)."""
    def fake_cfg(*keys, default=None):
        return default

    with patch("lib.config.cfg", side_effect=fake_cfg), \
         patch("lib.transport.Transport"), \
         patch("lib.volume_adapters.create_volume_adapter"), \
         patch("lib.volume_adapters.infer_volume_type", return_value="sonos"), \
         patch("lib.lydbro.LydbroHandler"):
        import importlib
        if "router" in sys.modules:
            import router as router_mod
            with patch.object(router_mod, "router_instance", MagicMock()):
                router = router_mod.EventRouter()
        else:
            import router as router_mod
            router = router_mod.EventRouter()
    return router


class TestCanvasGeneration:
    """Canvas injection uses a generation counter to prevent stale injection."""

    def test_generation_starts_at_zero(self):
        router = make_router()
        assert router._canvas_generation == 0

    def test_inject_canvas_bails_on_stale_generation(self):
        """If generation changed during fetch, canvas is not injected."""
        router = make_router()
        router._session = MagicMock()

        async def run():
            # Set up media state
            router.media._state = {"title": "Song", "artist": "Art", "canvas_url": ""}

            # Mock HTTP calls: player track_uri + spotify canvas
            track_resp = MagicMock()
            track_resp.status = 200
            track_resp.json = AsyncMock(return_value={"track_uri": "spotify:track:01RdEXps15f3VmQMV6OuTM"})
            track_resp.__aenter__ = AsyncMock(return_value=track_resp)
            track_resp.__aexit__ = AsyncMock(return_value=False)

            canvas_resp = MagicMock()
            canvas_resp.status = 200
            canvas_resp.json = AsyncMock(return_value={"canvas_url": "https://canvas.mp4"})
            canvas_resp.__aenter__ = AsyncMock(return_value=canvas_resp)
            canvas_resp.__aexit__ = AsyncMock(return_value=False)

            call_count = [0]
            def mock_get(url, **kwargs):
                call_count[0] += 1
                if "track_uri" in url:
                    return track_resp
                return canvas_resp

            router._session.get = mock_get

            # Inject with generation 1, but advance to 2 before it finishes
            router._canvas_generation = 2
            await router._inject_canvas({"title": "Song"}, generation=1)

            # Canvas should NOT have been applied
            assert router.media._state["canvas_url"] == ""

        asyncio.run(run())

    def test_inject_canvas_succeeds_on_matching_generation(self):
        """If generation matches, canvas is injected into media state."""
        router = make_router()
        router._session = MagicMock()

        async def run():
            router.media._state = {"title": "Song", "artist": "Art", "canvas_url": ""}
            router.media.push_media = AsyncMock()

            track_resp = MagicMock()
            track_resp.status = 200
            track_resp.json = AsyncMock(return_value={"track_uri": "spotify:track:01RdEXps15f3VmQMV6OuTM"})
            track_resp.__aenter__ = AsyncMock(return_value=track_resp)
            track_resp.__aexit__ = AsyncMock(return_value=False)

            canvas_resp = MagicMock()
            canvas_resp.status = 200
            canvas_resp.json = AsyncMock(return_value={"canvas_url": "https://canvas.mp4"})
            canvas_resp.__aenter__ = AsyncMock(return_value=canvas_resp)
            canvas_resp.__aexit__ = AsyncMock(return_value=False)

            def mock_get(url, **kwargs):
                if "track_uri" in url:
                    return track_resp
                return canvas_resp

            router._session.get = mock_get

            # Same generation — should succeed
            router._canvas_generation = 5
            await router._inject_canvas({"title": "Song"}, generation=5)

            assert router.media._state["canvas_url"] == "https://canvas.mp4"
            router.media.push_media.assert_called_once()

        asyncio.run(run())


class TestVolumeReportCooldown:
    """Player-reported volume races with local wheel-driven commands.

    The Sonos monitor polls the speaker every 500ms and reports the
    hardware-read volume back to the router. Mid-transition reads race
    with the target set by set_volume(), which flickers the UI arc
    between old and new values as the user turns the wheel.

    Fix: after a local set_volume(), ignore player-reported volume for
    a short cooldown so the mid-transition read doesn't feed back into
    the UI broadcast.
    """

    def _attach_adapter(self, router, max_volume=100):
        """Attach a mock adapter with the given hw max — default 100
        makes UI↔hw scaling a no-op so pre-scaling tests still read
        naturally."""
        router._volume = MagicMock()
        router._volume._max_volume = max_volume
        router._volume.set_volume = AsyncMock()
        router._volume.is_on_cached = MagicMock(return_value=True)
        return router._volume

    def test_report_suppressed_within_cooldown(self):
        router = make_router()
        router._accept_player_volume = True
        router.media.broadcast = AsyncMock()
        self._attach_adapter(router)

        async def run():
            # User wheels volume up to 50 (UI scale).
            await router.set_volume(50)
            await asyncio.sleep(0.01)
            broadcast_calls_after_set = router.media.broadcast.await_count

            # Player monitor polls mid-transition and reports hw 47.
            # With max_volume=100 this is UI 47 — which differs from
            # the router's 50 and would flicker the UI if not
            # suppressed by the cooldown.
            await router.report_volume(47)

            assert router.volume == 50, (
                f"router.volume should stay 50, got {router.volume}")
            assert router.media.broadcast.await_count == broadcast_calls_after_set

        asyncio.run(run())

    def test_report_accepted_after_cooldown(self):
        router = make_router()
        router._accept_player_volume = True
        router.media.broadcast = AsyncMock()
        self._attach_adapter(router)

        async def run():
            # Simulate a local set_volume long ago.
            router._last_local_volume_set = 0.0
            router.volume = 40

            # Player reports a real external change (e.g. Sonos app).
            await router.report_volume(55)
            assert router.volume == 55
            router.media.broadcast.assert_awaited()

        asyncio.run(run())

    def test_report_dedup_same_value(self):
        """A report that matches current volume is a no-op (no broadcast)."""
        router = make_router()
        router._accept_player_volume = True
        router._last_local_volume_set = 0.0  # no cooldown
        router.volume = 42
        router.media.broadcast = AsyncMock()
        self._attach_adapter(router)

        async def run():
            await router.report_volume(42)
            router.media.broadcast.assert_not_awaited()

        asyncio.run(run())


class TestVolumeStateSync:
    def _attach_adapter(self, router, max_volume=100):
        router._volume = MagicMock()
        router._volume._max_volume = max_volume
        router._volume.set_volume = AsyncMock()
        router._volume.is_on_cached = MagicMock(return_value=True)
        return router._volume

    def test_set_volume_state_updates_ui_without_hardware_command(self):
        router = make_router()
        router.media.broadcast = AsyncMock()
        adapter = self._attach_adapter(router)
        router.volume = 30
        router._last_local_volume_set = 12.0

        async def run():
            await router.set_volume_state(42)
            assert router.volume == 42
            adapter.set_volume.assert_not_awaited()
            router.media.broadcast.assert_awaited_once()
            assert router._last_local_volume_set == 12.0

        asyncio.run(run())

    def test_set_volume_state_dedups_same_value(self):
        router = make_router()
        router.media.broadcast = AsyncMock()
        self._attach_adapter(router)
        router.volume = 42

        async def run():
            await router.set_volume_state(42)
            router.media.broadcast.assert_not_awaited()

        asyncio.run(run())


class TestVolumeScaling:
    """UI 0–100 ↔ hardware 0–max_volume scaling.

    Office's PowerLink/BeoLab 8000 runs with max_volume=65 out of a
    PC2 range of 0–127. Before scaling, wheel positions above UI 65
    were clipped to the cap (dead zone) and mid-positions like UI 50
    felt unnaturally quiet because they sat at only 39% of the PC2
    scale. After scaling, the full UI wheel maps linearly onto
    0..max_volume, giving the user the whole range plus a safety cap.
    """

    def _make_router_with_adapter(self, max_volume):
        router = make_router()
        router._volume = MagicMock()
        router._volume._max_volume = max_volume
        router._volume.set_volume = AsyncMock()
        router._volume.is_on_cached = MagicMock(return_value=True)
        router.media.broadcast = AsyncMock()
        return router

    def test_set_volume_scales_ui_to_hw(self):
        """UI 50% with max_volume=70 must send hw 35 to the adapter."""
        router = self._make_router_with_adapter(max_volume=70)

        async def run():
            await router.set_volume(50)
            await asyncio.sleep(0.01)
            router._volume.set_volume.assert_awaited_once()
            hw_value = router._volume.set_volume.await_args.args[0]
            assert hw_value == 35.0, (
                f"UI 50 with max 70 should send hw 35, got {hw_value}")
            # self.volume stays in UI scale
            assert router.volume == 50

        asyncio.run(run())

    def test_set_volume_ui_100_hits_max(self):
        """UI 100% always lands exactly at the configured hw max."""
        router = self._make_router_with_adapter(max_volume=70)

        async def run():
            await router.set_volume(100)
            await asyncio.sleep(0.01)
            hw_value = router._volume.set_volume.await_args.args[0]
            assert hw_value == 70.0

        asyncio.run(run())

    def test_report_volume_unscales_hw_to_ui(self):
        """Player reports in hw scale; router stores in UI scale."""
        router = self._make_router_with_adapter(max_volume=70)
        router._accept_player_volume = True
        router._last_local_volume_set = 0.0  # no cooldown
        router.volume = 0

        async def run():
            # Player reports hw 35 (half of max 70).
            await router.report_volume(35)
            # Should register as UI 50.
            assert router.volume == 50.0, (
                f"hw 35 with max 70 should become UI 50, got {router.volume}")

        asyncio.run(run())

    def test_scaling_roundtrip(self):
        """set→report cycle must not drift the stored UI volume."""
        router = self._make_router_with_adapter(max_volume=70)
        router._accept_player_volume = True
        router._last_local_volume_set = 0.0

        async def run():
            # Router commanded UI 42; adapter got a hw value.
            await router.set_volume(42)
            await asyncio.sleep(0.01)
            hw = router._volume.set_volume.await_args.args[0]
            # Player reads hw back from speaker and reports it.
            await router.report_volume(hw)
            # UI value rounds back to 42.
            assert round(router.volume) == 42

        asyncio.run(run())


class TestSpawnIntegration:
    """_spawn is used throughout route_event — verify it tracks tasks."""

    def test_spawn_in_event_routing(self):
        router = make_router()

        async def run():
            # Verify the tracking set exists and is empty
            assert len(router._background_tasks) == 0

            # Spawn a task and verify it's tracked
            task = router._spawn(asyncio.sleep(0.01), name="test")
            assert len(router._background_tasks) == 1
            await task
            # After completion, auto-removed
            assert len(router._background_tasks) == 0

        asyncio.run(run())


class TestForwardToSourcePayloads:
    def test_source_switch_stop_does_not_inject_playback_targets(self):
        router = make_router()
        router.playback_state = {
            "audio_target_id": "audio-target",
            "video_target_id": "video-target",
            "audio_targets": [],
            "video_targets": [],
            "music_video_enabled": True,
        }

        sent = {}

        class _FakeResponse:
            status = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class _FakeSession:
            def post(self, url, json=None, timeout=None, headers=None):
                sent["url"] = url
                sent["json"] = json
                sent["headers"] = headers
                return _FakeResponse()

        router._session = _FakeSession()

        source = MagicMock()
        source.id = "mass"
        source.command_url = "http://localhost:8783/command"

        async def run():
            await router._forward_to_source(source, {"action": "stop"})

        asyncio.run(run())

        assert sent["json"] == {"action": "stop"}
