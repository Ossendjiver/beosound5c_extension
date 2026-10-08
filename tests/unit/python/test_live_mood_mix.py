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
    async def recommend(room,limit,mood,seed): moods.append(mood);return [{'uri':'a','duration':100}]
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
