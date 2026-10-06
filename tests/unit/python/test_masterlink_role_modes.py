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
    assert mod.normalize_masterlink_role("passive") == "ir_only"
    assert mod.normalize_masterlink_role("ir-only") == "ir_only"
    assert mod.normalize_masterlink_role("disabled") == "none"
    assert mod.normalize_masterlink_role("unknown-role") == "master"


def test_ir_only_role_uses_passive_identity_and_filter(monkeypatch, tmp_path):
    mod = _load_masterlink(monkeypatch, tmp_path, "ir_only")
    pc2 = mod.PC2Device()
    assert pc2.OUR_NODE_ID == 0x82
    assert pc2._address_filter_role == "ir_only"
    assert pc2._pc2_enabled is True
    assert pc2._ml_bus_enabled is False

    sent = []
    pc2.send_message = sent.append
    pc2.set_address_filter()

    assert sent == [[0xF6, 0x00, 0x82, 0x80, 0x83]]


def test_none_role_disables_pc2(monkeypatch, tmp_path):
    mod = _load_masterlink(monkeypatch, tmp_path, "none")
    pc2 = mod.PC2Device()
    assert pc2.OUR_NODE_ID == 0x82
    assert pc2._pc2_enabled is False
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


def test_ir_only_role_starts_usb_sniffer(monkeypatch, tmp_path):
    mod = _load_masterlink(monkeypatch, tmp_path, "ir_only")
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

    assert pc2.sniffer_thread is not None
    assert pc2.sender_thread is not None
    assert created_targets == [pc2._sniff_loop, pc2._sender_loop_wrapper]


def test_ir_only_role_ignores_ml_telegrams(monkeypatch, tmp_path):
    mod = _load_masterlink(monkeypatch, tmp_path, "ir_only")
    pc2 = mod.PC2Device()

    handled = []
    pc2._log_ml_telegram = handled.append
    pc2._process_usb_frame([0x60, 0x00, 0x00, 0x61])

    assert handled == []


def test_ir_only_role_still_enqueues_ir(monkeypatch, tmp_path):
    mod = _load_masterlink(monkeypatch, tmp_path, "ir_only")
    pc2 = mod.PC2Device()

    added = []
    pc2.message_queue = types.SimpleNamespace(add=added.append)
    pc2.process_beo4_keycode = lambda timestamp, message: {
        "device_type": "Audio",
        "key_name": "radio",
    }
    pc2._ir_passes_filter = lambda msg: True

    pc2._process_usb_frame([0x60, 0x00, 0x02, 0x61])

    assert added == [{"device_type": "Audio", "key_name": "radio"}]
