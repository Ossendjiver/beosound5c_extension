from __future__ import annotations

"""Correlation ID support for cross-service request tracing.

Generates a short unique ID at each entry point (HTTP request, HID event)
and carries it through the call chain via contextvars.  A logging filter
injects the ID into every log record so a single user action can be traced
across router, sources, and player services.

Usage in a service::

    from lib.correlation import install_logging, correlation_middleware

    install_logging("beo-router")           # replaces basicConfig
    app = web.Application(middlewares=[correlation_middleware])

For outgoing HTTP requests, add the header::

    from lib.correlation import correlation_headers
    session.post(url, headers=correlation_headers())
"""

import logging
import os
import time
from contextvars import ContextVar

from aiohttp import web

# Per-request latency warning threshold.  Handlers that exceed this are
# almost always doing sync I/O on the event loop (subprocess, requests,
# blocking file reads).  See commits 27cb774, ddd02ea.
_SLOW_HANDLER_MS = 250

# Short base-36 ID derived from monotonic time + PID for uniqueness
_cid: ContextVar[str] = ContextVar("cid", default="-")

HEADER = "X-Correlation-ID"


def new_id() -> str:
    """Generate a short (5-char) correlation ID."""
    # Combine monotonic ns with PID to avoid collisions across processes
    raw = int(time.monotonic_ns()) ^ (os.getpid() << 16)
    # Base-36 encode, take last 5 chars
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    result = []
    raw = abs(raw)
    while raw:
        result.append(chars[raw % 36])
        raw //= 36
    return "".join(result[-5:]) or "00000"


def set_id(cid: str | None = None) -> str:
    """Set the correlation ID for the current context.  Returns the ID."""
    cid = cid or new_id()
    _cid.set(cid)
    return cid


def get_id() -> str:
    return _cid.get()


def correlation_headers() -> dict[str, str]:
    """Return headers dict with the current correlation ID."""
    cid = _cid.get()
    if cid and cid != "-":
        return {HEADER: cid}
    return {}


class _CorrelationFilter(logging.Filter):
    def filter(self, record):
        record.cid = _cid.get()
        return True


@web.middleware
async def correlation_middleware(request: web.Request, handler):
    """aiohttp middleware: correlation ID + slow-handler latency warning.

    Sets the correlation ID from the inbound ``X-Correlation-ID`` header
    (or generates one), runs the handler, and logs a WARN if the handler
    took longer than ``_SLOW_HANDLER_MS``.  Slow handlers almost always
    indicate a blocking sync call on the event loop — the log line names
    the route so the culprit is easy to find.
    """
    cid = request.headers.get(HEADER) or new_id()
    set_id(cid)
    start = time.monotonic()
    response = None
    # Long-lived upgraded connections (WebSockets, SSE) stay open for
    # minutes — their "elapsed" is the session length, not a latency
    # signal.  Skip the latency warning for them.
    is_upgraded = (
        request.headers.get("Upgrade", "").lower() == "websocket"
    )
    try:
        response = await handler(request)
        return response
    finally:
        elapsed_ms = (time.monotonic() - start) * 1000
        if not is_upgraded and elapsed_ms > _SLOW_HANDLER_MS:
            route = request.match_info.route.resource
            route_name = (
                route.canonical if route is not None else request.path
            )
            # Slow here means "exceeds SLA", not necessarily "blocked the
            # loop".  The loop_monitor.py detector is the authority on
            # event-loop stalls; we just flag the request latency so
            # operators can correlate user-visible lag with a specific
            # handler + correlation ID.
            logging.getLogger("beo.latency").warning(
                "slow handler %s %s took %.0fms (threshold %dms)",
                request.method, route_name, elapsed_ms, _SLOW_HANDLER_MS,
            )
        if response is not None:
            response.headers[HEADER] = cid


def install_logging(name: str, level=logging.INFO) -> logging.Logger:
    """Configure structured logging with correlation IDs.

    Replaces basicConfig — call once at service startup.
    Returns the named logger for convenience.
    """
    fmt = "[%(asctime)s] %(levelname)s [%(cid)s] %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    root = logging.getLogger()
    root.setLevel(level)

    # Remove existing handlers (from prior basicConfig calls)
    for h in root.handlers[:]:
        root.removeHandler(h)

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    handler.addFilter(_CorrelationFilter())
    root.addHandler(handler)

    return logging.getLogger(name)
