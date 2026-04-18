"""Tests for services/lib/beacon.py — UUID stability and beacon payload."""
from __future__ import annotations

import sys
import uuid
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "services"))

from lib.beacon import _get_or_create_device_id, _build_payload


# ── UUID persistence ──────────────────────────────────────────────────────────

def test_uuid_created_on_first_call(tmp_path):
    """A UUID is generated and written when no device_id file exists."""
    result = _get_or_create_device_id(str(tmp_path))
    id_file = tmp_path / "device_id"
    assert id_file.exists()
    assert result == id_file.read_text().strip()


def test_uuid_is_valid_uuid4(tmp_path):
    result = _get_or_create_device_id(str(tmp_path))
    parsed = uuid.UUID(result)
    assert parsed.version == 4


def test_uuid_stable_across_calls(tmp_path):
    """Same UUID returned on every subsequent call — simulates reboots."""
    first = _get_or_create_device_id(str(tmp_path))
    second = _get_or_create_device_id(str(tmp_path))
    third = _get_or_create_device_id(str(tmp_path))
    assert first == second == third


def test_uuid_stable_when_file_pre_exists(tmp_path):
    """UUID read from an existing file, not regenerated."""
    known_id = "aaaaaaaa-bbbb-4ccc-dddd-eeeeeeeeeeee"
    (tmp_path / "device_id").write_text(known_id + "\n")
    result = _get_or_create_device_id(str(tmp_path))
    assert result == known_id


def test_uuid_survives_ota_exclude_list():
    """device_id is in _UPDATE_EXCLUDES so OTA rsync never clobbers it."""
    # Read the list directly from source rather than importing the full module
    # (input.py imports `hid` which isn't available in the test environment).
    source = (REPO_ROOT / "services" / "input.py").read_text()
    # Extract the _UPDATE_EXCLUDES list as text and check for the entry
    import ast, re
    m = re.search(r"_UPDATE_EXCLUDES\s*=\s*(\[.*?\])", source, re.DOTALL)
    assert m, "_UPDATE_EXCLUDES not found in input.py"
    excludes = ast.literal_eval(m.group(1))
    assert "device_id" in excludes, "device_id must be in _UPDATE_EXCLUDES"


def test_uuid_survives_deploy_ignore():
    """.deployignore lists device_id so deploy.sh --delete never removes it."""
    deployignore = REPO_ROOT / ".deployignore"
    if not deployignore.exists():
        pytest.skip(".deployignore not present in this repo")
    lines = deployignore.read_text().splitlines()
    assert "device_id" in lines, ".deployignore must contain 'device_id'"


def test_uuid_fallback_on_unwritable_dir(tmp_path):
    """Returns 'unknown' gracefully if the file can't be written — never raises."""
    ro_dir = tmp_path / "readonly"
    ro_dir.mkdir(mode=0o555)
    result = _get_or_create_device_id(str(ro_dir))
    assert result == "unknown"


# ── Payload shape ─────────────────────────────────────────────────────────────

def test_payload_contains_required_keys(tmp_path):
    (tmp_path / "VERSION").write_text("v0.8.0\n")
    with patch("lib.beacon._get_or_create_device_id", return_value="test-uuid"), \
         patch("lib.config.load_config", return_value={
             "device": "Test", "player": {"type": "sonos"},
             "volume": {"type": "beolab5"}, "spotify": {},
         }):
        payload = _build_payload(str(tmp_path))

    assert payload["device_id"] == "test-uuid"
    assert payload["version"] == "v0.8.0"
    assert isinstance(payload["sources"], list)
    assert payload["player_type"] == "sonos"
    assert payload["volume_type"] == "beolab5"


def test_payload_sources_excludes_system_sections(tmp_path):
    (tmp_path / "VERSION").write_text("v0.8.0\n")
    config = {
        "device": "x", "menu": {}, "scenes": [], "player": {}, "volume": {},
        "home_assistant": {}, "transport": {}, "showing": {}, "join": {},
        "bluetooth": {}, "remote": {},
        "spotify": {"client_id": "abc"},
        "cd": {"device": "/dev/sr0"},
        "radio": {},
    }
    with patch("lib.beacon._get_or_create_device_id", return_value="x"), \
         patch("lib.config.load_config", return_value=config):
        payload = _build_payload(str(tmp_path))

    assert set(payload["sources"]) == {"spotify", "cd", "radio"}


def test_payload_version_fallback_when_no_file(tmp_path):
    with patch("lib.beacon._get_or_create_device_id", return_value="x"), \
         patch("lib.config.load_config", return_value={}):
        payload = _build_payload(str(tmp_path))
    assert payload["version"] == "unknown"
