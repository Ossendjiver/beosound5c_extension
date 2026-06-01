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

    sent = []
    pc2.send_message = sent.append
    pc2.set_address_filter()

    assert sent == [[0xF6, 0x00, 0x82, 0x80, 0x83]]
