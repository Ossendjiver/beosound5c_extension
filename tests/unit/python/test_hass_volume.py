"""Frame volume override and regression coverage for existing HA routing."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from lib.volume_adapters.hass import HassVolume

YOUTUBE = "media_player.youtube_on_frame_65"
FRAME = "media_player.the_frame"
ROOM = "media_player.lounge"


class Response:
    def __init__(self, state=None, status=200):
        self.status = status
        self.state = state

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def json(self):
        return {"state": self.state}


@pytest.fixture
def adapter(monkeypatch, mock_config):
    mock_config({"volume": {"step": 3}})
    monkeypatch.setenv("HA_URL", "http://ha.test:8123")
    monkeypatch.setenv("HA_TOKEN", "test-token")
    monkeypatch.setenv("HASS_FALLBACK_ENTITY", "media_player.fallback")
    monkeypatch.setenv("HASS_VOLUME_PRIORITY", '["media_player.lounge"]')
    monkeypatch.delenv("MLGW_ENTITY_TO_MLN", raising=False)
    session = MagicMock()
    session.post.return_value = Response()
    volume = HassVolume(70, session)
    volume._send_mlgw_steps = AsyncMock(return_value=False)
    return volume


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["playing", "paused", "buffering"])
@pytest.mark.parametrize("volume,service", [(33, "volume_up"), (27, "volume_down")])
async def test_youtube_routes_both_directions_to_physical_frame(adapter, state, volume, service):
    adapter._session.get.return_value = Response(state)
    await adapter._apply_volume(volume)
    adapter._session.post.assert_called_once_with(
        f"http://ha.test:8123/api/services/media_player/{service}",
        headers=adapter._headers(), json={"entity_id": FRAME},
    )
    assert adapter._session.get.call_args.args[0].endswith(f"/states/{YOUTUBE}")


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["idle", "off", "unavailable", "unknown", None])
@pytest.mark.parametrize("volume,service", [(33, "volume_up"), (27, "volume_down")])
async def test_inactive_youtube_preserves_room_routing(adapter, state, volume, service):
    adapter._session.get.side_effect = [Response(state), Response("playing")]
    await adapter._apply_volume(volume)
    adapter._session.post.assert_called_once_with(
        f"http://ha.test:8123/api/services/media_player/{service}",
        headers=adapter._headers(), json={"entity_id": ROOM},
    )
    adapter._send_mlgw_steps.assert_awaited_once_with(ROOM, 2, volume - 30)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [Response(status=404), Response(status=503), RuntimeError("offline")])
async def test_lookup_failure_preserves_room_routing(adapter, failure):
    adapter._session.get.side_effect = [failure, Response("playing")]
    assert await adapter._get_active_target() == ROOM


@pytest.mark.asyncio
async def test_existing_fallback_and_priority_selection(adapter):
    adapter._session.get.side_effect = [Response("idle"), Response("paused")]
    assert await adapter._get_active_target() == "media_player.fallback"
    adapter._fallback_entity = ""
    adapter._session.get.side_effect = [Response("off"), Response("idle")]
    assert await adapter._get_active_target() == ROOM


@pytest.mark.asyncio
async def test_no_token_preserves_selection_without_lookup(adapter):
    adapter._ha_token = ""
    assert await adapter._get_active_target() == "media_player.fallback"
    adapter._session.get.assert_not_called()


@pytest.mark.asyncio
async def test_existing_mlgw_delivery_is_preserved(adapter):
    adapter._session.get.side_effect = [Response("off"), Response("playing")]
    adapter._send_mlgw_steps.return_value = True
    await adapter._apply_volume(33)
    adapter._send_mlgw_steps.assert_awaited_once_with(ROOM, 2, 3)
    adapter._session.post.assert_not_called()
