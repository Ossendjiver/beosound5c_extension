"""Frame volume override and regression coverage for existing HA routing."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from lib.volume_adapters.hass import HassVolume

YOUTUBE = "media_player.youtube_on_frame_65"
FRAME = "media_player.the_frame"
ROOM = "media_player.lounge"


class Response:
    def __init__(self, state=None, status=200, **attributes):
        self.status = status
        self.state = state
        self.attributes = attributes

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def json(self):
        return {"state": self.state, "attributes": self.attributes}


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


@pytest.mark.asyncio
@pytest.mark.parametrize("child", ["media_player.dining", "media_player.dining_3"])
async def test_dining_showing_routes_to_the_physical_volume_entity(adapter, child):
    adapter._showing_entity = "media_player.beosound_global_showing"
    adapter._showing_child_targets = {child: "media_player.dining"}
    adapter._session.get.side_effect = [Response("off"), Response("playing", active_child=child),
                                        Response("playing", volume_level=0.46)]
    await adapter._apply_volume(33)
    assert adapter._session.post.call_args.args[0].endswith("/volume_set")
    assert adapter._session.post.call_args.kwargs["json"] == {"entity_id": "media_player.dining", "volume_level": 0.49}
    assert adapter._session.get.call_count == 3


@pytest.mark.asyncio
async def test_dining_uses_fresh_receiver_level_and_respects_cap(adapter):
    adapter._showing_child_targets = {"media_player.dining": "media_player.dining"}
    adapter._get_active_target = AsyncMock(return_value="media_player.dining")
    adapter._session.get.return_value = Response("playing", volume_level=0.69)
    adapter.sync_volume_baseline(await adapter.read_active_volume())
    await adapter._apply_volume(70)
    assert adapter._session.post.call_args.kwargs["json"]["volume_level"] == 0.70
    adapter._session.get.return_value = Response("playing", volume_level=0.4)
    await adapter._apply_volume(68)
    assert adapter._session.post.call_args.kwargs["json"]["volume_level"] == 0.38


@pytest.mark.asyncio
async def test_dining_fallback_only_overrides_an_inactive_volume_priority(adapter):
    adapter._showing_entity = "media_player.beosound_global_showing"
    adapter._showing_child_targets = {"media_player.dining_3": "media_player.dining"}
    adapter._session.get.side_effect = [Response("off"), Response("idle"), Response("playing")]
    assert await adapter._get_active_target() == ROOM
    adapter._session.get.side_effect = [Response("off"), Response("idle"), Response("off"), Response("playing")]
    assert await adapter._get_active_target() == "media_player.dining"


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["media_player.cuisine", "media_player.cuisine_ma", "media_player.lounge_g", "media_player.lounge_mini_ma"])
async def test_cast_and_ma_outputs_report_actual_volume(adapter, target):
    adapter._showing_entity = "media_player.beosound_global_showing"
    adapter._showing_child_targets = {target: target}
    adapter._session.get.side_effect = [Response("off"), Response("playing", active_child=target),
                                        Response("playing", volume_level=0.35)]
    assert await adapter.read_active_volume() == 35
    adapter._session.get.side_effect = [Response("off"), Response("playing", active_child=target),
                                        Response("playing", volume_level=0.35)]
    await adapter._apply_volume(28)
    assert adapter._session.post.call_args.kwargs["json"] == {"entity_id": target, "volume_level": 0.33}


@pytest.mark.asyncio
async def test_frame_youtube_reads_the_powered_tv_volume(adapter):
    adapter._showing_child_targets = {YOUTUBE: FRAME}
    adapter._session.get.side_effect = [Response("playing"), Response("on", volume_level=0.16)]
    assert await adapter.read_active_volume() == 16


@pytest.mark.asyncio
@pytest.mark.parametrize("art_mode,target", [("off", FRAME), ("on", "media_player.fallback")])
async def test_frame_hdmi_fallback_leaves_art_mode_alone(adapter, art_mode, target):
    adapter._showing_entity = "media_player.beosound_global_showing"
    adapter._showing_child_targets = {YOUTUBE: FRAME}
    adapter._session.get.side_effect = [Response("off"), Response("idle"), Response("off"),
                                        Response("on", volume_level=0.16, art_mode_status=art_mode)]
    assert await adapter._get_active_target() == target
