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
