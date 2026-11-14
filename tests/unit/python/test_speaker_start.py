import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from lib.speaker_start import SpeakerStart, SpeakerStartError


@pytest.fixture
def picker(mock_config, monkeypatch):
    config = Path(__file__).resolve().parents[3] / "config/speaker-start.example.json"
    mock_config(json.loads(config.read_text(encoding="utf-8")))
    monkeypatch.setenv("HA_TOKEN", "test-token")
    return SpeakerStart(None)


@pytest.mark.asyncio
async def test_picker_requires_all_receivers_off_and_no_current_playback(picker):
    states = [{"entity_id": entity, "state": "off"} for entity in picker.settings["power_entities"]]
    states += [{"entity_id": "binary_sensor.lounge_area_presence", "state": "off"}]
    picker._ha = AsyncMock(return_value=states)
    assert [target["id"] for target in await picker.options()] == [
        "dining-bl4000", "dining-penta", "lounge-window", "lounge-cinema",
    ]
    states.append({"entity_id": "media_player.dining", "state": "playing"})
    assert await picker.options() == []
    states.pop()
    states[0]["state"] = "on"
    assert await picker.options() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("target_id", ["dining-bl4000", "dining-penta", "lounge-window", "lounge-cinema"])
async def test_manual_start_holds_lease_and_sends_the_exact_queue(picker, target_id):
    target = picker.targets[target_id]
    calls = []

    async def ha(path, data=None, **kwargs):
        calls.append((path, data))
        if path == "/states":
            return [{"entity_id": entity, "state": "off"} for entity in picker.settings["power_entities"]]
        if path.startswith("/states/"):
            entity = path.removeprefix("/states/")
            if entity in target["player_entities"]:
                return {"state": "playing"}
            if entity == target.get("receiver"):
                return {"state": "on", "attributes": {"source": target["source"]}}
            if entity == target.get("switch"):
                return {"state": "on"}
            if entity == target.get("startup_input", {}).get("entity"):
                return {"state": "playing", "attributes": {"source": target["startup_input"]["source"]}}
            return {"state": "off"}
        return {}

    async def post(port, path, payload):
        calls.append((path, payload))
        return {"state": "playing"}

    picker._ha = ha
    picker._post_source = post
    result = await picker.start(target_id, {"command": "play_item", "url": "library://track/1", "queue_id": "old"})
    assert result["queue_id"] == target["queue_id"]
    assert calls[0][1]["action"] == "acquire"
    assert calls[-1][1] == {"action": "release", "owner": calls[0][1]["owner"]}
    assert ("/command", {"command": "play_now", "url": "library://track/1",
                          "target_player_id": target["queue_id"], "queue_id": target["queue_id"]}) in calls
    if target.get("receiver"):
        assert ("/services/media_player/select_source", {"entity_id": target["receiver"], "source": target["source"]}) in calls
    if target.get("cinema"):
        assert ("/services/script/homemedia_speaker_control", {"room": "lounge", "operation": "orientation", "cinema": target["cinema"]}) in calls


@pytest.mark.asyncio
async def test_route_race_releases_lease_without_waking_a_speaker(picker):
    picker._ha = AsyncMock()
    picker._all_off = AsyncMock(return_value=False)
    picker._wake = AsyncMock()
    with pytest.raises(SpeakerStartError, match="turned on"):
        await picker.start("lounge-window", None)
    picker._wake.assert_not_awaited()
    assert [call.args[1]["action"] for call in picker._ha.await_args_list] == ["acquire", "release"]
