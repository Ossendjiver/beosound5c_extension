#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any

import paho.mqtt.client as mqtt
import serial
from aiohttp import web

from lib.config import cfg, reload_config
from lib.correlation import correlation_middleware, install_logging
from lib.hlk_protocol import (
    CommandProtocol,
    DelayOffState,
    GATE_COUNT,
    HLKDerivedConfig,
    ReportParser,
    derive_state_payload,
)
from lib.watchdog import watchdog_loop

logger = install_logging("beo-hlk")

HLK_HTTP_PORT = 8784
DEFAULT_RECONNECT_DELAY_SECONDS = 5.0
DEFAULT_AVAILABILITY_TIMEOUT_SECONDS = 10.0
DEFAULT_PUBLISH_HEARTBEAT_SECONDS = 30.0
DEFAULT_SERIAL_TIMEOUT_SECONDS = 0.2
DEFAULT_MANUAL_OVERRIDE_TIMEOUT_SECONDS = 300.0
DEFAULT_MQTT_DISCOVERY_PREFIX = "homeassistant"
DEFAULT_MQTT_BASE_TOPIC = "beosound5c"


def _device_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", str(name or "").strip().lower())
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or "beosound5c"


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass(slots=True)
class MQTTConfig:
    host: str
    port: int = 1883
    username: str = ""
    password: str = ""
    discovery_prefix: str = DEFAULT_MQTT_DISCOVERY_PREFIX
    base_topic: str = DEFAULT_MQTT_BASE_TOPIC
    client_id: str = "beo-hlk"
    retain_discovery: bool = True
    qos: int = 1


@dataclass(slots=True)
class HLKServiceConfig:
    device_name: str
    device_slug: str
    enabled: bool = False
    port: str = "/dev/serial0"
    baudrate: int = 256000
    engineering_mode: bool = True
    reconnect_delay_s: float = DEFAULT_RECONNECT_DELAY_SECONDS
    availability_timeout_s: float = DEFAULT_AVAILABILITY_TIMEOUT_SECONDS
    publish_heartbeat_s: float = DEFAULT_PUBLISH_HEARTBEAT_SECONDS
    serial_timeout_s: float = DEFAULT_SERIAL_TIMEOUT_SECONDS
    publish_raw_frames: bool = True
    mqtt_discovery: bool = True
    entity_prefix: str = "bs5c"
    sensor_name: str = "BS5c Screen HLK"
    manual_override_timeout_s: float = DEFAULT_MANUAL_OVERRIDE_TIMEOUT_SECONDS
    mqtt: MQTTConfig = field(default_factory=lambda: MQTTConfig(host="homeassistant.local"))
    thresholds: HLKDerivedConfig = field(default_factory=HLKDerivedConfig)

    @property
    def state_topic(self) -> str:
        return f"{self.mqtt.base_topic}/{self.device_slug}/hlk/state"

    @property
    def availability_topic(self) -> str:
        return f"{self.mqtt.base_topic}/{self.device_slug}/hlk/availability"

    @property
    def serial_topic(self) -> str:
        return f"{self.mqtt.base_topic}/{self.device_slug}/hlk/serial"

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": [f"bs5c_hlk_{self.device_slug}"],
            "name": self.sensor_name,
            "manufacturer": "Hi-Link",
            "model": "LD2410",
        }


class SharedConfig:
    def __init__(self, initial: HLKServiceConfig) -> None:
        self._lock = threading.Lock()
        self._value = initial

    def get(self) -> HLKServiceConfig:
        with self._lock:
            return self._value

    def set(self, value: HLKServiceConfig) -> None:
        with self._lock:
            self._value = value


class RuntimeStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {
            "enabled": False,
            "connected": False,
            "available": False,
            "wake": False,
            "macro_movement": False,
            "presence_candidate": False,
            "presence_stable": False,
            "last_error": None,
            "last_report_at": 0.0,
            "last_report_timestamp": None,
            "serial_port": None,
            "baudrate": None,
        }

    def update(self, **values: Any) -> None:
        with self._lock:
            self._state.update(values)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            snap = dict(self._state)
        last_report_at = float(snap.get("last_report_at") or 0.0)
        snap["last_report_age_s"] = round(max(0.0, time.monotonic() - last_report_at), 1) if last_report_at else None
        return snap


class MQTTManager:
    def __init__(self, config_ref: SharedConfig, runtime: RuntimeStore) -> None:
        self.config_ref = config_ref
        self.runtime = runtime
        self.connected = threading.Event()
        self._callbacks: list[callable] = []
        cfg_obj = config_ref.get().mqtt
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cfg_obj.client_id)
        if cfg_obj.username:
            self.client.username_pw_set(cfg_obj.username, cfg_obj.password or None)
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self._started = False

    def add_on_connect(self, callback) -> None:
        self._callbacks.append(callback)

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        mqtt_cfg = self.config_ref.get().mqtt
        try:
            self.client.connect_async(mqtt_cfg.host, mqtt_cfg.port, keepalive=60)
            self.client.loop_start()
        except Exception as e:
            logger.warning("MQTT startup failed: %s", e)

    def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        try:
            self.client.loop_stop()
        finally:
            try:
                self.client.disconnect()
            except Exception:
                pass

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code == 0:
            self.connected.set()
            mqtt_cfg = self.config_ref.get().mqtt
            logger.info("MQTT connected -> %s:%s", mqtt_cfg.host, mqtt_cfg.port)
            for callback in list(self._callbacks):
                try:
                    callback()
                except Exception as e:
                    logger.warning("MQTT on-connect callback failed: %s", e)
        else:
            self.connected.clear()
            logger.warning("MQTT connect failed with code %s", reason_code)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        self.connected.clear()
        logger.warning("MQTT disconnected with code %s", reason_code)

    def publish(self, topic: str, payload: str, *, retain: bool = False) -> None:
        if not self._started:
            return
        try:
            cfg_obj = self.config_ref.get().mqtt
            self.client.publish(topic, payload=payload, qos=cfg_obj.qos, retain=retain)
        except Exception as e:
            logger.debug("MQTT publish failed for %s: %s", topic, e)

    def publish_json(self, topic: str, payload: dict[str, Any], *, retain: bool = False) -> None:
        self.publish(topic, json.dumps(payload, separators=(",", ":"), ensure_ascii=True), retain=retain)


class DiscoveryPublisher:
    def __init__(self, mqtt_manager: MQTTManager) -> None:
        self.mqtt = mqtt_manager

    def publish(self, config: HLKServiceConfig) -> None:
        if not config.mqtt_discovery:
            return
        prefix = config.mqtt.discovery_prefix
        state_topic = config.state_topic
        availability_topic = config.availability_topic
        device = config.device_info
        entity_prefix = config.entity_prefix
        entities = [
            self._binary(
                object_id=f"{entity_prefix}_macro",
                name="Macro Movement",
                value_template="{{ 'ON' if value_json.macro_movement else 'OFF' }}",
                device_class="motion",
            ),
            self._binary(
                object_id=f"{entity_prefix}_hlk_presence_stable",
                name="HLK Presence Stable",
                value_template="{{ 'ON' if value_json.presence_stable else 'OFF' }}",
                device_class="occupancy",
            ),
            self._binary(
                object_id=f"{entity_prefix}_hlk_presence",
                name="HLK Presence",
                value_template="{{ 'ON' if value_json.presence else 'OFF' }}",
                device_class="occupancy",
            ),
            self._binary(
                object_id=f"{entity_prefix}_hlk_moving",
                name="HLK Moving",
                value_template="{{ 'ON' if value_json.moving else 'OFF' }}",
                device_class="motion",
            ),
            self._binary(
                object_id=f"{entity_prefix}_hlk_still",
                name="HLK Still",
                value_template="{{ 'ON' if value_json.still else 'OFF' }}",
                device_class="occupancy",
            ),
            self._sensor(
                object_id=f"{entity_prefix}_hlk_target_state",
                name="HLK Target State",
                value_template="{{ value_json.target_state }}",
                icon="mdi:radar",
            ),
            self._sensor(
                object_id=f"{entity_prefix}_hlk_target_state_raw",
                name="HLK Target State Raw",
                value_template="{{ value_json.target_state_raw }}",
                entity_category="diagnostic",
            ),
            self._sensor(
                object_id=f"{entity_prefix}_hlk_detection_distance_cm",
                name="HLK Detection Distance",
                value_template="{{ value_json.detection_distance_cm }}",
                unit="cm",
                device_class="distance",
            ),
            self._sensor(
                object_id=f"{entity_prefix}_hlk_moving_distance_cm",
                name="HLK Moving Distance",
                value_template="{{ value_json.moving_distance_cm }}",
                unit="cm",
                device_class="distance",
            ),
            self._sensor(
                object_id=f"{entity_prefix}_hlk_still_distance_cm",
                name="HLK Still Distance",
                value_template="{{ value_json.still_distance_cm }}",
                unit="cm",
                device_class="distance",
            ),
            self._sensor(
                object_id=f"{entity_prefix}_hlk_moving_energy",
                name="HLK Moving Energy",
                value_template="{{ value_json.moving_energy }}",
                unit="%",
                state_class="measurement",
            ),
            self._sensor(
                object_id=f"{entity_prefix}_hlk_still_energy",
                name="HLK Still Energy",
                value_template="{{ value_json.still_energy }}",
                unit="%",
                state_class="measurement",
            ),
            self._sensor(
                object_id=f"{entity_prefix}_hlk_payload_length",
                name="HLK Payload Length",
                value_template="{{ value_json.payload_length }}",
                entity_category="diagnostic",
            ),
            self._sensor(
                object_id=f"{entity_prefix}_hlk_light_level",
                name="HLK Light Level",
                value_template="{{ value_json.light_level if value_json.light_level is not none else '' }}",
                entity_category="diagnostic",
            ),
        ]

        for index in range(GATE_COUNT):
            entities.append(
                self._sensor(
                    object_id=f"{entity_prefix}_hlk_g{index}_move_energy",
                    name=f"HLK G{index} Move Energy",
                    value_template=(
                        "{{ value_json.moving_gate_energies[" + str(index) + "] "
                        f"if value_json.moving_gate_energies|length > {index} else '' }}"
                    ),
                    unit="%",
                    state_class="measurement",
                    entity_category="diagnostic",
                )
            )
            entities.append(
                self._sensor(
                    object_id=f"{entity_prefix}_hlk_g{index}_still_energy",
                    name=f"HLK G{index} Still Energy",
                    value_template=(
                        "{{ value_json.still_gate_energies[" + str(index) + "] "
                        f"if value_json.still_gate_energies|length > {index} else '' }}"
                    ),
                    unit="%",
                    state_class="measurement",
                    entity_category="diagnostic",
                )
            )

        for entity in entities:
            component = entity.pop("component")
            object_id = entity.pop("object_id")
            entity.update(
                {
                    "stat_t": state_topic,
                    "avty_t": availability_topic,
                    "pl_avail": "online",
                    "pl_not_avail": "offline",
                    "dev": device,
                }
            )
            topic = f"{prefix}/{component}/{object_id}/config"
            self.mqtt.publish_json(topic, entity, retain=config.mqtt.retain_discovery)

    @staticmethod
    def _binary(*, object_id: str, name: str, value_template: str, device_class: str) -> dict[str, Any]:
        return {
            "component": "binary_sensor",
            "object_id": object_id,
            "default_entity_id": f"binary_sensor.{object_id}",
            "name": name,
            "uniq_id": object_id,
            "dev_cla": device_class,
            "pl_on": "ON",
            "pl_off": "OFF",
            "val_tpl": value_template,
        }

    @staticmethod
    def _sensor(
        *,
        object_id: str,
        name: str,
        value_template: str,
        unit: str | None = None,
        device_class: str | None = None,
        state_class: str | None = None,
        entity_category: str | None = None,
        icon: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "component": "sensor",
            "object_id": object_id,
            "default_entity_id": f"sensor.{object_id}",
            "name": name,
            "uniq_id": object_id,
            "val_tpl": value_template,
        }
        if unit:
            payload["unit_of_meas"] = unit
        if device_class:
            payload["dev_cla"] = device_class
        if state_class:
            payload["stat_cla"] = state_class
        if entity_category:
            payload["ent_cat"] = entity_category
        if icon:
            payload["ic"] = icon
        return payload


class ReconnectRequested(RuntimeError):
    pass


class HLKWorker(threading.Thread):
    def __init__(
        self,
        config_ref: SharedConfig,
        runtime: RuntimeStore,
        mqtt_manager: MQTTManager,
        discovery: DiscoveryPublisher,
        stop_event: threading.Event,
    ) -> None:
        super().__init__(name="beo-hlk-reader", daemon=True)
        self.config_ref = config_ref
        self.runtime = runtime
        self.mqtt = mqtt_manager
        self.discovery = discovery
        self.stop_event = stop_event
        self.reconnect_event = threading.Event()
        self.command = CommandProtocol()
        self.parser = ReportParser()
        self._stable_state = DelayOffState()
        self._last_state_json = ""
        self._last_publish_at = 0.0

    def request_reconnect(self) -> None:
        self.reconnect_event.set()

    def run(self) -> None:
        last_availability = None
        while not self.stop_event.is_set():
            config = self.config_ref.get()
            self.runtime.update(enabled=config.enabled)
            if not config.enabled:
                if last_availability != "offline":
                    self.mqtt.publish(config.availability_topic, "offline", retain=True)
                    last_availability = "offline"
                self.runtime.update(
                    connected=False,
                    available=False,
                    wake=False,
                    macro_movement=False,
                    presence_candidate=False,
                    presence_stable=False,
                    last_error=None,
                )
                self.stop_event.wait(2.0)
                continue

            try:
                self._run_serial_session(config)
                last_availability = "online"
            except ReconnectRequested:
                logger.info("HLK reconnect requested")
            except Exception as e:
                self.runtime.update(
                    connected=False,
                    available=False,
                    last_error=str(e),
                )
                logger.warning("HLK session failed: %s", e)
                self.mqtt.publish(config.availability_topic, "offline", retain=True)
                last_availability = "offline"
                self.stop_event.wait(max(1.0, config.reconnect_delay_s))

    def _run_serial_session(self, config: HLKServiceConfig) -> None:
        logger.info("Opening HLK serial -> %s @ %d", config.port, config.baudrate)
        self.parser = ReportParser()
        self.command = CommandProtocol()
        self.reconnect_event.clear()
        self._last_state_json = ""

        with serial.Serial(
            config.port,
            config.baudrate,
            timeout=config.serial_timeout_s,
        ) as ser:
            self.runtime.update(
                connected=True,
                available=False,
                last_error=None,
                serial_port=config.port,
                baudrate=config.baudrate,
            )
            self.mqtt.publish(config.availability_topic, "online", retain=True)
            self.discovery.publish(config)
            tx_frames, ack_frames = self.command.configure_sensor(ser, config.engineering_mode)
            if config.publish_raw_frames:
                for frame in tx_frames:
                    self._publish_serial_frame(config, direction="tx", frame_family="command", frame=frame)
                for frame in ack_frames:
                    self._publish_serial_frame(config, direction="rx", frame_family="ack", frame=frame)

            last_engineering_retry = 0.0
            while not self.stop_event.is_set():
                if self.reconnect_event.is_set():
                    raise ReconnectRequested()

                chunk = ser.read(256)
                if not chunk:
                    self._publish_heartbeat_if_due(config)
                    continue

                reports = self.parser.feed(chunk)
                for report in reports:
                    now = time.monotonic()
                    if config.publish_raw_frames and report.get("frame_hex"):
                        self._publish_serial_frame(
                            config,
                            direction="rx",
                            frame_family="report",
                            frame=bytes.fromhex(report["frame_hex"]),
                        )
                    if config.engineering_mode and report.get("report_type") != "engineering":
                        if now - last_engineering_retry >= 10.0:
                            last_engineering_retry = now
                            logger.warning("HLK left engineering mode; re-enabling")
                            tx_frames, ack_frames = self.command.configure_sensor(ser, True)
                            if config.publish_raw_frames:
                                for frame in tx_frames:
                                    self._publish_serial_frame(config, direction="tx", frame_family="command", frame=frame)
                                for frame in ack_frames:
                                    self._publish_serial_frame(config, direction="rx", frame_family="ack", frame=frame)

                    state_payload = derive_state_payload(
                        report,
                        config.thresholds,
                        stable_state=self._stable_state,
                        now=now,
                    )
                    self._stable_state = DelayOffState(
                        value=bool(state_payload.get("presence_stable")),
                        off_since=state_payload.get("presence_stable_off_since"),
                    )
                    state_payload.update(
                        {
                            "available": True,
                            "connected": True,
                            "entity_prefix": config.entity_prefix,
                            "manual_override_timeout_s": config.manual_override_timeout_s,
                            "serial_port": config.port,
                            "baudrate": config.baudrate,
                            "last_report_monotonic": now,
                        }
                    )
                    self.runtime.update(
                        **state_payload,
                        last_error=None,
                        last_report_at=now,
                        last_report_timestamp=state_payload.get("timestamp"),
                    )
                    self._publish_state(config, self.runtime.snapshot(), force=False)

                self._publish_heartbeat_if_due(config)

    def _publish_serial_frame(
        self,
        config: HLKServiceConfig,
        *,
        direction: str,
        frame_family: str,
        frame: bytes,
    ) -> None:
        self.mqtt.publish_json(
            config.serial_topic,
            {
                "timestamp": int(time.time()),
                "direction": direction,
                "frame_family": frame_family,
                "hex": frame.hex(),
                "bytes": len(frame),
            },
            retain=False,
        )

    def _publish_state(self, config: HLKServiceConfig, payload: dict[str, Any], *, force: bool) -> None:
        state_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
        if not force and state_json == self._last_state_json:
            return
        self._last_state_json = state_json
        self._last_publish_at = time.monotonic()
        self.mqtt.publish(config.state_topic, state_json, retain=True)

    def _publish_heartbeat_if_due(self, config: HLKServiceConfig) -> None:
        now = time.monotonic()
        runtime = self.runtime.snapshot()
        last_report_at = float(runtime.get("last_report_at") or 0.0)
        if last_report_at and now - last_report_at > config.availability_timeout_s:
            self.runtime.update(available=False)
        if now - self._last_publish_at < config.publish_heartbeat_s:
            return
        self._publish_state(config, self.runtime.snapshot(), force=True)


def _load_service_config() -> HLKServiceConfig:
    raw = cfg("hlk", default={}) or {}
    device_name = str(cfg("device", default="BeoSound5c") or "BeoSound5c")
    device_slug = _device_slug(device_name)
    entity_prefix = str(raw.get("entity_prefix") or device_slug).strip() or device_slug
    transport_cfg = cfg("transport", default={}) or {}
    mqtt_host = str(transport_cfg.get("mqtt_broker") or "homeassistant.local")
    mqtt_port = _coerce_int(transport_cfg.get("mqtt_port"), 1883)
    mqtt_cfg = MQTTConfig(
        host=mqtt_host,
        port=mqtt_port,
        username=os.getenv("MQTT_USER", ""),
        password=os.getenv("MQTT_PASSWORD", ""),
        discovery_prefix=str(raw.get("mqtt_discovery_prefix") or DEFAULT_MQTT_DISCOVERY_PREFIX),
        base_topic=str(raw.get("mqtt_base_topic") or DEFAULT_MQTT_BASE_TOPIC),
        client_id=f"beo-hlk-{device_slug}",
    )
    thresholds = HLKDerivedConfig(
        wake_distance_min_cm=_coerce_int(raw.get("wake_distance_min_cm"), 25),
        wake_distance_max_cm=_coerce_int(raw.get("wake_distance_max_cm"), 150),
        wake_moving_energy_min=_coerce_int(raw.get("wake_moving_energy_min"), 18),
        presence_distance_min_cm=_coerce_int(raw.get("presence_distance_min_cm"), 25),
        presence_distance_max_cm=_coerce_int(raw.get("presence_distance_max_cm"), 450),
        presence_moving_energy_min=_coerce_int(raw.get("presence_moving_energy_min"), 18),
        presence_still_energy_min=_coerce_int(raw.get("presence_still_energy_min"), 12),
        presence_delay_off_s=_coerce_float(raw.get("presence_delay_off_s"), 20.0),
    )
    return HLKServiceConfig(
        device_name=device_name,
        device_slug=device_slug,
        enabled=_truthy(raw.get("enabled"), False),
        port=str(raw.get("port") or "/dev/serial0"),
        baudrate=_coerce_int(raw.get("baudrate"), 256000),
        engineering_mode=_truthy(raw.get("engineering_mode"), True),
        reconnect_delay_s=max(1.0, _coerce_float(raw.get("reconnect_delay_s"), DEFAULT_RECONNECT_DELAY_SECONDS)),
        availability_timeout_s=max(1.0, _coerce_float(raw.get("availability_timeout_s"), DEFAULT_AVAILABILITY_TIMEOUT_SECONDS)),
        publish_heartbeat_s=max(5.0, _coerce_float(raw.get("publish_heartbeat_s"), DEFAULT_PUBLISH_HEARTBEAT_SECONDS)),
        serial_timeout_s=max(0.05, _coerce_float(raw.get("serial_timeout_s"), DEFAULT_SERIAL_TIMEOUT_SECONDS)),
        publish_raw_frames=_truthy(raw.get("publish_raw_frames"), True),
        mqtt_discovery=_truthy(raw.get("mqtt_discovery"), True),
        entity_prefix=entity_prefix,
        sensor_name=str(raw.get("sensor_name") or f"{device_name} Screen HLK"),
        manual_override_timeout_s=max(0.0, _coerce_float(raw.get("manual_override_timeout_s"), DEFAULT_MANUAL_OVERRIDE_TIMEOUT_SECONDS)),
        mqtt=mqtt_cfg,
        thresholds=thresholds,
    )


def _config_snapshot(config: HLKServiceConfig) -> dict[str, Any]:
    threshold_map = asdict(config.thresholds)
    threshold_map["presence_delay_off_s"] = round(float(threshold_map["presence_delay_off_s"]), 1)
    return {
        "enabled": config.enabled,
        "port": config.port,
        "baudrate": config.baudrate,
        "engineering_mode": config.engineering_mode,
        "reconnect_delay_s": config.reconnect_delay_s,
        "availability_timeout_s": config.availability_timeout_s,
        "publish_heartbeat_s": config.publish_heartbeat_s,
        "serial_timeout_s": config.serial_timeout_s,
        "publish_raw_frames": config.publish_raw_frames,
        "mqtt_discovery": config.mqtt_discovery,
        "entity_prefix": config.entity_prefix,
        "sensor_name": config.sensor_name,
        "manual_override_timeout_s": config.manual_override_timeout_s,
        "mqtt_discovery_prefix": config.mqtt.discovery_prefix,
        "mqtt_base_topic": config.mqtt.base_topic,
        **threshold_map,
    }


CONFIG_REF = SharedConfig(_load_service_config())
RUNTIME = RuntimeStore()
MQTT = MQTTManager(CONFIG_REF, RUNTIME)
DISCOVERY = DiscoveryPublisher(MQTT)
STOP_EVENT = threading.Event()
WORKER = HLKWorker(CONFIG_REF, RUNTIME, MQTT, DISCOVERY, STOP_EVENT)


def _publish_connectivity_state() -> None:
    config = CONFIG_REF.get()
    DISCOVERY.publish(config)
    if config.enabled:
        MQTT.publish(config.availability_topic, "online", retain=True)
        MQTT.publish_json(config.state_topic, RUNTIME.snapshot(), retain=True)


async def handle_state(request: web.Request) -> web.Response:
    config = CONFIG_REF.get()
    payload = {
        "status": "ok",
        "config": _config_snapshot(config),
        "runtime": RUNTIME.snapshot(),
    }
    return web.json_response(payload)


async def handle_health(request: web.Request) -> web.Response:
    runtime = RUNTIME.snapshot()
    status = 200 if runtime.get("connected") or not CONFIG_REF.get().enabled else 503
    return web.json_response({"status": "ok" if status == 200 else "degraded", "runtime": runtime}, status=status)


async def handle_reload(request: web.Request) -> web.Response:
    reconnect = False
    if request.can_read_body:
        try:
            body = await request.json()
            reconnect = _truthy((body or {}).get("reconnect"), False)
        except Exception:
            reconnect = False

    previous = CONFIG_REF.get()
    reload_config()
    current = _load_service_config()
    CONFIG_REF.set(current)
    RUNTIME.update(enabled=current.enabled)
    DISCOVERY.publish(current)

    reconnect_needed = reconnect or any(
        [
            previous.enabled != current.enabled,
            previous.port != current.port,
            previous.baudrate != current.baudrate,
            previous.engineering_mode != current.engineering_mode,
        ]
    )
    if reconnect_needed:
        WORKER.request_reconnect()

    return web.json_response(
        {
            "status": "ok",
            "reconnect": reconnect_needed,
            "config": _config_snapshot(current),
            "runtime": RUNTIME.snapshot(),
        }
    )


async def main() -> None:
    MQTT.add_on_connect(_publish_connectivity_state)
    MQTT.start()
    WORKER.start()

    app = web.Application(middlewares=[correlation_middleware])
    app.router.add_get("/state", handle_state)
    app.router.add_get("/health", handle_health)
    app.router.add_post("/reload", handle_reload)
    app.router.add_options("/reload", handle_reload)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", HLK_HTTP_PORT)
    await site.start()
    logger.info("HLK HTTP listening on http://0.0.0.0:%d", HLK_HTTP_PORT)

    stop_waiter = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_waiter.set)
        except NotImplementedError:
            pass

    asyncio.create_task(watchdog_loop())
    try:
        await stop_waiter.wait()
    finally:
        STOP_EVENT.set()
        WORKER.request_reconnect()
        if WORKER.is_alive():
            WORKER.join(timeout=5)
        MQTT.stop()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
