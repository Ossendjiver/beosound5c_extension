import asyncio
import copy
import pytest
from lib.mood_mix import MoodMixes
from sources.mass.service import MassSource


class FakeQueue:
    def __init__(self):
        self.state = {'state':'playing','current_index':1,'elapsed_time':10,
            'current_item':{'queue_item_id':'seed-id','duration':100,'media_item':{'uri':'seed','name':'Seed'}},
            'items':[{'queue_item_id':'past','uri':'past'}, {'queue_item_id':'seed-id','uri':'seed'}, {'queue_item_id':'future','uri':'future'}]}
        self.calls=[]
    async def command(self, cmd, **data):
        self.calls.append((cmd,data))
        if cmd=='mood_snapshot': return {'state':'ok','snapshot':copy.deepcopy(self.state)}
        assert cmd=='mood_replace_upcoming'
        assert data['expected_item_id']==self.state['current_item']['queue_item_id']
        self.state['items']=self.state['items'][:self.state['current_index']+1]+[
            {'queue_item_id':'generated-'+u,'media_item':{'uri':u}} for u in data['media']]
        return {'state':'queued'}


@pytest.mark.asyncio
async def test_pending_steering_retries_after_transition_with_full_old_queue():
    q=FakeQueue();race=False
    async def recommend(*args):
        nonlocal race
        if race:
            race=False
            q.state['current_index']=2
            q.state['current_item']=copy.deepcopy(q.state['items'][2])
        return [dict(uri=f'track{i}',duration=100) for i in range(20)]
    mixes=MoodMixes(q.command,recommend)
    await mixes.begin('q','bedroom',mode='radio')
    race=True
    await mixes.update('q',90,.5)
    assert mixes.public('q')['pending_refresh']
    assert len(q.state['items'])-q.state['current_index']-1>5
    before=len(q.calls)
    await mixes.tick('q')
    assert not mixes.public('q')['pending_refresh']
    assert any(cmd=='mood_replace_upcoming' for cmd,_ in q.calls[before:])


@pytest.mark.asyncio
async def test_insufficient_mood_coverage_removes_old_tail_and_reports_reason():
    q=FakeQueue();available=True
    async def recommend(*args):return [dict(uri='a',duration=100)] if available else []
    mixes=MoodMixes(q.command,recommend)
    await mixes.begin('q','bedroom',mode='radio')
    available=False
    with pytest.raises(ValueError,match='No mood-compatible tracks'):
        await mixes.update('q',270,.5)
    assert len(q.state['items'])==2 and q.state['elapsed_time']==10
    assert mixes.public('q')['refresh_status']=='insufficient_mood_data'
    calls=len(q.calls);await mixes.tick('q')
    assert all(cmd=='mood_snapshot' for cmd,_ in q.calls[calls:])


@pytest.mark.asyncio
async def test_refill_shortage_does_not_remove_existing_compatible_upcoming_tracks():
    q=FakeQueue();available=True
    async def recommend(*args):return [dict(uri='a',duration=100)] if available else []
    mixes=MoodMixes(q.command,recommend)
    await mixes.begin('q','bedroom')
    await mixes.update('q',270,.5)
    available=False
    await mixes.tick('q')
    assert q.state['items'][-1]['media_item']['uri']=='a'


@pytest.mark.asyncio
async def test_choice_replaces_future_only_keeps_played_current_and_position():
    q=FakeQueue()
    async def recommend(*args): return [{'uri':'seed','duration':100},{'uri':'a','duration':100},{'uri':'a','duration':100},{'uri':'b','duration':100}]
    mixes=MoodMixes(q.command,recommend)
    await mixes.begin('q','bedroom',{'uri':'seed','duration':100})
    assert len(q.state['items'])==2  # selected collection tail waits for mood choice
    assert mixes.public('q')['awaiting_choice']
    await mixes.update('q',90,.75)
    assert q.state['current_item']['queue_item_id']=='seed-id'
    assert q.state['elapsed_time']==10
    assert [i.get('uri') or i['media_item']['uri'] for i in q.state['items']]==['past','seed','a','b']
    assert not mixes.public('q')['awaiting_choice']


@pytest.mark.asyncio
async def test_server_fallback_only_at_final_five_seconds_and_not_while_paused():
    q=FakeQueue();moods=[]
    async def recommend(room,limit,mood,seed,policy=None): moods.append(mood);return [{'uri':'a','duration':100}]
    mixes=MoodMixes(q.command,recommend)
    await mixes.begin('q','kitchen')
    q.state['elapsed_time']=94;await mixes.tick('q');assert mixes.public('q')['awaiting_choice']
    q.state['elapsed_time']=95;q.state['state']='paused';await mixes.tick('q');assert mixes.public('q')['awaiting_choice']
    q.state['state']='playing';await mixes.tick('q');assert not mixes.public('q')['awaiting_choice']
    assert all(m is None for m in moods)
    assert q.state['items'][-1]['media_item']['uri']=='a'


@pytest.mark.asyncio
async def test_manual_new_playback_cancels_mix_without_overwriting_queue():
    q=FakeQueue()
    async def recommend(*args): return [{'uri':'a','duration':100}]
    mixes=MoodMixes(q.command,recommend);await mixes.begin('q','lounge')
    q.state['current_item']={'queue_item_id':'manual','uri':'other'}
    calls=len(q.calls);await mixes.tick('q')
    assert not mixes.public('q')['active']
    assert all(cmd=='mood_snapshot' for cmd,_ in q.calls[calls:])


@pytest.mark.asyncio
async def test_track_change_during_recommendations_never_restarts_or_mutates_tail():
    q=FakeQueue()
    async def recommend(*args):
        q.state['current_item']={'queue_item_id':'new-id','uri':'new-song'}
        return [{'uri':'a','duration':100}]
    mixes=MoodMixes(q.command,recommend)
    # direct setup lets the test place the race specifically inside the recommendation fetch
    mixes.sessions['q']={'room':'lounge','mood':{},'seed':{},'seen':[],'owned':['seed'],'generation':0}
    snap=copy.deepcopy(q.state)
    await mixes.fill('q',snap)
    assert not any(cmd=='mood_replace_upcoming' for cmd,_ in q.calls)


@pytest.mark.asyncio
async def test_mass_command_uses_replace_next_batch_and_never_transport_or_seek():
    source=object.__new__(MassSource);calls=[]
    q=FakeQueue()
    async def snapshot(queue): return copy.deepcopy(q.state)
    async def response(command,**payload): calls.append((command,payload));return {'result':None}
    source._get_queue_snapshot=snapshot;source._send_command_response=response
    result=await source._handle_mood_queue('mood_replace_upcoming',{'queue_id':'q','expected_item_id':'seed-id','media':['a','b']})
    assert result['state']=='queued'
    assert calls==[('player_queues/dont_stop_the_music',{'queue_id':'q','dont_stop_the_music_enabled':False}),
        ('player_queues/play_media',{'queue_id':'q','media':['a','b'],'option':'replace_next','radio_mode':False})]
    calls.clear()
    result=await source._handle_mood_queue('mood_replace_upcoming',{'queue_id':'q','expected_item_id':'stale','media':['a']})
    assert result['state']=='error' and calls==[]

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['radio','mood'])
async def test_mix_refills_over_many_batches_and_preserves_current(mode):
    q=FakeQueue();serial=0
    async def recommend(room,limit,mood,seed,policy=None):
        nonlocal serial
        serial+=1
        return [dict(uri=f'batch{serial}-{i}',name=f'Song {serial}-{i}',artist=f'Artist {serial}-{i}',duration=100) for i in range(20)]
    mixes=MoodMixes(q.command,recommend)
    await mixes.begin('q','lounge',mode=mode)
    if mode=='mood':await mixes.update('q',90,.4)
    for _ in range(80):
        # Consume the tail until the proactive threshold is reached.
        q.state['current_index']=len(q.state['items'])-4
        q.state['current_item']=copy.deepcopy(q.state['items'][q.state['current_index']])
        current_id=q.state['current_item']['queue_item_id']
        elapsed=q.state['elapsed_time']
        await mixes.tick('q')
        assert mixes.public('q')['active']
        assert len(q.state['items'])-q.state['current_index']-1>=20
        assert q.state['current_item']['queue_item_id']==current_id and q.state['elapsed_time']==elapsed
    assert serial>=80


@pytest.mark.asyncio
async def test_exhausted_pool_reuses_recording_but_never_alternate_version():
    q=FakeQueue()
    old=dict(uri='old',name='Old Song',artist='Artist',duration=100)
    version=dict(uri='remaster',name='Old Song (Remastered)',artist='Artist',duration=100)
    async def recommend(*args):return [old,version]
    mixes=MoodMixes(q.command,recommend)
    mixes.sessions['q']={'room':'lounge','mood':None,'seed':{'duration':100},'seen':['old'],
                        'owned':['seed'],'recordings':[old],'versions':[old],'generation':0}
    await mixes.fill('q',copy.deepcopy(q.state))
    assert q.state['items'][-1]['media_item']['uri']=='old'
    assert all(i.get('media_item',{}).get('uri')!='remaster' for i in q.state['items'])


@pytest.mark.asyncio
async def test_long_queue_snapshot_fetches_window_using_global_position():
    source=object.__new__(MassSource);calls=[]
    async def snapshot(queue):return {'state':'playing','current_index':1200,'resolved_queue_id':'holder'}
    async def command(cmd,**args):
        calls.append((cmd,args))
        return [{'queue_item_id':str(i),'media_item':{'uri':f'uri{i}'}} for i in range(1180,1220)]
    source._get_queue_snapshot=snapshot;source.send_command=command
    snap=await source._get_mood_snapshot('member')
    from lib.mood_mix import current,item_index
    assert snap['current_index']==1200 and item_index(snap)==20
    assert current(snap)['uri']=='uri1200'
    assert calls==[('player_queues/items',{'queue_id':'holder','offset':1180,'limit':100})]

@pytest.mark.asyncio
async def test_root_and_session_survive_restart_and_steering_without_tick_writes():
    q=FakeQueue();q.state['current_item']['duration']=300
    stored={};writes=[]
    def save(data):
        stored.clear();stored.update(copy.deepcopy(data));writes.append(copy.deepcopy(data))
    async def recommend(*args):
        return [dict(uri=f'good{i}',name=f'Song {i}',artist=f'Artist {i}',duration=900) for i in range(6)]+[
            dict(uri='too-long',duration=901),dict(uri='too-short',duration=99)]
    mixes=MoodMixes(q.command,recommend,load=lambda:stored,save=save)
    await mixes.begin('q','bedroom',mode='radio')
    mixes=MoodMixes(q.command,recommend,load=lambda:stored,save=save)
    assert mixes.sessions['q']['seed']['duration']==300
    before=len(writes);await mixes.tick('q');assert len(writes)==before
    q.state['current_item']=copy.deepcopy(q.state['items'][2]);q.state['current_item']['duration']=900
    q.state['current_index']=2
    await mixes.update('q',120,.8)
    assert mixes.sessions['q']['seed']['duration']==300
    assert all(i.get('media_item',{}).get('uri') not in ('too-long','too-short') for i in q.state['items'])
    mixes.stop('q');assert stored['sessions']=={}

@pytest.mark.asyncio
async def test_invalid_session_storage_and_unknown_root_are_safe():
    q=FakeQueue()
    async def recommend(*args):return []
    assert MoodMixes(q.command,recommend,load=lambda:{'version':1,'sessions':[]}).sessions=={}
    q.state['current_item']['duration']=0
    mixes=MoodMixes(q.command,recommend)
    with pytest.raises(ValueError,match='known duration'):await mixes.begin('q','bedroom')
    assert not mixes.sessions
    assert all(cmd=='mood_snapshot' for cmd,_ in q.calls)
