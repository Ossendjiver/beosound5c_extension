"""Idle BS5c follows SHOWING; selected MASS playback stays authoritative."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from lib.media_state import MediaState
from sources.mass.service import MassSource

ENTITY = 'media_player.display_target'

@pytest.mark.parametrize('payload', [
    {'state': 'playing', 'title': 'Bedroom track'},
    {'state': 'playing', 'title': 'Ensuite track', '_source_id': 'mass', '_reason': 'track_change'},
    {'state': 'playing', 'title': 'Whole house', 'relay_id': 'showing', 'entity_id': 'media_player.other_aggregate'},
    {'state': 'idle', 'title': 'Old track'},
])
def test_other_room_and_bootstrap_updates_cannot_replace_fallback(payload):
    result = MediaState().validate_update(payload, None, 0, fallback_entity_id=ENTITY)
    assert result['dropped']

@pytest.mark.parametrize('state', ['playing', 'paused', 'idle'])
def test_configured_showing_relay_is_accepted(state):
    payload = {'relay_id': 'showing', 'entity_id': ENTITY, 'state': state, 'title': '' if state == 'idle' else 'Lounge'}
    assert MediaState().validate_update(payload, None, 0, fallback_entity_id=ENTITY) is None

@pytest.mark.parametrize('source', ['mass', 'kodi', 'news'])
def test_selected_direct_source_keeps_its_metadata(source):
    payload = {'_source_id': source, 'title': 'Direct playback', 'state': 'playing'}
    assert MediaState().validate_update(payload, source, 0, True, fallback_entity_id=ENTITY) is None


def test_slow_showing_response_cannot_overwrite_new_direct_playback():
    payload = {'relay_id': 'showing', 'entity_id': ENTITY, 'title': 'Lounge', 'state': 'playing'}
    assert MediaState().validate_update(payload, 'mass', 0, fallback_entity_id=ENTITY)['dropped']


def make_source():
    with patch.object(MassSource, '_load_local_cache', return_value=False):
        return MassSource()

@pytest.mark.asyncio
async def test_metadata_does_not_search_other_queues_when_selected_target_is_idle(mock_config):
    mock_config({'showing': {'exclusive_playing_fallback': True}})
    source = make_source()
    source._preferred_player_id = 'lounge-wiim'
    source._refresh_preferred_player_from_router = AsyncMock()
    source.send_command = AsyncMock(return_value={'queue_id': 'lounge-queue'})
    source._resolve_queue_candidates = AsyncMock(return_value=['bedroom', 'ensuite'])
    source._build_now_playing_payload = AsyncMock(side_effect=lambda queue: {'queue_id': queue, 'state': 'idle'})
    result = await source._best_now_playing_payload()
    assert result['queue_id'] == 'lounge-queue'
    assert result['state'] == 'idle'
    source._resolve_queue_candidates.assert_not_awaited()
    assert [call.args[0] for call in source._build_now_playing_payload.await_args_list] == ['lounge-queue', 'lounge-wiim']

@pytest.mark.asyncio
async def test_idle_source_resync_does_not_adopt_external_room(mock_config):
    mock_config({'showing': {'exclusive_playing_fallback': True}})
    source = make_source()
    source._registered_state = 'available'
    source._router_selected_mass = AsyncMock(return_value=False)
    source._resolve_metadata_queue_candidates = AsyncMock(return_value=['bedroom'])
    source._publish_now_playing = AsyncMock()
    assert not (await source.handle_resync())['resynced']
    source._resolve_metadata_queue_candidates.assert_not_awaited()
    source._publish_now_playing.assert_not_awaited()

@pytest.mark.asyncio
async def test_existing_direct_remote_source_resync_survives_router_restart(mock_config):
    mock_config({'showing': {'exclusive_playing_fallback': True}})
    source = make_source()
    source._registered_state = 'playing'
    source._router_selected_mass = AsyncMock(return_value=False)
    source._resolve_metadata_queue_candidates = AsyncMock(return_value=['chosen-queue'])
    source._build_now_playing_payload = AsyncMock(return_value={'state': 'playing', 'uri': 'track://direct'})
    source._publish_now_playing = AsyncMock()
    assert (await source.handle_resync())['resynced']
    source._publish_now_playing.assert_awaited_once_with('chosen-queue', requested_uri='track://direct', reason='resync', force_state='playing')

@pytest.mark.asyncio
@pytest.mark.parametrize('selected', [False, None])
async def test_monitor_cannot_claim_idle_or_unreachable_router(mock_config, selected):
    mock_config({'showing': {'exclusive_playing_fallback': True}})
    source = make_source()
    source._connected = True
    source._router_selected_mass = AsyncMock(return_value=selected)
    source._best_now_playing_payload = AsyncMock()
    source.register = AsyncMock()
    with patch('sources.mass.service.asyncio.sleep', AsyncMock(side_effect=asyncio.CancelledError)):
        with pytest.raises(asyncio.CancelledError):
            await source._remote_metadata_monitor_loop()
    source.register.assert_not_awaited()
    source._best_now_playing_payload.assert_not_awaited()

@pytest.mark.asyncio
async def test_monitor_keeps_direct_track_advancement(mock_config):
    mock_config({'showing': {'exclusive_playing_fallback': True}})
    source = make_source()
    source._connected = True
    source._router_selected_mass = AsyncMock(return_value=True)
    source._best_now_playing_payload = AsyncMock(side_effect=[
        {'queue_id': 'direct', 'state': 'playing', 'uri': 'track://a', 'title': 'A'},
        {'queue_id': 'direct', 'state': 'playing', 'uri': 'track://b', 'title': 'B'},
    ])
    source.register = AsyncMock()
    source.post_media_update = AsyncMock()
    with patch('sources.mass.service.asyncio.sleep', AsyncMock(side_effect=[None, asyncio.CancelledError])):
        with pytest.raises(asyncio.CancelledError):
            await source._remote_metadata_monitor_loop()
    assert [call.kwargs['title'] for call in source.post_media_update.await_args_list] == ['A', 'B']
    assert all(call.kwargs['auto_power'] is False for call in source.register.await_args_list)
