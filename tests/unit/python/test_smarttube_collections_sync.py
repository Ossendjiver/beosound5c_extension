"""Kodi shared SmartTube collections: initial sync, update, delete, conflict."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "integrations/kodi/service.bs5c.youtube.history/resources/lib"))
from collections_sync import sync_collection


def test_collection_sync_and_conflict_preservation(tmp_path):
    profile = tmp_path / "smarttube"
    own = tmp_path / "monitor"
    profile.mkdir()
    channel_id = "UC1234567890123456789012"
    local_item = {"id": channel_id, "title": "Channel"}
    (profile / "follows.json").write_text(json.dumps([local_item]), encoding="utf-8")
    server = {}

    def remote(host, key, name, change=None):
        assert name == "follows"
        if change is None:
            return {"items": list(server.values())}
        old = server.get(change["id"])
        version = old["version"] if old else 0
        if change["base_version"] != version:
            return {"conflict": True}
        server[change["id"]] = {
            "id": change["id"], "version": version + 1,
            "item": change["item"], "deleted": change["deleted"],
        }
        return {"accepted": True, "version": version + 1}

    def sync():
        return sync_collection(str(profile), str(own), "follows", "bedroom",
                               "192.168.4.103", "x"*40, remote=remote)

    assert sync()["changes"] == 1
    assert sync()["changes"] == 0
    server[channel_id] = {"id": channel_id, "version": 2, "deleted": False,
                          "item": {"id": channel_id, "title": "Updated by Lounge"}}
    assert sync()["changes"] == 1
    assert json.loads((profile / "follows.json").read_text())[0]["title"] == "Updated by Lounge"

    (profile / "follows.json").write_text(json.dumps([{"id": channel_id, "title": "Edited on Bedroom"}]))
    server[channel_id] = {"id": channel_id, "version": 3, "deleted": False,
                          "item": {"id": channel_id, "title": "Edited on Lounge"}}
    assert sync()["conflicts"] == 1
    assert json.loads((profile / "follows.json").read_text())[0]["title"] == "Edited on Bedroom"
    assert json.loads((own / "follows.conflicts.json").read_text())["ids"] == [channel_id]


def test_remote_tombstone_removes_only_previously_synced_item(tmp_path):
    profile = tmp_path / "profile"
    own = tmp_path / "monitor"
    profile.mkdir()
    item_id = "dQw4w9WgXcQ"
    remote_row = {"id": item_id, "version": 1, "deleted": False,
                  "item": {"id": item_id, "title": "Saved"}}

    def remote(host, key, name, change=None):
        return {"items": [remote_row]}

    assert sync_collection(str(profile), str(own), "watch_later", "bedroom",
                           "192.168.4.103", "x"*40, remote=remote)["changes"] == 1
    assert len(json.loads((profile / "watch_later.json").read_text())) == 1
    remote_row.update(version=2, deleted=True, item={})
    assert sync_collection(str(profile), str(own), "watch_later", "bedroom",
                           "192.168.4.103", "x"*40, remote=remote)["changes"] == 1
    assert json.loads((profile / "watch_later.json").read_text()) == []
