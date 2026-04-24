from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from sources.kodi.service import KodiSource
from sources.mass.service import MassSource


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _make_mass_source():
    with patch.object(MassSource, "_load_local_cache", return_value=False):
        return MassSource()


def _make_kodi_source():
    with patch.object(KodiSource, "_load_local_cache", return_value=False):
        return KodiSource()


class TestSourceHandoffStop:
    def test_mass_source_switch_stop_ignores_selected_target(self):
        source = _make_mass_source()
        source._apply_playback_target_from_data = MagicMock()
        source._handle_transport_command = AsyncMock(return_value={"state": "available"})

        result = _run(source.handle_command("transport_stop", {
            "action": "stop",
            "playback": {"audio_target_id": "remote-player"},
        }))

        assert result["state"] == "available"
        source._apply_playback_target_from_data.assert_not_called()
        assert source._handle_transport_command.await_args.kwargs["preferred_player"] == ""

    def test_kodi_source_switch_stop_ignores_selected_target(self):
        source = _make_kodi_source()
        source._apply_playback_target_from_data = MagicMock()
        source._handle_transport_command = AsyncMock(return_value={"state": "available"})

        result = _run(source.handle_command("transport_stop", {
            "action": "stop",
            "playback": {"video_target_id": "remote-kodi"},
        }))

        assert result["state"] == "available"
        source._apply_playback_target_from_data.assert_not_called()
        assert source._handle_transport_command.await_args.kwargs["target"] is None
