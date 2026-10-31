"""Controls for a shared MA queue explicitly bound to the displayed recording.

Never select another room, resolve a default target, register a source, or power a room.
"""
import asyncio
import aiohttp


def error(reason):
    return {'state': 'error', 'reason': reason}


async def command(source, data):
    action = data.get('action')
    if action not in {'play_now', 'play_next', 'remove', 'start_radio'}:
        return error('unsupported_queue_action')
    queue = str(data.get('queue_id') or '').strip()
    marker = str(data.get('queue_item_id') or '').strip()
    current_id = str(data.get('expected_current_item_id') or '').strip()
    title, artist = str(data.get('title') or '').strip(), str(data.get('artist') or '').strip()
    if not all((queue, marker, current_id, title, artist)):
        return error('missing_queue_identity')
    lock = getattr(source, '_playing_queue_action_lock', None)
    if lock is None:
        source._playing_queue_action_lock = lock = asyncio.Lock()
    async with lock:
        bound = await source._playing_queue_snapshot(title, artist)
        if not bound.get('bound') or bound.get('read_only') or bound.get('queue_id') != queue:
            return error('playing_queue_changed')
        if bound.get('current_item_id') != current_id:
            return error('current_item_changed')
        item = next((row for row in bound.get('tracks', [])
                     if str(row.get('queue_item_id') or '') == marker), None)
        if not item:
            return error('queue_item_not_found')
        # Stable ID only: stale numeric indexes must never select another song.
        payload = {'queue_id': queue, 'queue_item_id': marker}
        if action == 'remove':
            return await source._handle_queue_remove_command(payload)
        if action == 'play_next':
            return await source._handle_queue_play_next_command(payload)
        if action == 'start_radio' and (not item.get('uri') or not item.get('duration')):
            return error('radio_seed_metadata_unavailable')
        if action == 'play_now' or marker != current_id or bound.get('state') == 'paused':
            response = await source._send_command_response('player_queues/play_index', queue_id=queue, index=marker)
            if not source._api_response_ok(response):
                return error('play_index_failed')
            _, _, confirmed = await source._wait_for_queue_item_state(queue, marker, current=True)
            if not confirmed:
                return error('play_index_failed')
        if action == 'play_now':
            return {'state': 'playing', 'queue_id': queue}
        # Local pattern/song radio, never Music Assistant's endless/radio_mode path.
        # Read the actual selected seed again; do not begin from an advanced track.
        snap = await source._get_queue_snapshot(queue)
        playing = source._extract_current_queue_item(snap) or {}
        if str(playing.get('queue_item_id') or '') != marker:
            return error('current_item_changed')
        seed = dict(item, title=item.get('name'), media_type='track')
        try:
            async with source.session.post('http://127.0.0.1:8788/library/mix',
                    json={'action': 'begin', 'queue_id': queue, 'room': 'lounge',
                          'mode': 'radio', 'seed': seed, 'expected_item_id': marker},
                    timeout=aiohttp.ClientTimeout(total=45)) as response:
                result = await response.json()
                if response.status != 200 or not result.get('active'):
                    return error('radio_start_failed')
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return error('radio_start_failed')
        return {'state': 'playing', 'queue_id': queue, 'mode': 'radio'}
