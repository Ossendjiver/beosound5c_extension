from __future__ import annotations

import importlib
import json
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

SERVICES_DIR = Path(__file__).resolve().parents[3] / "services"
sys.path.insert(0, str(SERVICES_DIR))

pytestmark = pytest.mark.skipif(
    sys.version_info < (3, 10),
    reason="services/input.py uses Python 3.10+ typing syntax",
)


def _load_input_module(monkeypatch):
    sys.modules.pop("input", None)
    monkeypatch.setitem(sys.modules, "hid", types.ModuleType("hid"))
    return importlib.import_module("input")


def _mock_cfg(
    entity_id: str = "media_player.beosound_global_showing",
    ha_url: str = "http://ha.local:8123",
):
    values = {
        ("home_assistant", "url"): ha_url,
        ("showing", "entity_id"): entity_id,
    }

    def _cfg(*keys, default=None):
        return values.get(keys, default)

    return _cfg


class _FakeResponse:
    def __init__(self, status: int, payload=None, text: str = ""):
        self.status = status
        self._payload = payload
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return self._payload

    async def text(self):
        return self._text


class _FakeSession:
    def __init__(self, *, get_response: _FakeResponse | None = None, post_response: _FakeResponse | None = None):
        self._get_response = get_response
        self._post_response = post_response
        self.last_get = None
        self.last_post = None

    def get(self, url, *, headers=None):
        self.last_get = {"url": url, "headers": headers or {}}
        return self._get_response

    def post(self, url, *, headers=None, json=None):
        self.last_post = {"url": url, "headers": headers or {}, "json": json}
        return self._post_response


class _FakeRequest:
    def __init__(self, *, method: str = "GET", payload=None, json_error: Exception | None = None):
        self.method = method
        self._payload = payload
        self._json_error = json_error

    async def json(self):
        if self._json_error:
            raise self._json_error
        return self._payload


@pytest.mark.asyncio
async def test_handle_appletv_returns_showing_media_payload(monkeypatch):
    input_mod = _load_input_module(monkeypatch)
    monkeypatch.setattr(input_mod, "cfg", _mock_cfg())
    monkeypatch.delenv("HA_TOKEN", raising=False)

    session = _FakeSession(
        get_response=_FakeResponse(
            200,
            payload={
                "state": "paused",
                "attributes": {
                    "media_title": "Arrival",
                    "media_artist": "Hans Zimmer",
                    "media_album_name": "Dune OST",
                    "app_name": "Plex",
                    "friendly_name": "BeoSound Global Showing",
                    "entity_picture": "/api/media_player_proxy/image.jpg",
                    "supported_features": 12345,
                },
            },
        )
    )
    monkeypatch.setattr(input_mod, "get_http_session", AsyncMock(return_value=session))

    response = await input_mod.handle_appletv(_FakeRequest())
    body = json.loads(response.text)

    assert response.status == 200
    assert response.headers["Access-Control-Allow-Origin"] == "*"
    assert session.last_get == {
        "url": "http://ha.local:8123/api/states/media_player.beosound_global_showing",
        "headers": {},
    }
    assert body == {
        "entity_id": "media_player.beosound_global_showing",
        "title": "Arrival",
        "artist": "Hans Zimmer",
        "album": "Dune OST",
        "app_name": "Plex",
        "friendly_name": "BeoSound Global Showing",
        "artwork": "http://ha.local:8123/api/media_player_proxy/image.jpg",
        "state": "paused",
        "supported_features": 12345,
    }


@pytest.mark.asyncio
async def test_handle_appletv_command_forwards_transport_to_home_assistant(monkeypatch):
    input_mod = _load_input_module(monkeypatch)
    monkeypatch.setattr(input_mod, "cfg", _mock_cfg())
    monkeypatch.delenv("HA_TOKEN", raising=False)

    session = _FakeSession(post_response=_FakeResponse(200, text="ok"))
    monkeypatch.setattr(input_mod, "get_http_session", AsyncMock(return_value=session))

    response = await input_mod.handle_appletv_command(
        _FakeRequest(method="POST", payload={"command": "toggle"})
    )
    body = json.loads(response.text)

    assert response.status == 200
    assert response.headers["Access-Control-Allow-Origin"] == "*"
    assert session.last_post == {
        "url": "http://ha.local:8123/api/services/media_player/media_play_pause",
        "headers": {"Content-Type": "application/json"},
        "json": {"entity_id": "media_player.beosound_global_showing"},
    }
    assert body == {
        "status": "ok",
        "entity_id": "media_player.beosound_global_showing",
        "command": "toggle",
        "service": "media_play_pause",
    }
