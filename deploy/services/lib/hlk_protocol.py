from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

REPORT_HEADER = b"\xF4\xF3\xF2\xF1"
REPORT_FOOTER = b"\xF8\xF7\xF6\xF5"
CMD_HEADER = b"\xFD\xFC\xFB\xFA"
CMD_FOOTER = b"\x04\x03\x02\x01"

ENABLE_CONFIG = 0x00FF
END_CONFIG = 0x00FE
ENABLE_ENGINEERING = 0x0062
DISABLE_ENGINEERING = 0x0063

TARGET_STATE_LABELS = {
    0x00: "none",
    0x01: "moving",
    0x02: "still",
    0x03: "both",
}

GATE_COUNT = 9


@dataclass(slots=True)
class HLKDerivedConfig:
    wake_distance_min_cm: int = 25
    wake_distance_max_cm: int = 150
    wake_moving_energy_min: int = 18
    presence_distance_min_cm: int = 25
    presence_distance_max_cm: int = 450
    presence_moving_energy_min: int = 18
    presence_still_energy_min: int = 12
    presence_delay_off_s: float = 20.0


@dataclass(slots=True)
class DelayOffState:
    value: bool = False
    off_since: float | None = None


class ReportParser:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def feed(self, chunk: bytes) -> list[dict[str, Any]]:
        reports: list[dict[str, Any]] = []
        self.buffer.extend(chunk)
        while True:
            header_index = self.buffer.find(REPORT_HEADER)
            if header_index < 0:
                if len(self.buffer) > len(REPORT_HEADER):
                    del self.buffer[:-len(REPORT_HEADER)]
                break
            if header_index > 0:
                del self.buffer[:header_index]
            if len(self.buffer) < 10:
                break
            data_len = int.from_bytes(self.buffer[4:6], "little")
            total_len = 4 + 2 + data_len + 4
            if len(self.buffer) < total_len:
                break
            frame = bytes(self.buffer[:total_len])
            if frame[-4:] != REPORT_FOOTER:
                del self.buffer[0]
                continue
            del self.buffer[:total_len]
            report = self.parse_frame(frame)
            if report is not None:
                report["frame_hex"] = frame.hex()
                report["frame_bytes"] = len(frame)
                reports.append(report)
        return reports

    @staticmethod
    def parse_frame(frame: bytes) -> dict[str, Any] | None:
        frame_data = frame[6:-4]
        if len(frame_data) < 4:
            return None
        report_type = frame_data[0]
        if frame_data[1] != 0xAA or frame_data[-2:] != b"\x55\x00":
            return None
        payload = frame_data[2:-2]
        if len(payload) < 9:
            return None

        target_state_raw = payload[0]
        report: dict[str, Any] = {
            "timestamp": int(time.time()),
            "report_type_raw": report_type,
            "report_type": "engineering" if report_type == 0x01 else "normal" if report_type == 0x02 else "unknown",
            "payload_length": len(payload),
            "target_state_raw": target_state_raw,
            "target_state": TARGET_STATE_LABELS.get(target_state_raw, "unknown"),
            "presence": target_state_raw != 0x00,
            "moving": bool(target_state_raw & 0x01),
            "still": bool(target_state_raw & 0x02),
            "moving_distance_cm": int.from_bytes(payload[1:3], "little"),
            "moving_energy": payload[3],
            "still_distance_cm": int.from_bytes(payload[4:6], "little"),
            "still_energy": payload[6],
            "detection_distance_cm": int.from_bytes(payload[7:9], "little"),
        }

        if report_type == 0x01 and len(payload) >= 30:
            report["moving_target_gate"] = payload[9]
            report["still_target_gate"] = payload[10]
            report["moving_gate_energies"] = list(payload[11:20])
            report["still_gate_energies"] = list(payload[20:29])
            report["light_level"] = payload[29]
            if len(payload) > 30:
                report["out_pin_presence_status"] = payload[30] == 0x01
        else:
            report["moving_target_gate"] = None
            report["still_target_gate"] = None
            report["moving_gate_energies"] = []
            report["still_gate_energies"] = []
            report["light_level"] = None

        return report


class CommandProtocol:
    def __init__(self) -> None:
        self.buffer = bytearray()

    @staticmethod
    def build_command_frame(command_word: int, payload: bytes = b"") -> bytes:
        intra = command_word.to_bytes(2, "little") + payload
        return CMD_HEADER + len(intra).to_bytes(2, "little") + intra + CMD_FOOTER

    def send_command(self, ser, command_word: int, payload: bytes = b"") -> bytes:
        frame = self.build_command_frame(command_word, payload)
        ser.write(frame)
        ser.flush()
        return frame

    def read_ack(self, ser, expected_command_word: int, timeout_s: float = 2.0) -> bytes:
        expected_ack_word = expected_command_word | 0x0100
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            chunk = ser.read(128)
            if not chunk:
                continue
            self.buffer.extend(chunk)
            while True:
                header_index = self.buffer.find(CMD_HEADER)
                if header_index < 0:
                    if len(self.buffer) > len(CMD_HEADER):
                        del self.buffer[:-len(CMD_HEADER)]
                    break
                if header_index > 0:
                    del self.buffer[:header_index]
                if len(self.buffer) < 10:
                    break
                data_len = int.from_bytes(self.buffer[4:6], "little")
                total_len = 4 + 2 + data_len + 4
                if len(self.buffer) < total_len:
                    break
                frame = bytes(self.buffer[:total_len])
                if frame[-4:] != CMD_FOOTER:
                    del self.buffer[0]
                    continue
                del self.buffer[:total_len]
                frame_data = frame[6:-4]
                if len(frame_data) < 4:
                    continue
                ack_word = int.from_bytes(frame_data[0:2], "little")
                status = int.from_bytes(frame_data[2:4], "little")
                if ack_word != expected_ack_word:
                    continue
                if status != 0x0000:
                    raise RuntimeError(
                        f"LD2410 command 0x{expected_command_word:04X} failed with status 0x{status:04X}"
                    )
                return frame
        raise TimeoutError(f"Timed out waiting for LD2410 ack 0x{expected_command_word:04X}")

    def configure_sensor(self, ser, engineering_mode: bool) -> tuple[list[bytes], list[bytes]]:
        tx_frames: list[bytes] = []
        ack_frames: list[bytes] = []
        ser.reset_input_buffer()
        tx_frames.append(self.send_command(ser, ENABLE_CONFIG, b"\x01\x00"))
        ack_frames.append(self.read_ack(ser, ENABLE_CONFIG))
        command = ENABLE_ENGINEERING if engineering_mode else DISABLE_ENGINEERING
        tx_frames.append(self.send_command(ser, command))
        ack_frames.append(self.read_ack(ser, command))
        tx_frames.append(self.send_command(ser, END_CONFIG))
        ack_frames.append(self.read_ack(ser, END_CONFIG))
        ser.reset_input_buffer()
        return tx_frames, ack_frames


def derive_wake_signal(report: dict[str, Any] | None, config: HLKDerivedConfig) -> bool:
    if not report:
        return False
    moving_distance = _int_value(report.get("moving_distance_cm"))
    moving_energy = _int_value(report.get("moving_energy"))
    return (
        bool(report.get("moving"))
        and config.wake_distance_min_cm <= moving_distance <= config.wake_distance_max_cm
        and moving_energy >= config.wake_moving_energy_min
    )


def derive_presence_candidate(report: dict[str, Any] | None, config: HLKDerivedConfig) -> bool:
    if not report:
        return False
    raw_presence = bool(report.get("presence"))
    detection_distance = _int_value(report.get("detection_distance_cm"))
    moving_distance = _int_value(report.get("moving_distance_cm"))
    still_distance = _int_value(report.get("still_distance_cm"))
    moving_energy = _int_value(report.get("moving_energy"))
    still_energy = _int_value(report.get("still_energy"))

    valid_detection = config.presence_distance_min_cm <= detection_distance <= config.presence_distance_max_cm
    moving_candidate = (
        config.presence_distance_min_cm <= moving_distance <= config.presence_distance_max_cm
        and moving_energy >= config.presence_moving_energy_min
    )
    still_candidate = (
        config.presence_distance_min_cm <= still_distance <= config.presence_distance_max_cm
        and still_energy >= config.presence_still_energy_min
    )
    return raw_presence or (valid_detection and (moving_candidate or still_candidate))


def apply_delay_off(
    state: DelayOffState,
    candidate: bool,
    *,
    now: float | None = None,
    delay_off_s: float,
) -> DelayOffState:
    if now is None:
        now = time.monotonic()
    delay_off_s = max(0.0, float(delay_off_s))

    if candidate:
        return DelayOffState(value=True, off_since=None)

    if not state.value:
        return DelayOffState(value=False, off_since=None)

    off_since = state.off_since if state.off_since is not None else now
    if delay_off_s <= 0 or now - off_since >= delay_off_s:
        return DelayOffState(value=False, off_since=None)
    return DelayOffState(value=True, off_since=off_since)


def derive_state_payload(
    report: dict[str, Any] | None,
    config: HLKDerivedConfig,
    *,
    stable_state: DelayOffState | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    payload = dict(report or {})
    wake = derive_wake_signal(payload, config)
    presence_candidate = derive_presence_candidate(payload, config)
    delay_state = apply_delay_off(
        stable_state or DelayOffState(),
        presence_candidate,
        now=now,
        delay_off_s=config.presence_delay_off_s,
    )
    payload["macro_movement"] = wake
    payload["wake"] = wake
    payload["presence_candidate"] = presence_candidate
    payload["presence_stable"] = delay_state.value
    payload["presence_stable_off_since"] = delay_state.off_since
    return payload


def _int_value(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
