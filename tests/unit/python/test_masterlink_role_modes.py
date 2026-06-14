import asyncio
import importlib
import json
import sys
import types


def _load_masterlink(monkeypatch, tmp_path, role):
    import lib.config as config_mod

    usb_pkg = types.ModuleType("usb")
    usb_core = types.ModuleType("usb.core")
    usb_util = types.ModuleType("usb.util")
    usb_pkg.core = usb_core
    usb_pkg.util = usb_util
    monkeypatch.setitem(sys.modules, "usb", usb_pkg)
    monkeypatch.setitem(sys.modules, "usb.core", usb_core)
    monkeypatch.setitem(sys.modules, "usb.util", usb_util)

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "device": "Test",
        "menu": {"PLAYING": "playing"},
        "home_assistant": {"webhook_url": "http://example.invalid/webhook"},
        "masterlink": {"role": role},
    }))
    monkeypatch.setattr(config_mod, "_SEARCH_PATHS", [str(config_path)])
    config_mod._config = None
    sys.modules.pop("masterlink", None)
    return importlib.import_module("masterlink")


def test_masterlink_role_aliases_normalize(monkeypatch, tmp_path):
    mod = _load_masterlink(monkeypatch, tmp_path, "audio_slave")
    assert mod.ML_ROLE == "link"
    assert mod.normalize_masterlink_role("slave") == "link"
    assert mod.normalize_masterlink_role("passive") == "none"
    assert mod.normalize_masterlink_role("disabled") == "none"
    assert mod.normalize_masterlink_role("unknown-role") == "master"


def test_none_role_uses_passive_identity_and_filter(monkeypatch, tmp_path):
    mod = _load_masterlink(monkeypatch, tmp_path, "none")
    pc2 = mod.PC2Device()
    assert pc2.OUR_NODE_ID == 0x82
    assert pc2._address_filter_role == "none"
    assert pc2._ml_bus_enabled is False

    sent = []
    pc2.send_message = sent.append
    pc2.set_address_filter()

    assert sent == [[0xF6, 0x00, 0x82, 0x80, 0x83]]


def test_none_role_starts_without_usb_sniffer(monkeypatch, tmp_path):
    mod = _load_masterlink(monkeypatch, tmp_path, "none")
    created_targets = []

    class FakeThread:
        def __init__(self, target=None, *args, **kwargs):
            self.target = target
            self.daemon = False
            created_targets.append(target)

        def start(self):
            return None

        def join(self, timeout=None):
            return None

    monkeypatch.setattr(mod.threading, "Thread", FakeThread)

    pc2 = mod.PC2Device()
    pc2.start_sniffing()

    assert pc2.sniffer_thread is None
    assert pc2.sender_thread is not None
    assert created_targets == [pc2._sender_loop_wrapper]


def test_none_role_passively_forwards_tv_status(monkeypatch, tmp_path):
    mod = _load_masterlink(monkeypatch, tmp_path, "none")

    forwarded = []

    async def fake_forward(session, source, action, device_type, link="", count=1):
        forwarded.append({
            "session": session,
            "source": source,
            "action": action,
            "device_type": device_type,
            "link": link,
            "count": count,
        })

    monkeypatch.setattr(mod, "forward_to_router", fake_forward)

    class FakeTasks:
        def __init__(self):
            self.spawned = []

        def spawn(self, coro, *, name=None):
            self.spawned.append((coro, name))
            return None

    pc2 = types.SimpleNamespace(
        session=object(),
        _background_tasks=FakeTasks(),
        node_label=lambda node_id: "VIDEO_MASTER" if node_id == 0xC0 else f"0x{node_id:02X}",
    )
    role = mod.PassiveRole(pc2)

    role.handle_telegram(
        0x14, 0x87, 0xC0, 0x83, 0x0B,
        [0x0B, 0x01, 0x00, 0x00, 0x40, 0x00, 0x01],
    )

    assert len(pc2._background_tasks.spawned) == 1
    coro, name = pc2._background_tasks.spawned[0]
    assert name == "passive_status_tv"
    asyncio.run(coro)
    assert forwarded == [{
        "session": pc2.session,
        "source": "masterlink",
        "action": "tv",
        "device_type": "Video",
        "link": "VIDEO_MASTER",
        "count": 1,
    }]

    role.handle_telegram(
        0x14, 0x87, 0xC0, 0x83, 0x0B,
        [0x0B, 0x01, 0x00, 0x00, 0x40, 0x00, 0x01],
    )
    assert len(pc2._background_tasks.spawned) == 1
