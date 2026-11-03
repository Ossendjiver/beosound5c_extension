import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from lib.playing_queue_actions import command


def source():
    bound={'bound':True,'read_only':False,'queue_id':'link','current_item_id':'current',
           'tracks':[{'queue_item_id':'selected','uri':'tidal://one','name':'Song','duration':200}]}
    return SimpleNamespace(_playing_queue_snapshot=AsyncMock(return_value=bound),
        _handle_queue_remove_command=AsyncMock(return_value={'state':'removed'}),
        _handle_queue_play_next_command=AsyncMock(return_value={'state':'moved'}),
        _send_command_response=AsyncMock(return_value={'ok':True}),
        _api_response_ok=Mock(return_value=True),
        _wait_for_queue_item_state=AsyncMock(return_value=(1,{},True)),
        _get_queue_snapshot=AsyncMock(return_value={'current_item':{'queue_item_id':'selected'}}),
        _extract_current_queue_item=lambda s:s['current_item'])


def data(action='play_now'):
    return dict(action=action,queue_id='link',queue_item_id='selected',expected_current_item_id='current',title='Now',artist='Artist',index=0)


@pytest.mark.asyncio
@pytest.mark.parametrize('change',[
    {'queue_id':'bedroom'},{'queue_item_id':'missing'},{'expected_current_item_id':'old'},
    {'queue_item_id':''},{'artist':''},{'action':'transfer_queue'}])
async def test_stale_or_foreign_action_never_sends(change):
    s=source();result=await command(s,data()|change)
    assert result['state']=='error'
    s._send_command_response.assert_not_called()
    s._handle_queue_remove_command.assert_not_called()
    s._handle_queue_play_next_command.assert_not_called()


@pytest.mark.asyncio
async def test_unbound_queue_is_read_only():
    s=source();s._playing_queue_snapshot.return_value['read_only']=True
    assert (await command(s,data()))['state']=='error'
    s._send_command_response.assert_not_called()


@pytest.mark.asyncio
async def test_play_uses_stable_id_without_default_or_room_activation():
    s=source();result=await command(s,data())
    assert result['state']=='playing'
    s._send_command_response.assert_awaited_once_with('player_queues/play_index',queue_id='link',index='selected')


@pytest.mark.asyncio
@pytest.mark.parametrize('action,handler',[('remove','_handle_queue_remove_command'),('play_next','_handle_queue_play_next_command')])
async def test_queue_edit_never_uses_ui_index(action,handler):
    s=source();await command(s,data(action))
    getattr(s,handler).assert_awaited_once_with({'queue_id':'link','queue_item_id':'selected'})


@pytest.mark.asyncio
async def test_radio_requires_seed_metadata_before_playing():
    s=source();s._playing_queue_snapshot.return_value['tracks'][0]['duration']=0
    assert (await command(s,data('start_radio')))['reason']=='radio_seed_metadata_unavailable'
    s._send_command_response.assert_not_called()


@pytest.mark.asyncio
async def test_radio_checks_actual_seed_after_confirming_play():
    s=source();s._get_queue_snapshot.return_value={'current_item':{'queue_item_id':'advanced'}}
    assert (await command(s,data('start_radio')))['reason']=='current_item_changed'


@pytest.mark.asyncio
async def test_radio_uses_local_pattern_session_and_expected_seed():
    s=source();calls=[]
    class Response:
        status=200
        async def json(self):return {'active':True}
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
    def post(url,**kwargs):calls.append((url,kwargs));return Response()
    s.session=SimpleNamespace(post=post)
    assert (await command(s,data('start_radio')))['mode']=='radio'
    assert calls[0][0]=='http://127.0.0.1:8788/library/mix'
    body=calls[0][1]['json']
    assert body['mode']=='radio' and body['queue_id']=='link' and body['expected_item_id']=='selected'
    assert all('radio_mode' not in kwargs for _,kwargs in s._send_command_response.await_args_list)
