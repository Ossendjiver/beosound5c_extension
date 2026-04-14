"""Tests for correlation middleware: ID propagation + slow-handler warning."""

import asyncio
import logging

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from lib import correlation
from lib.correlation import HEADER, correlation_middleware


@pytest.fixture
def app():
    async def fast(request):
        return web.Response(text="ok")

    async def slow(request):
        # Exceed the threshold by enough to be stable on loaded CI.
        await asyncio.sleep((correlation._SLOW_HANDLER_MS + 200) / 1000)
        return web.Response(text="slow")

    async def boom(request):
        raise web.HTTPInternalServerError(text="boom")

    a = web.Application(middlewares=[correlation_middleware])
    a.router.add_get("/fast", fast)
    a.router.add_get("/slow", slow)
    a.router.add_get("/boom", boom)
    return a


async def _client(app):
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_correlation_id_header_roundtrip(app):
    client = await _client(app)
    try:
        resp = await client.get("/fast", headers={HEADER: "abc12"})
        assert resp.status == 200
        assert resp.headers[HEADER] == "abc12"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_correlation_id_generated_when_missing(app):
    client = await _client(app)
    try:
        resp = await client.get("/fast")
        assert resp.status == 200
        assert len(resp.headers[HEADER]) >= 1  # some id assigned
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_slow_handler_logs_warning(app, caplog):
    client = await _client(app)
    try:
        with caplog.at_level(logging.WARNING, logger="beo.latency"):
            resp = await client.get("/slow")
        assert resp.status == 200
        msgs = [r.message for r in caplog.records if r.name == "beo.latency"]
        assert any("slow handler" in m and "/slow" in m for m in msgs), msgs
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_fast_handler_does_not_log_warning(app, caplog):
    client = await _client(app)
    try:
        with caplog.at_level(logging.WARNING, logger="beo.latency"):
            resp = await client.get("/fast")
        assert resp.status == 200
        assert not [r for r in caplog.records if r.name == "beo.latency"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_websocket_handler_does_not_log_slow_warning(caplog):
    """Long-lived WS handlers must not trigger the slow-handler warning.

    Regression: on first deploy the middleware flagged ``GET /router/ws``
    as taking 330 seconds simply because the client stayed connected.
    """
    async def ws_handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        # Stay "connected" long enough to clearly exceed the threshold.
        await asyncio.sleep((correlation._SLOW_HANDLER_MS + 200) / 1000)
        await ws.close()
        return ws

    a = web.Application(middlewares=[correlation_middleware])
    a.router.add_get("/ws", ws_handler)
    client = await _client(a)
    try:
        with caplog.at_level(logging.WARNING, logger="beo.latency"):
            async with client.ws_connect("/ws") as ws:
                async for _msg in ws:
                    break
        assert not [
            r for r in caplog.records if r.name == "beo.latency"
        ], "WS handler should not be flagged as slow"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_middleware_survives_handler_exception(app):
    # Raising HTTPException bubbles past our ``return`` and aiohttp builds
    # a fresh error response outside our scope — no correlation header on
    # the wire, but the middleware itself must not crash.
    client = await _client(app)
    try:
        resp = await client.get("/boom")
        assert resp.status == 500
    finally:
        await client.close()
