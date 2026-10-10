"""Best-effort Frame art mode alongside All Off; never power off or wake it."""
import asyncio
import logging
import os
import re

import aiohttp
from .config import cfg

log = logging.getLogger('beo-router.frame')


async def frame_art_standby(session):
    entity = str(cfg('home_assistant', 'all_off_frame_entity', default='') or
                 cfg('mass', 'youtube_search', 'frame_entity', default='') or '').strip()
    token = os.getenv('HA_TOKEN', '').strip()
    if not re.fullmatch(r'media_player\.[a-z0-9_]+', entity) or not token or session is None:
        return
    base = str(os.getenv('HA_URL') or cfg('home_assistant', 'url', default='')).strip().rstrip('/')
    if not base:
        return
    if not base.endswith('/api'):
        base += '/api'
    headers = {'Authorization': 'Bearer '+token}
    try:
        async with session.get(base+'/states/'+entity, headers=headers,
                               timeout=aiohttp.ClientTimeout(total=4)) as response:
            response.raise_for_status()
            state = await response.json()
        if state.get('entity_id') != entity or state.get('state') not in {'on', 'playing', 'paused', 'buffering', 'idle'}:
            return
        if (state.get('attributes') or {}).get('art_mode_status') == 'on':
            return
        async with session.post(base+'/services/samsungtv_smart/set_art_mode',
                                headers=headers, json={'entity_id': entity},
                                timeout=aiohttp.ClientTimeout(total=5)) as response:
            response.raise_for_status()
            log.info('All Off: Frame art mode requested')
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError, AttributeError) as error:
        # Do not disclose URLs/credentials or disturb the independent ML command.
        log.warning('All Off: Frame art mode unavailable (%s)', type(error).__name__)
