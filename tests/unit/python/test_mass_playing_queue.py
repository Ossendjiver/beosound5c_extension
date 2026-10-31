import asyncio
import pytest
import sources.mass.service as module
from sources.mass.service import MassSource

@pytest.mark.parametrize('snapshot,title,artist,bound', [
 ({}, '', '', False),
 ({'state':'playing','current_item':{'name':'Song','media_item':{'name':'Song','artists':[{'name':'Artist'}]}}},'Other','Artist',False),
 ({'state':'playing','current_item':{'name':'Song','media_item':{'name':'Song','artists':[{'name':'Artist'}]}}},'Song','Artist',True),
 ({'state':'playing','current_item':{'name':'Song'}},'','',False),
 ({'state':'idle','items':[]},'','',True),
])
def test_queue_is_bound_only_to_verified_display(monkeypatch,snapshot,title,artist,bound):
    calls=[]
    monkeypatch.setattr(module,'cfg',lambda *args,**kw:'verified-link')
    source=object.__new__(MassSource)
    async def get(player): calls.append(player);return snapshot
    source._get_queue_snapshot=get
    result=asyncio.run(source._playing_queue_snapshot(title,artist))
    assert result['bound'] is bound
    assert result['read_only'] is True
    assert calls == ['verified-link']


def test_verified_queue_exposes_stable_ids_uris_and_scoped_capabilities(monkeypatch):
    monkeypatch.setattr(module,'cfg',lambda *args,**kw:'verified-link')
    source=object.__new__(MassSource)
    item={'queue_item_id':'current-id','media_item':{'name':'Song','uri':'tidal://one','duration':200,'artists':[{'name':'Artist'}]}}
    async def get(player):return {'state':'playing','current_item':item,'items':[item],'current_index':0,'resolved_queue_id':'canonical-link'}
    source._get_queue_snapshot=get
    result=asyncio.run(source._playing_queue_snapshot('Song','Artist'))
    assert result['read_only'] is False
    assert result['current_item_id']=='current-id' and result['queue_id']=='canonical-link'
    assert result['tracks'][0]['uri']=='tidal://one'
    assert result['tracks'][0]['duration']==200
    assert result['capabilities']['startRadio'] and result['capabilities']['remove']
