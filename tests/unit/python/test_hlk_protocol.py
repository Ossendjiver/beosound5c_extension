from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

SERVICES_DIR = Path(__file__).resolve().parents[3] / "services"
sys.path.insert(0, str(SERVICES_DIR))

from lib.hlk_protocol import (  # noqa: E402
    DelayOffState,
    HLKDerivedConfig,
    ReportParser,
    apply_delay_off,
    derive_presence_candidate,
    derive_state_payload,
    derive_wake_signal,
)


def _engineering_frame(
    *,
    target_state_raw: int = 0x03,
    moving_distance_cm: int = 82,
    moving_energy: int = 31,
    still_distance_cm: int = 91,
    still_energy: int = 19,
    detection_distance_cm: int = 91,
) -> bytes:
    payload = bytearray()
    payload.append(target_state_raw)
    payload.extend(int(moving_distance_cm).to_bytes(2, "little"))
    payload.append(int(moving_energy))
    payload.extend(int(still_distance_cm).to_bytes(2, "little"))
    payload.append(int(still_energy))
    payload.extend(int(detection_distance_cm).to_bytes(2, "little"))
    payload.append(2)
    payload.append(3)
    payload.extend(bytes(range(11, 20)))
    payload.extend(bytes(range(21, 30)))
    payload.append(74)
    payload.append(1)
    frame_data = bytes([0x01, 0xAA]) + bytes(payload) + b"\x55\x00"
    return b"\xF4\xF3\xF2\xF1" + len(frame_data).to_bytes(2, "little") + frame_data + b"\xF8\xF7\xF6\xF5"


def test_report_parser_decodes_engineering_frame():
    parser = ReportParser()

    reports = parser.feed(_engineering_frame())

    assert len(reports) == 1
    report = reports[0]
    assert report["report_type"] == "engineering"
    assert report["presence"] is True
    assert report["moving"] is True
    assert report["still"] is True
    assert report["moving_distance_cm"] == 82
    assert report["still_distance_cm"] == 91
    assert report["detection_distance_cm"] == 91
    assert report["light_level"] == 74
    assert report["frame_bytes"] > 0


def test_derive_wake_signal_uses_close_moving_thresholds():
    cfg = HLKDerivedConfig(wake_distance_min_cm=25, wake_distance_max_cm=150, wake_moving_energy_min=18)

    assert derive_wake_signal(
        {
            "moving": True,
            "moving_distance_cm": 120,
            "moving_energy": 21,
        },
        cfg,
    ) is True
    assert derive_wake_signal(
        {
            "moving": True,
            "moving_distance_cm": 220,
            "moving_energy": 21,
        },
        cfg,
    ) is False
    assert derive_wake_signal(
        {
            "moving": False,
            "moving_distance_cm": 120,
            "moving_energy": 40,
        },
        cfg,
    ) is False


def test_presence_candidate_matches_room_templates():
    cfg = HLKDerivedConfig(
        presence_distance_min_cm=25,
        presence_distance_max_cm=450,
        presence_moving_energy_min=18,
        presence_still_energy_min=12,
    )

    assert derive_presence_candidate(
        {
            "presence": False,
            "detection_distance_cm": 300,
            "moving_distance_cm": 0,
            "moving_energy": 0,
            "still_distance_cm": 280,
            "still_energy": 12,
        },
        cfg,
    ) is True
    assert derive_presence_candidate(
        {
            "presence": False,
            "detection_distance_cm": 600,
            "moving_distance_cm": 300,
            "moving_energy": 25,
            "still_distance_cm": 0,
            "still_energy": 0,
        },
        cfg,
    ) is False


def test_apply_delay_off_holds_presence_until_timeout():
    state = DelayOffState(value=True, off_since=None)
    now = 100.0

    held = apply_delay_off(state, False, now=now, delay_off_s=20.0)
    still_held = apply_delay_off(held, False, now=118.0, delay_off_s=20.0)
    cleared = apply_delay_off(still_held, False, now=121.0, delay_off_s=20.0)

    assert held.value is True
    assert held.off_since == now
    assert still_held.value is True
    assert cleared.value is False
    assert cleared.off_since is None


def test_derive_state_payload_sets_macro_and_stable_presence():
    cfg = HLKDerivedConfig()
    report = {
        "presence": True,
        "moving": True,
        "moving_distance_cm": 90,
        "moving_energy": 28,
        "still_distance_cm": 0,
        "still_energy": 0,
        "detection_distance_cm": 90,
    }

    payload = derive_state_payload(report, cfg, stable_state=DelayOffState(), now=time.monotonic())

    assert payload["wake"] is True
    assert payload["macro_movement"] is True
    assert payload["presence_candidate"] is True
    assert payload["presence_stable"] is True
