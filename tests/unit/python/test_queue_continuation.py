import copy
import pytest
from lib.queue_continuation import QueueContinuation
from lib.mood_mix import MoodMixes
from library import _music_item
from test_live_mood_mix import FakeQueue
from sources.mass.service import MassSource

def snapshot(kind='track',state='playing'):
    items=[dict(queue_item_id=str(i),duration=100,media_item=dict(uri=f'library://{i}',name=f'Song {i}',artist=f'Artist {i}',duration=100,media_type=kind)) for i in range(4)]
    return dict(state=state,current_index=1,current_item=items[1],items=items,repeat_mode='off')

@pytest.mark.asyncio
async def test_default_adoption_preserves_album_order_and_queue_item_ids():
    q=FakeQueue();q.state=snapshot();calls=[]
    async def recommend(*args):return [dict(uri=f'new{i}',name=f'New {i}',artist=f'N {i}',duration=100) for i in range(20)]
    mixes=MoodMixes(q.command,recommend)
    async def api(cmd,args):
        calls.append((cmd,args))
        if cmd=='player_queues/all':return [dict(queue_id='q',state='playing',items=4,current_index=1)]
        if cmd=='players/sleep_timer/get':return None
        raise AssertionError(cmd)
    auto=QueueContinuation(api,mixes,_music_item,lambda _:'phone')
    original=copy.deepcopy(q.state['items']);await auto.check()
    assert q.state['items'][:4]==original
    assert len(q.state['items'])==24
    assert mixes.sessions['q']['mode']=='radio'
    assert not any(c in ['players/cmd/play','player_queues/resume'] for c,_ in calls)
    assert all(c in ['mood_snapshot','mood_append_upcoming'] for c,_ in q.calls)

@pytest.mark.parametrize('kind',['podcast_episode','radio','sound_effect','video'])
def test_spoken_live_and_video_queues_excluded(kind):
    assert not QueueContinuation(None,None,_music_item,None).eligible(snapshot(kind))

@pytest.mark.parametrize('state',['idle','off','paused','buffering'])
def test_does_not_adopt_inactive_queues(state):
    assert not QueueContinuation(None,None,_music_item,None).eligible(snapshot(state=state))

@pytest.mark.asyncio
async def test_sleep_timer_repeat_and_existing_mix_respected():
    q=FakeQueue();q.state=snapshot();begun=[]
    class Mix:
        sessions={}
        async def snapshot(self,_):return q.state
        async def begin(self,*args,**kw):begun.append(args)
    async def api(cmd,args):
        if cmd=='player_queues/all':return [dict(queue_id='q',state='playing')]
        if cmd=='players/sleep_timer/get':return 1e12
        raise AssertionError(cmd)
    auto=QueueContinuation(api,Mix(),_music_item,lambda _:'phone')
    await auto.check();assert begun==[]
    q.state['repeat_mode']='all';await auto.check();assert begun==[]
    auto.mixes.sessions={'q':{}};await auto.check();assert begun==[]

@pytest.mark.asyncio
async def test_stop_mix_optout_survives_restart_until_new_queue():
    q=FakeQueue();q.state=snapshot();head='old';saved={};begun=[]
    class Mix:
        sessions={}
        async def snapshot(self,_):return q.state
        async def begin(self,*args,**kw):begun.append(args)
    async def api(cmd,args):
        if cmd=='player_queues/items':return [dict(queue_item_id=head)]
        if cmd=='player_queues/all':return [dict(queue_id='q',state='playing')]
        if cmd=='players/sleep_timer/get':return None
        raise AssertionError(cmd)
    def save(v):saved.clear();saved.update(v)
    auto=QueueContinuation(api,Mix(),_music_item,lambda _:'phone',load=lambda:saved,save=save)
    await auto.block('q')
    auto=QueueContinuation(api,Mix(),_music_item,lambda _:'phone',load=lambda:copy.deepcopy(saved),save=save)
    await auto.check();assert begun==[]
    head='new';await auto.check();assert len(begun)==1

@pytest.mark.asyncio
async def test_append_command_preserves_tail_and_rejects_manual_edit_race():
    q=FakeQueue();q.state=snapshot();source=object.__new__(MassSource);calls=[]
    async def read(_):return copy.deepcopy(q.state)
    async def response(cmd,**args):calls.append((cmd,args));return {'result':None}
    source._get_queue_snapshot=read;source._send_command_response=response
    data=dict(queue_id='q',expected_item_id='1',expected_upcoming_ids=['2','3'],media=['new'])
    result=await source._handle_mood_queue('mood_append_upcoming',data)
    assert result['state']=='queued';assert calls[-1][1]['option']=='add'
    calls.clear();q.state['items'].append(dict(queue_item_id='manual'))
    result=await source._handle_mood_queue('mood_append_upcoming',data)
    assert result['state']=='error' and calls==[]

@pytest.mark.asyncio
async def test_failed_auto_append_retries_append_and_never_replaces_album():
    q=FakeQueue();q.state=snapshot();fail=True;ops=[]
    async def command(cmd,**data):
        nonlocal fail
        ops.append(cmd)
        if cmd=='mood_append_upcoming' and fail:
            fail=False;return {'state':'error','reason':'temporary'}
        return await q.command(cmd,**data)
    async def recommend(*args):return [dict(uri=f'new{i}',name=f'New {i}',artist=f'N {i}',duration=100) for i in range(20)]
    mixes=MoodMixes(command,recommend);original=copy.deepcopy(q.state['items'])
    with pytest.raises(ValueError):await mixes.begin('q','phone',mode='radio',preserve_upcoming=True)
    assert not mixes.sessions['q'].get('pending_refresh')
    mixes.sessions['q'].pop('next_refill',None)
    await mixes.tick('q')
    assert q.state['items'][:4]==original
    assert 'mood_replace_upcoming' not in ops


def test_external_native_source_cannot_adopt_stale_ma_queue():
    data=snapshot();data['active']=False
    assert not QueueContinuation(None,None,_music_item,None).eligible(data)
