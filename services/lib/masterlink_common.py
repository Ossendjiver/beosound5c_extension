# BeoSound 5c
# Copyright (C) 2024-2026 Markus Kirsten
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Shared helpers used by all three Master Link role modules
# (master / provider / link).  Lives separately so the role modules
# can be siblings without cross-importing each other.

import logging
from datetime import datetime
from os import environ

import aiohttp

from lib.endpoints import ROUTER_EVENT

logger = logging.getLogger('beo-masterlink')

# Beo4 transport keys arriving over the ML bus.  The provider role sees
# them inside an N.MUSIC session (master forwards them to drive our local
# player); the master role sees them when a link device forwards a Beo4
# transport press from its own remote.  Forwarded to beo-router as
# synthetic source events so the router-side handlers don't need to know
# the input came from ML.
ML_BEO4_TRANSPORT_ACTIONS = {
    0x1E: "up",
    0x1F: "down",
    0x32: "left",
    0x33: "return",
    0x34: "right",
    0x35: "go",
    0x36: "stop",
}


async def forward_to_router(session, source, action, device_type, link="",
                            count=1):
    """Synthesize a router event from an ML-side input.

    Used by the provider role (transport keys forwarded over ML), the
    master role (link-device source/transport keys), and the link role
    (sources received from a remote master)."""
    webhook_data = {
        'device_name': environ.get("BEOSOUND_DEVICE_NAME", "BeoSound5c"),
        'source': source,
        'link': link,
        'action': action,
        'device_type': device_type,
        'count': count,
        'timestamp': datetime.now().isoformat(),
    }
    try:
        async with session.post(
            ROUTER_EVENT, json=webhook_data,
            timeout=aiohttp.ClientTimeout(total=1.0),
        ) as resp:
            if resp.status != 200:
                logger.warning("Router returned HTTP %d (ml-forward: %s)",
                               resp.status, action)
    except Exception as e:
        logger.warning("Router unreachable (ml-forward %s): %s", action, e)
