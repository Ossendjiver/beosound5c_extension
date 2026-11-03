import asyncio
import pytest
from lib import radio_seed, music_features


def seed():
    return {'uri':'tidal--instance://track/156393896','name':'Soon As You Get Home','artist':'Rose Gold','duration':204}


@pytest.mark.asyncio
async def test_external_seed_uses_available_provider_genre_and_caches_once():
    calls=[];cache={}
    async def fetch(uri):
        calls.append(uri)
        return dict(seed(),metadata={'genres':['R&B/Soul']})
    result,changed,status=await radio_seed.enrich(seed(),cache,fetch,1000)
    assert status=='ready' and changed and result['genres']==['R&B/Soul']
    candidate={'artist':'Other artist','genres':['contemporary r&b']}
    assert music_features.radio_distance(candidate,seed()) is None
    assert music_features.radio_distance(candidate,result) is not None
    result,changed,status=await radio_seed.enrich(seed(),cache,fetch,1001)
    assert not changed and len(calls)==1 and status=='ready'


@pytest.mark.asyncio
async def test_canonical_library_item_requires_exact_provider_mapping():
    async def fetch(uri):return {'uri':'library://track/1','metadata':{'genres':['R&B/Soul']},'provider_mappings':[{'item_id':'156393896','provider_instance':'tidal--instance'}]}
    result,_,status=await radio_seed.enrich(seed(),{},fetch,1000)
    assert status=='ready' and result['uri']==seed()['uri']


@pytest.mark.asyncio
@pytest.mark.parametrize('detail',[
 {'uri':'other://track/156393896','item_id':'156393896','metadata':{'genres':['pop']}},
 {'uri':'library://track/1','provider_mappings':[{'item_id':'156393896','provider_instance':'other'}],'metadata':{'genres':['pop']}},
 None])
async def test_wrong_recording_cannot_seed_radio(detail):
    async def fetch(uri):return detail
    result,_,status=await radio_seed.enrich(seed(),{},fetch,1000)
    assert not radio_seed.usable(result) and status=='provider_metadata_unavailable'


@pytest.mark.asyncio
async def test_failure_has_persistent_backoff_without_unrelated_fallback():
    calls=[];cache={}
    async def fetch(uri):calls.append(uri);raise TimeoutError()
    result,changed,status=await radio_seed.enrich(seed(),cache,fetch,1000)
    assert changed and not radio_seed.usable(result)
    result,changed,status=await radio_seed.enrich(seed(),cache,fetch,1001)
    assert not changed and status=='waiting_for_metadata_retry' and len(calls)==1
    await radio_seed.enrich(seed(),cache,fetch,1901)
    assert len(calls)==2


@pytest.mark.asyncio
async def test_existing_audio_needs_no_provider_lookup():
    item=seed()|{'audio_features':{'model':'test','energy':.4,'valence':.5}}
    async def fetch(uri):raise AssertionError('Already profiled')
    result,changed,status=await radio_seed.enrich(item,{},fetch,1000)
    assert status=='ready' and not changed


@pytest.mark.asyncio
async def test_cancellation_is_not_swallowed():
    async def fetch(uri):raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):await radio_seed.enrich(seed(),{},fetch,1000)


@pytest.mark.asyncio
async def test_real_recommender_enriches_external_seed_before_radio_filter(tmp_path,monkeypatch,mock_config):
    import library as lib
    monkeypatch.setattr(lib,'DB_PATH',tmp_path/'recommendations.sqlite3')
    mock_config({'device':'Test'})
    service=lib.LibraryService()
    songs=[{'uri':'library://track/2','name':'Soul choice','artist':'Other artist','genres':['R&B/Soul'],'duration':210,'trusted':True},
           {'uri':'library://track/3','name':'Wrong genre','artist':'Other','genres':['death metal'],'duration':210,'trusted':True}]
    service._mood_candidates=lambda *args:(songs,False)
    service._context_for_room=lambda room:{'room':room,'hour':8,'time_bucket':'morning','weekpart':'weekday'}
    calls=[]
    async def fetch(uri):calls.append(uri);return dict(seed(),metadata={'genres':['R&B/Soul']})
    service._fetch_radio_seed=fetch
    picks=await service._mix_recommend('bedroom',10,None,seed(),{'mode':'radio','queue_id':'link','exclude':[seed()['uri']]})
    assert [p['name'] for p in picks]==['Soul choice']
    assert service.model.last_trace['seed_metadata']=='ready'
    assert len(calls)==1 and service.model.get_kv('track_metadata',{})[seed()['uri']]['genres']==['R&B/Soul']
    service.model.db.close()


@pytest.mark.asyncio
async def test_empty_first_radio_fill_persists_visible_retry_status():
    from lib.mood_mix import MoodMixes
    saved=[]
    snap={'state':'playing','current_index':0,'elapsed_time':5,'current_item':{'queue_item_id':'seed','duration':204,'media_item':seed()},'items':[{'queue_item_id':'seed','media_item':seed()}]}
    async def command(name,**kwargs):
        assert name=='mood_snapshot'
        return {'state':'ok','snapshot':snap}
    async def recommend(*args):return []
    mixes=MoodMixes(command,recommend,save=lambda value:saved.append(value))
    await mixes.begin('link','bedroom',seed(),mode='radio')
    assert mixes.public('link')['refresh_status']=='waiting_for_candidates'
    assert mixes.public('link')['last_error']
    assert saved[-1]['sessions']['link']['refresh_status']=='waiting_for_candidates'
