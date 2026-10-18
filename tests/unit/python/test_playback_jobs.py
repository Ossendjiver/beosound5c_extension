"""Offline orchestration safety checks. No real players or HA connection."""
import asyncio
import copy
import sqlite3
from contextlib import asynccontextmanager
import pytest
from lib.playback_jobs import Coordinator, Store, Conflict, InvalidIntent, Uncertain, Snapshot, canonical

ID="request-01234567890123456789"
class Lease:
    def __init__(self):self.owner=None;self.lost=False
    async def check(self,owner):
        if self.lost or self.owner!=owner:raise Uncertain("Lease lost")
    @asynccontextmanager
    async def hold(self,owner):
        self.owner=owner
        try:yield
        finally:self.owner=None

class Fake:
    def __init__(self):
        self.calls=[];self.actions=[];self.lease=Lease();self.fail_reply=False;self.drop_move=False;self.advance=False
        self.players=[dict(player_id=x,available=True,active_source=x,state='playing',can_group_with=['link','cast','phone']) for x in ['link','cast','phone']]
        self.rows={'link':[dict(queue_item_id='a',uri='tidal://1'),dict(queue_item_id='b',uri='tidal://2')], 'cast':[],'phone':[]}
        self.queues=[dict(queue_id=x,state='playing' if x=='link' else 'idle',current_item={'queue_item_id':'a'} if x=='link' else {}) for x in ['link','cast','phone']]
        self.ha=[dict(entity_id='media_player.'+x,state='on' if x=='lounge' else 'off',attributes={'source':'LINK','source_list':['LINK','A.AUX']}) for x in ['lounge','bedroom','ensuite']]
        self.ha.append(dict(entity_id='input_boolean.kodi_mass_interlock',state='off',attributes={}))
        self.routes={x:{'primary':dict(player_id='link',entity='media_player.'+x,source='LINK',shared=True)} for x in ['lounge','bedroom','ensuite']}
        self.routes['kitchen']={'primary':dict(player_id='cast')}
        self.store=Store(sqlite3.connect(':memory:'))
        self.worker=Coordinator(self.store,self.routes,self.command,self.states,self.actuator,self.lease,wait=self.wait)
    async def wait(self,seconds):await asyncio.sleep(0)
    async def states(self):return copy.deepcopy(self.ha)
    def queue(self,id):return next(q for q in self.queues if q['queue_id']==id)
    async def command(self,name,args):
        self.calls.append((name,args))
        if name=='players/all':return copy.deepcopy(self.players)
        if name=='player_queues/all':return copy.deepcopy(self.queues)
        if name=='player_queues/items':return copy.deepcopy(self.rows[args['queue_id']])
        if name=='player_queues/transfer':
            if not self.drop_move:
                src,dst=args['source_queue_id'],args['target_queue_id']
                self.rows[dst]=self.rows[src];self.rows[src]=[]
                self.queue(dst).update(state='playing' if args['auto_play'] else 'paused',current_item={'queue_item_id':'b' if self.advance else 'a'})
                self.queue(src).update(state='idle',current_item={})
            if self.fail_reply:raise TimeoutError('Lost response')
            return {}
        if name=='players/cmd/group':
            next(p for p in self.players if p['player_id']==args['player_id'])['synced_to']=args['target_player'];return {}
        if name=='players/cmd/ungroup':
            next(p for p in self.players if p['player_id']==args['player_id']).pop('synced_to',None);return {}
        raise AssertionError(name)
    async def actuator(self,action,entity,data):
        self.actions.append((action,entity,data));state=next(s for s in self.ha if s['entity_id']==entity)
        if action=='select_source':state.update(state='on');state['attributes']['source']=data['source']
        else:state.update(state='off')
    async def run(self,op='transfer',source='lounge',target='kitchen'):
        request=dict(request_id=ID,operation=op,target={'room':target})
        if op!='leave':request['source']={'room':source}
        self.worker.submit(request);await self.worker.process(ID);return self.store.get(ID)
    def mutations(self):return [c for c in self.calls if c[0].startswith('players/cmd/') or c[0]=='player_queues/transfer']


def test_idempotency_and_restart_are_not_replayed():
    f=Fake();intent=dict(request_id=ID,operation='leave',target={'room':'kitchen'})
    a=f.worker.submit(intent);assert f.worker.submit(intent)==a
    with pytest.raises(Conflict):f.worker.submit(dict(intent,target={'room':'bedroom'}))
    f.store.update(ID,'running','dispatching');f.store.recover()
    assert f.store.get(ID)['status']=='interrupted' and not f.store.pending()

def test_expired_receipt_keeps_idempotency():
    f=Fake();request=dict(request_id=ID,operation='leave',target={'room':'kitchen'})
    f.worker.submit(request);f.store.update(ID,'succeeded','confirmed');f.store.db.execute('UPDATE playback_jobs SET updated=0');f.store.db.commit();f.store.prune()
    assert f.worker.submit(request)['status']=='succeeded'
    with pytest.raises(Conflict):f.worker.submit(dict(request,target={'room':'lounge'}))

@pytest.mark.asyncio
async def test_transfer_continues_without_phone_and_lost_ack_read_only_reconciles():
    f=Fake();f.fail_reply=True;f.advance=True
    result=await f.run()
    assert result['status']=='succeeded';assert len(f.mutations())==1
    assert f.actions==[('turn_off','media_player.lounge',{})]
    assert len(f.rows['cast'])==2

@pytest.mark.asyncio
async def test_empty_queue_never_destroys_destination():
    f=Fake();f.rows['link']=[];f.rows['cast']=[dict(queue_item_id='keep',uri='x')]
    assert (await f.run())['status']=='failed'
    assert not f.mutations() and not f.actions and f.rows['cast'][0]['queue_item_id']=='keep'

@pytest.mark.asyncio
async def test_failed_move_no_hardware_or_replay():
    f=Fake();f.drop_move=True;f.fail_reply=True
    assert (await f.run())['status']=='needs_review'
    assert len(f.mutations())==1 and not f.actions

@pytest.mark.asyncio
async def test_shared_join_only_requested_target_and_no_ma_move():
    f=Fake();assert (await f.run('join',target='bedroom'))['status']=='succeeded'
    assert not f.mutations()
    assert f.actions==[('select_source','media_player.bedroom',{'source':'LINK'})]

@pytest.mark.asyncio
async def test_shared_transfer_only_hardware():
    f=Fake();assert (await f.run('transfer',target='bedroom'))['status']=='succeeded'
    assert not f.mutations()
    assert [x[:2] for x in f.actions]==[('select_source','media_player.bedroom'),('turn_off','media_player.lounge')]

@pytest.mark.asyncio
@pytest.mark.parametrize('state',['on','unknown','unavailable'])
async def test_transfer_preserves_other_shared_listeners(state):
    f=Fake();f.ha[1]['state']=state
    assert (await f.run())['status'] in {'failed','needs_review'}
    assert not f.mutations() and not f.actions

@pytest.mark.asyncio
async def test_cannot_join_silent_or_stale_shared_room():
    f=Fake();f.ha[0]['state']='off'
    assert (await f.run('join',target='bedroom'))['status']=='failed';assert not f.actions

@pytest.mark.asyncio
async def test_native_join_uses_group_not_transfer():
    f=Fake();assert (await f.run('join'))['status']=='succeeded'
    assert [x[0] for x in f.mutations()]==['players/cmd/group'];assert not f.actions

@pytest.mark.asyncio
async def test_grouped_source_never_moved():
    f=Fake();f.players[0]['group_members']=['link','cast']
    assert (await f.run())['status']=='failed';assert not f.mutations()

@pytest.mark.asyncio
async def test_lease_loss_after_move_never_powers_room():
    f=Fake();original=f.command
    async def command(name,args):
        result=await original(name,args)
        if name=='player_queues/transfer':f.lease.lost=True
        return result
    f.worker.command=command
    assert (await f.run())['status']=='needs_review';assert not f.actions;assert len(f.mutations())==1

@pytest.mark.asyncio
async def test_read_cache_coalesces_concurrent_clients():
    f=Fake();await asyncio.gather(*(f.worker.snapshot.get() for _ in range(12)))
    assert [x[0] for x in f.calls].count('players/all')==1
    await f.worker.snapshot.get(fresh=True)
    assert [x[0] for x in f.calls].count('players/all')==2

@pytest.mark.asyncio
async def test_leave_shared_only_turns_requested_room_off():
    f=Fake();assert (await f.run('leave',target='bedroom'))['status']=='succeeded'
    assert f.actions==[('turn_off','media_player.bedroom',{})];assert not f.mutations()

@pytest.mark.parametrize('operation',['pause','resume','stop','raw'])
def test_cheap_commands_not_queued_behind_transfer(operation):
    f=Fake()
    with pytest.raises(InvalidIntent):f.worker.submit(dict(request_id=ID,operation=operation,target={'room':'kitchen'}))

def test_endpoints_are_server_owned():
    f=Fake()
    for endpoint in [{'room':'lounge','entity':'media_player.other'},{'kind':'phone','player_id':'arbitrary'},{'room':'unknown'}]:
        with pytest.raises(InvalidIntent):f.worker.submit(dict(request_id=ID,operation='leave',target=endpoint))
    assert f.worker.route({'kind':'phone','player_id':'homemedia-12345678-1234-1234-1234-123456789012'}).entity==''

@pytest.mark.asyncio
async def test_alternate_input_listener_also_protects_shared_queue():
    f=Fake();f.routes['bedroom']['primary'].update(source='A.AUX',alternate_sources=['LINK']);f.ha[1]['state']='on'
    assert (await f.run())['status']=='failed';assert not f.mutations()

@pytest.mark.asyncio
async def test_unknown_interlock_blocks_before_any_command():
    f=Fake();f.ha[-1]['state']='unavailable'
    assert (await f.run())['status']=='failed';assert not f.actions and not f.mutations()

@pytest.mark.asyncio
async def test_already_joined_native_room_is_noop():
    f=Fake();f.players[1]['synced_to']='link'
    assert (await f.run('join'))['status']=='succeeded';assert not f.actions and not f.mutations()

@pytest.mark.asyncio
async def test_lost_group_reply_reconciles_without_second_join():
    f=Fake();original=f.command
    async def command(name,args):
        result=await original(name,args)
        if name=='players/cmd/group':raise TimeoutError('Lost response')
        return result
    f.worker.command=command
    assert (await f.run('join'))['status']=='succeeded'
    assert [x[0] for x in f.mutations()]==['players/cmd/group']

@pytest.mark.asyncio
async def test_room_audio_policy_runs_once_after_final_hardware_state():
    f=Fake();sync=[]
    async def audio(owner,room):
        await f.lease.check(owner)
        sync.append((room,{x['entity_id']:x['state'] for x in f.ha}))
    f.worker.audio_sync=audio
    assert (await f.run('transfer',target='bedroom'))['status']=='succeeded'
    assert len(sync)==1 and sync[0][0]=='bedroom'
    assert sync[0][1]['media_player.lounge']=='off' and sync[0][1]['media_player.bedroom']=='on'
    assert not f.mutations()


# Safeguards recovered from the retired Home Media #2 routing prototype.
@pytest.mark.asyncio
@pytest.mark.parametrize('identity', [dict(provider='kodi'), dict(name='Renamed TV', device_info={'model':'Kodi'})])
async def test_hidden_kodi_target_fails_before_any_mutation(identity):
    f=Fake();f.players[1].update(identity)
    assert (await f.run('join'))['status']=='failed'
    assert not f.actions and not f.mutations()

@pytest.mark.asyncio
@pytest.mark.parametrize('membership', ['group_members','static_group_members'])
async def test_group_with_kodi_member_fails_before_any_mutation(membership):
    f=Fake();f.players[1][membership]=['cast','hidden']
    f.players.append(dict(player_id='hidden',provider='kodi',available=True))
    assert (await f.run('join'))['status']=='failed'
    assert not f.actions and not f.mutations()

@pytest.mark.asyncio
async def test_protocol_output_cannot_hide_kodi_target():
    f=Fake();f.players[1]['active_output_protocol']='hidden'
    f.players.append(dict(player_id='hidden',provider='kodi',available=True))
    assert (await f.run('join'))['status']=='failed'
    assert not f.actions and not f.mutations()

@pytest.mark.asyncio
async def test_reverse_group_membership_already_joined_is_noop():
    f=Fake();f.players[0]['group_members']=['link','cast']
    assert (await f.run('join'))['status']=='succeeded'
    assert not f.actions and not f.mutations()

def test_reverse_group_membership_ambiguous_owner_is_rejected():
    from lib.playback_jobs import leader
    f=Fake();f.players[0]['group_members']=['link','cast'];f.players[2]['group_members']=['phone','cast']
    with pytest.raises(Uncertain):leader('cast',{'players':f.players})


@pytest.mark.asyncio
async def test_configured_protocol_child_cannot_hide_kodi_identity():
    f=Fake();f.routes['kitchen']['primary']['player_id']='hidden'
    f.players.append(dict(player_id='hidden',provider='kodi',protocol_parent_id='cast',available=True))
    assert (await f.run('join'))['status']=='failed'
    assert not f.actions and not f.mutations()

@pytest.mark.asyncio
async def test_kodi_source_is_rejected_before_routing_target():
    f=Fake();f.players[0].update(provider='kodi',name='Renamed TV')
    assert (await f.run('join'))['status']=='failed'
    assert not f.actions and not f.mutations()
