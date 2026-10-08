"""Transport follows the configured fallback only when no direct source owns it."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from test_router import make_router


@pytest.mark.asyncio
@pytest.mark.parametrize("action,command", [
    ("go", "toggle"), ("play", "play"), ("pause", "pause"), ("stop", "stop"),
    ("left", "previous"), ("right", "next"), ("up", "next"),
    ("down", "previous"), ("next", "next"), ("prev", "previous"),
])
async def test_idle_transport_uses_configured_fallback(monkeypatch, action, command):
    router = make_router()
    import router as router_mod
    values = {
        ("showing", "exclusive_playing_fallback"): True,
        ("showing", "entity_id"): "media_player.display_target",
    }
    monkeypatch.setattr(router_mod, "cfg", lambda *keys, default=None: values.get(keys, default))
    router._handle_audio = True
    router.registry = MagicMock(active_source=None)
    router._session = MagicMock()
    response = MagicMock(status=200)
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)
    router._session.post.return_value = response
    router._forward_to_source = AsyncMock()

    await router.route_event({"device_type": "Audio", "action": action})

    router._session.post.assert_called_once()
    assert router._session.post.call_args.args[0].endswith("/appletv/command")
    assert router._session.post.call_args.kwargs["json"] == {"command": command}
    router._forward_to_source.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_source_keeps_priority_over_fallback(monkeypatch):
    router = make_router()
    import router as router_mod
    monkeypatch.setattr(router_mod, "cfg", lambda *keys, default=None: True)
    router._handle_audio = True
    source = MagicMock(id="direct", state="playing", handles={"go"})
    router.registry = MagicMock(active_source=source)
    router._session = MagicMock()
    router._forward_to_source = AsyncMock()

    await router.route_event({"device_type": "Audio", "action": "go"})

    router._forward_to_source.assert_awaited_once()
    router._session.post.assert_not_called()


@pytest.mark.asyncio
async def test_failed_fallback_does_not_resume_another_player(monkeypatch):
    router = make_router()
    import router as router_mod
    monkeypatch.setattr(router_mod, "cfg", lambda *keys, default=None: True)
    router._handle_audio = True
    router.registry = MagicMock(active_source=None)
    router._session = MagicMock()
    response = MagicMock(status=503)
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)
    router._session.post.return_value = response
    router._forward_to_source = AsyncMock()

    await router.route_event({"device_type": "Audio", "action": "go"})

    router._session.post.assert_called_once()
    router._forward_to_source.assert_not_awaited()
