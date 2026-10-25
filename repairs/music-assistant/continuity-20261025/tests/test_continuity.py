import asyncio
import importlib.util
import logging
from pathlib import Path
from types import SimpleNamespace as N
from contextlib import asynccontextmanager
import pytest

ROOT=Path(__file__).resolve().parents[1]
def load(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'helpers'/f'{name}.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
routes=load('tidal_dash_routes');recovery=load('stream_failure_recovery')

class Router:
    base_url='http://127.0.0.1:8097'
    def __init__(self):self.routes={}
    def register_dynamic_route(self,path,handler,method):
        assert path not in self.routes;self.routes[path]=handler
    def unregister_dynamic_route(self,path,method):self.routes.pop(path)

class Mass:
    def __init__(self):self.streams=Router();self.timers=[]
    def call_later(self,delay,callback,*args,**kwargs):self.timers.append((delay,callback,args))

def test_dash_route_outlives_cached_details_even_for_short_tracks():
    mass=Mass();helper=routes.DashManifestRoutes(mass)
    url=helper.register(b'<MPD/>',1)
    assert url.startswith(mass.streams.base_url+'/tidal-dash/')
    assert mass.timers[-1][0] >= routes.DASH_DETAILS_TTL+routes.IDLE_BUFFER
    assert routes.DASH_DETAILS_TTL < 300+1  # previous short-track route died before the old 600s cache

@pytest.mark.asyncio
async def test_serving_or_reusing_manifest_renews_lease_and_stale_cleanup_cannot_remove_it():
    mass=Mass();helper=routes.DashManifestRoutes(mass);url=helper.register(b'<MPD/>',211);path=url.removeprefix(mass.streams.base_url)
    old=mass.timers[-1]
    response=await mass.streams.routes[path](None)
    assert response.body==b'<MPD/>' and response.headers['Cache-Control']=='no-cache'
    old[1](*old[2]);assert path in mass.streams.routes
    current=mass.timers[-1];assert helper.register(b'<MPD/>',211)==url
    current[1](*current[2]);assert path in mass.streams.routes
    last=mass.timers[-1];last[1](*last[2]);assert path not in mass.streams.routes and not helper.leases

def test_reload_namespace_long_tracks_and_capacity_are_safe():
    mass=Mass();one=routes.DashManifestRoutes(mass);two=routes.DashManifestRoutes(mass)
    assert one.register(b'<MPD/>',7200)!=two.register(b'<MPD/>',7200)
    assert mass.timers[-1][0]>=7500
    one.leases.update({str(n):object() for n in range(1024)})
    before=set(mass.streams.routes)
    with pytest.raises(RuntimeError):one.register(b'<different/>',1)
    assert set(mass.streams.routes)==before
    for data in [b'',b'x'*262145]:
        with pytest.raises(ValueError):two.register(data,1)

class Host:
    def __init__(self):
        self.items=[N(queue_item_id=str(i),duration=211,media_type='track',available=True,
            streamdetails=N(provider='tidal--test',buffer=None,stream_error=True)) for i in range(6)]
        self.queue=N(active=True,flow_mode=False,repeat_mode='off',state='idle',current_index=0,
                     current_item=self.items[0],elapsed_time=211)
        self.data=N(session_id='session',queue=self.queue)
        self._queue_data={'q':self.data};self.calls=[];self.timer=None;self.lock_mutation=None
        self.player=N(state=N(available=True,powered=True,active_source='q',playback_state='idle'),stop_called=False)
        self.mass=N(create_task=asyncio.create_task,players=N(get_player=lambda _:self.player,
            get_sleep_timer=lambda _:self.timer,get_group_and_player_lock=self.lock))
        self.logger=logging.getLogger('continuity-tests');self.fail=False
    def get_item(self,q,index):
        if isinstance(index,str):return next((i for i in self.items if i.queue_item_id==index),None)
        return self.items[index] if 0<=index<len(self.items) else None
    def index_by_id(self,q,item):return next((n for n,i in enumerate(self.items) if i.queue_item_id==item),None)
    @asynccontextmanager
    async def lock(self,_):
        if self.lock_mutation:self.lock_mutation()
        yield
    async def play_index(self,q,item):
        self.calls.append((q,item))
        if self.fail:raise IOError('lost acknowledgement')

async def immediate(_):await asyncio.sleep(0)
async def run(host,helper=None,item='0'):
    helper=helper or recovery.StreamFailureRecovery(host,sleep=immediate)
    helper.arm('q',item,host.data.session_id)
    task=helper.pending.get('q')
    if task:
        try:await task
        except IOError:pass
    await asyncio.sleep(0)
    return helper

@pytest.mark.asyncio
async def test_confirmed_failure_at_eof_continues_once_with_fresh_details_and_unchanged_tail():
    h=Host();tail=list(h.items);helper=await run(h)
    assert h.calls==[('q','1')] and h.items[1].streamdetails is None
    assert h.items==tail
    await run(h,helper);assert len(h.calls)==1

@pytest.mark.asyncio
@pytest.mark.parametrize('mutation',[
    lambda h:setattr(h.queue,'state','paused'),
    lambda h:setattr(h.player.state,'playback_state','paused'),
    lambda h:setattr(h.player,'stop_called',True),
    lambda h:setattr(h.player.state,'powered',False),
    lambda h:setattr(h.player.state,'powered',None),
    lambda h:setattr(h.player.state,'available',False),
    lambda h:setattr(h.player.state,'active_source','other'),
    lambda h:setattr(h.queue,'active',False),
    lambda h:setattr(h.queue,'flow_mode',True),
    lambda h:setattr(h.queue,'repeat_mode','all'),
    lambda h:setattr(h,'timer',1),
    lambda h:setattr(h,'timer',1e12),
    lambda h:setattr(h.queue,'elapsed_time',30),
    lambda h:setattr(h.items[1],'available',False),
    lambda h:setattr(h.items[0],'media_type','podcast_episode'),
    lambda h:setattr(h.items[0].streamdetails,'provider','builtin'),
])
async def test_manual_or_ambiguous_or_unavailable_states_never_start(mutation):
    h=Host();mutation(h);await run(h);assert not h.calls

@pytest.mark.asyncio
async def test_prefetch_failure_waits_for_failed_item_and_does_not_cut_off_playing_audio():
    h=Host();h.queue.state=h.player.state.playback_state='playing';n=0
    async def sleep(_):
        nonlocal n
        n+=1
        assert not h.calls
        if n==3:
            h.queue.current_index=1;h.queue.current_item=h.items[1]
            h.queue.state=h.player.state.playback_state='idle'
        await asyncio.sleep(0)
    helper=recovery.StreamFailureRecovery(h,sleep=sleep)
    await run(h,helper,item='1');assert n==3 and h.calls==[('q','2')]

@pytest.mark.asyncio
async def test_stop_or_pause_cancels_pending_failure_even_if_then_resumed():
    h=Host();gate=asyncio.Event()
    async def sleep(_):await gate.wait()
    helper=recovery.StreamFailureRecovery(h,sleep=sleep);helper.arm('q','0','session');task=helper.pending['q']
    await asyncio.sleep(0);helper.cancel('q');gate.set()
    with pytest.raises(asyncio.CancelledError):await task
    assert not h.calls and not helper.pending

@pytest.mark.asyncio
@pytest.mark.parametrize('mutation',[
    lambda h:setattr(h.data,'session_id','new-session'),
    lambda h:setattr(h.queue,'state','paused'),
    lambda h:setattr(h,'timer',1e12),
    lambda h:setattr(h.items[1],'queue_item_id','user-replacement'),
    lambda h:setattr(h.player.state,'active_source','new-source'),
])
async def test_rechecks_under_playback_lock_before_any_transport(mutation):
    h=Host();h.lock_mutation=lambda:mutation(h);await run(h);assert not h.calls

@pytest.mark.asyncio
async def test_lost_acknowledgement_is_not_replayed_and_consecutive_failure_budget_is_bounded():
    h=Host();h.fail=True;helper=await run(h)
    await run(h,helper);assert len(h.calls)==1
    h=Host();helper=recovery.StreamFailureRecovery(h,sleep=immediate)
    for n in range(5):
        h.queue.current_index=n;h.queue.current_item=h.items[n]
        await run(h,helper,str(n))
    assert len(h.calls)==3

@pytest.mark.asyncio
async def test_successful_automatic_advance_or_session_replacement_discards_stale_failure():
    h=Host()
    async def sleep(_):h.queue.current_index=2;h.queue.current_item=h.items[2];await asyncio.sleep(0)
    await run(h,recovery.StreamFailureRecovery(h,sleep=sleep));assert not h.calls

@pytest.mark.asyncio
async def test_buffer_cleanup_race_and_timer_read_failure_fail_closed():
    h=Host()
    async def clear():h.data.session_id='new-session'
    h.items[1].streamdetails.buffer=N(clear=clear)
    await run(h);assert not h.calls
    h=Host()
    def fail(_):raise IOError('unavailable timer state')
    h.mass.players.get_sleep_timer=fail
    await run(h);assert not h.calls

@pytest.mark.asyncio
async def test_successful_refresh_cancels_old_failure_before_normal_eof():
    h=Host()
    async def sleep(_):h.items[0].streamdetails.stream_error=False;await asyncio.sleep(0)
    await run(h,recovery.StreamFailureRecovery(h,sleep=sleep));assert not h.calls

@pytest.mark.asyncio
async def test_no_confirmed_failure_cannot_arm_an_idle_watchdog():
    h=Host();h.items[0].streamdetails.stream_error=False
    helper=await run(h);assert not h.calls and not helper.pending
