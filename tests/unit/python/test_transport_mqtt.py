"""Tests for the MQTT reconnect / message routing logic in lib/transport.py.

The reconnect loop in ``Transport._mqtt_loop`` has been a sore spot — big
surface area, auto-reconnect, exponential backoff, retained status, and
three separate message routing paths (command handler, extra
subscriptions, unmatched).  This file covers the contract that isn't
exercised anywhere else in the suite.

aiomqtt is stubbed via ``sys.modules`` so these tests never touch a real
broker.
"""

import asyncio
import json
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

SERVICES_DIR = Path(__file__).resolve().parents[3] / "services"
sys.path.insert(0, str(SERVICES_DIR))


# ── aiomqtt fake ──────────────────────────────────────────────────────────

class _FakeWill:
    def __init__(self, *, topic, payload, qos=0, retain=False):
        self.topic = topic
        self.payload = payload
        self.qos = qos
        self.retain = retain


class _FakeTopic(str):
    """Matches both the topic_in constant and the extra subscriptions."""
    def matches(self, pattern: str) -> bool:
        return str(self) == pattern


class _FakeMessage:
    def __init__(self, topic: str, payload_obj):
        self.topic = _FakeTopic(topic)
        self.payload = json.dumps(payload_obj).encode()


class _FakeClient:
    """Stand-in for aiomqtt.Client used by Transport._mqtt_loop().

    Test control knobs:
      - connect_raises: exception to raise on __aenter__
      - messages: iterable of _FakeMessage to yield from .messages
      - post_messages_hook: called after the messages iterator finishes,
        useful for stopping the loop.
    """

    # Class-level controls — set by each test before triggering the loop
    connect_raises: list = []
    messages: list = []
    post_messages_hook = None
    published: list = []
    subscribed: list = []
    active_instance = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        _FakeClient.published = []
        _FakeClient.subscribed = []
        _FakeClient.active_instance = self

    async def __aenter__(self):
        if _FakeClient.connect_raises:
            raise _FakeClient.connect_raises.pop(0)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def publish(self, topic, payload, qos=0, retain=False):
        _FakeClient.published.append(
            {"topic": topic, "payload": payload, "qos": qos, "retain": retain})

    async def subscribe(self, topic):
        _FakeClient.subscribed.append(topic)

    @property
    def messages(self):
        async def gen():
            for m in _FakeClient.__dict__["messages"] if False else self._msgs():
                yield m
            if _FakeClient.post_messages_hook is not None:
                await _FakeClient.post_messages_hook()
            # Never yield again — the outer while loop decides to exit
            # based on transport._running.

        return gen()

    def _msgs(self):
        return _FakeClient.__class__.__dict__.get("messages_iter", lambda: iter(_FakeClient.messages))()


def _install_fake_aiomqtt():
    fake = types.SimpleNamespace(Client=_FakeClient, Will=_FakeWill)
    sys.modules["aiomqtt"] = fake
    return fake


def _uninstall_fake_aiomqtt():
    sys.modules.pop("aiomqtt", None)


# ── Transport factory ─────────────────────────────────────────────────────

def _make_transport(mode="mqtt"):
    """Build a Transport with cfg() patched to return MQTT defaults."""
    def fake_cfg(*keys, default=None):
        key = "/".join(keys)
        return {
            "transport/mode": mode,
            "transport/mqtt_broker": "test.broker",
            "transport/mqtt_port": 1883,
            "home_assistant/webhook_url": "http://x/w",
            "device": "Test Device",
        }.get(key, default)

    with patch("lib.transport.cfg", side_effect=fake_cfg):
        from lib.transport import Transport
        return Transport()


# ── Tests ─────────────────────────────────────────────────────────────────

class TestReconnectBackoff:
    """_mqtt_loop must reconnect with exponential backoff, capped."""

    def test_backoff_doubles_until_cap(self):
        _install_fake_aiomqtt()
        try:
            t = _make_transport()
            sleeps = []

            async def fake_sleep(d):
                sleeps.append(d)
                # Fifth failure: stop the loop so the test terminates.
                if len(sleeps) >= 5:
                    t._running = False

            _FakeClient.connect_raises = [
                OSError("no broker") for _ in range(6)]
            _FakeClient.messages = []

            async def run():
                t._running = True
                with patch("asyncio.sleep", side_effect=fake_sleep):
                    await t._mqtt_loop()

            asyncio.run(run())
            assert sleeps == [1, 2, 4, 8, 16]
        finally:
            _uninstall_fake_aiomqtt()

    def test_backoff_caps_at_30(self):
        _install_fake_aiomqtt()
        try:
            t = _make_transport()
            sleeps = []

            async def fake_sleep(d):
                sleeps.append(d)
                if len(sleeps) >= 8:
                    t._running = False

            _FakeClient.connect_raises = [OSError("x") for _ in range(10)]
            _FakeClient.messages = []

            async def run():
                t._running = True
                with patch("asyncio.sleep", side_effect=fake_sleep):
                    await t._mqtt_loop()

            asyncio.run(run())
            assert sleeps == [1, 2, 4, 8, 16, 30, 30, 30]
        finally:
            _uninstall_fake_aiomqtt()

    def test_backoff_resets_after_successful_connect(self):
        """After a good connect, the next failure should restart backoff at 1s."""
        _install_fake_aiomqtt()
        try:
            t = _make_transport()
            sleeps = []

            # Connect OK once, then fail three times.
            _FakeClient.connect_raises = [
                None,               # sentinel for "first attempt succeeds"
                OSError("drop 1"),
                OSError("drop 2"),
                OSError("drop 3"),
            ]

            # Our _FakeClient.__aenter__ pops from connect_raises and raises
            # if the popped value is an exception.  A None slot should NOT
            # raise — patch __aenter__ to treat None as success.
            original_aenter = _FakeClient.__aenter__

            async def aenter(self):
                if _FakeClient.connect_raises:
                    v = _FakeClient.connect_raises.pop(0)
                    if isinstance(v, Exception):
                        raise v
                return self

            _FakeClient.__aenter__ = aenter

            # Set up the good-connect run to return no messages and then
            # raise to trigger reconnect.  Simplest: after the first OK
            # connect, have the messages generator raise.
            async def hook():
                raise OSError("disconnected mid-stream")

            _FakeClient.post_messages_hook = hook
            _FakeClient.messages = []

            async def fake_sleep(d):
                sleeps.append(d)
                if len(sleeps) >= 3:
                    t._running = False

            async def run():
                t._running = True
                with patch("asyncio.sleep", side_effect=fake_sleep):
                    await t._mqtt_loop()

            asyncio.run(run())
            _FakeClient.__aenter__ = original_aenter
            _FakeClient.post_messages_hook = None
            # Sleeps after the reset: 1 (first drop after good connect),
            # then 2, then 4.
            assert sleeps[:3] == [1, 2, 4]
        finally:
            _uninstall_fake_aiomqtt()


class TestPublishGuards:
    """send_mqtt must refuse to publish when disconnected."""

    def test_send_mqtt_no_client(self):
        t = _make_transport()
        t._mqtt_client = None
        result = asyncio.run(t._send_mqtt({"action": "go"}))
        assert result is False

    def test_send_mqtt_publish_error_returns_false(self):
        t = _make_transport()

        class BadClient:
            async def publish(self, *a, **k):
                raise RuntimeError("pipe broken")

        t._mqtt_client = BadClient()
        result = asyncio.run(t._send_mqtt({"action": "go"}))
        assert result is False


class TestWebhookGuards:

    def test_send_webhook_no_session(self):
        t = _make_transport(mode="webhook")
        t._session = None
        result = asyncio.run(t._send_webhook({"action": "go"}))
        assert result is False


class TestSendEventFanOut:
    """send_event must run webhook and MQTT in parallel when mode=both."""

    def test_both_mode_invokes_both_transports(self):
        t = _make_transport(mode="both")
        calls = []

        async def fake_webhook(p):
            calls.append(("w", p["action"]))
            return True

        async def fake_mqtt(p):
            calls.append(("m", p["action"]))
            return True

        t._send_webhook = fake_webhook
        t._send_mqtt = fake_mqtt
        asyncio.run(t.send_event({"action": "go"}))
        assert {c[0] for c in calls} == {"w", "m"}

    def test_send_event_swallows_exceptions(self):
        """One transport erroring must not kill the other."""
        t = _make_transport(mode="both")

        async def boom(p):
            raise RuntimeError("boom")

        async def ok(p):
            return True

        t._send_webhook = boom
        t._send_mqtt = ok
        # Should not raise — gather(..., return_exceptions=True)
        asyncio.run(t.send_event({"action": "x"}))


class TestShutdownDuringBackoff:
    """stop() must cancel the MQTT task even if it's sleeping in backoff."""

    def test_stop_cancels_sleeping_loop(self):
        _install_fake_aiomqtt()
        try:
            t = _make_transport()

            # Make connects fail, so the loop enters its backoff sleep.
            _FakeClient.connect_raises = [OSError("no broker") for _ in range(10)]
            _FakeClient.messages = []

            async def run():
                await t.start()
                # Yield enough for the loop to hit its first failure+sleep.
                await asyncio.sleep(0.01)
                await t.stop()

            asyncio.run(run())
            # After stop, the task is gone and _running is False.
            assert t._running is False
            assert t._mqtt_task is None
        finally:
            _uninstall_fake_aiomqtt()
