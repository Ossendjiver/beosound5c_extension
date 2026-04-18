"""Startup beacon — sends a single anonymous POST to beosound5c.com/api/beacon.

Payload: device_id (stable UUID), version, sources, player_type, volume_type.
The server adds IP and country via Cloudflare headers.

Opt-out: create a file called NO_TELEMETRY in the repo root.
"""

import json
import logging
import os
import uuid

import aiohttp

logger = logging.getLogger('beacon')

BEACON_URL = 'https://beosound5c.com/api/beacon'

_KNOWN_SYSTEM_KEYS = frozenset({
    'device', 'menu', 'scenes', 'player', 'volume',
    'home_assistant', 'transport', 'showing', 'join',
    'bluetooth', 'remote',
})


def _get_or_create_device_id(base_path: str) -> str:
    id_file = os.path.join(base_path, 'device_id')
    try:
        if os.path.isfile(id_file):
            return open(id_file).read().strip()
        device_id = str(uuid.uuid4())
        with open(id_file, 'w') as f:
            f.write(device_id + '\n')
        logger.debug('Generated new device_id: %s', device_id)
        return device_id
    except Exception as e:
        logger.debug('device_id file unavailable: %s', e)
        return 'unknown'


def _build_payload(base_path: str) -> dict:
    # Version
    try:
        version = open(os.path.join(base_path, 'VERSION')).read().strip()
    except Exception:
        version = 'unknown'

    # Sources: top-level config keys that aren't system sections
    try:
        from lib.config import load_config
        config = load_config()
        sources = [k for k, v in config.items()
                   if k not in _KNOWN_SYSTEM_KEYS and isinstance(v, dict)]
        player_type = (config.get('player') or {}).get('type', 'unknown')
        volume_type = (config.get('volume') or {}).get('type', 'unknown')
    except Exception:
        sources = []
        player_type = 'unknown'
        volume_type = 'unknown'

    return {
        'device_id':   _get_or_create_device_id(base_path),
        'version':     version,
        'sources':     sources,
        'player_type': player_type,
        'volume_type': volume_type,
    }


async def send_beacon(base_path: str) -> None:
    """Fire-and-forget. Logs result at DEBUG; never raises."""
    try:
        opt_out = os.path.join(base_path, 'NO_TELEMETRY')
        if os.path.isfile(opt_out):
            logger.debug('Telemetry disabled (NO_TELEMETRY file present)')
            return

        payload = _build_payload(base_path)

        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(BEACON_URL, json=payload) as resp:
                body = await resp.json()
                logger.info(
                    'Beacon sent — version=%s country=%s',
                    payload['version'], body.get('country', '?'),
                )
    except Exception as e:
        logger.debug('Beacon failed (non-fatal): %s', e)
