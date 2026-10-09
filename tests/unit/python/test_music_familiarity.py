"""Familiarity priors must remain weak, distinct from behaviour, and read-only."""
import asyncio
import time
import pytest
import library as lib
from lib import music_familiarity as f

NOW = 1_800_000_000.0


def song(uri='library://track/1', name='Song', artist='Artist', **extra):
    return dict(uri=uri, name=name, artists=[{'name': artist}], media_type='track',
                provider='library' if uri.startswith('library:') else 'soundcloud--test', **extra)


def local(uri='library://track/1', origin='automatic'):
    return {'uri':uri, 'title':'Song', 'artist':'Artist', 'seconds':150, 'reward':.05 if origin=='automatic' else 1}


def test_raw_counts_are_capped_and_repeat_polls_do_not_increase_them():
    c={};f.observe(c,song(),'ma',count=1,now=NOW)
    before=f.boost(song(),c,[],now=NOW)
    for _ in range(100):f.observe(c,song(),'ma',count=1000000,now=NOW+50)
    assert f.boost(song(),c,[],now=NOW)==before
    huge={};f.observe(huge,song(),'ma',count=1e30,now=NOW)
    assert f.boost(song(),huge,[],now=NOW)==f.MAX_BOOST


def test_overlapping_providers_and_ma_take_max_not_sum():
    c={};f.observe(c,song(),'ma',count=4,now=NOW)
    before=f.boost(song(),c,[],now=NOW)
    f.observe(c,song('soundcloud--test://track/7'),'provider:sc',count=4,history=True,now=NOW)
    assert f.boost(song(),c,[],now=NOW)==before


def test_aliases_match_library_and_provider_tracks_without_title_fuzziness():
    c={};f.observe(c,song('soundcloud--test://track/7'),'provider:sc',history=True,now=NOW)
    mapped=song(name='Renamed',provider_mappings=[{'provider_instance':'soundcloud--test','item_id':'7'}])
    assert f.boost(mapped,c,[],now=NOW)==.10
    assert f.boost(song(name='Song Remix'),c,[],now=NOW)==0


def test_local_manual_and_automatic_listening_are_not_counted_again():
    for origin in ('manual','automatic'):
        c={};f.observe(c,song(),'ma',count=1,rank=1,history=True,now=NOW)
        assert f.boost(song(),c,[local(origin=origin)],now=NOW)==0
        f.observe(c,song(),'ma',count=200,now=NOW+60)
        assert f.boost(song(),c,[local(origin=origin)],now=NOW+60)==0


def test_bad_counts_and_expired_evidence_have_no_influence():
    for value in (True,-3,float('inf'),float('nan'),'invalid'):
        c={};f.observe(c,song(),'ma',count=value,now=NOW)
        assert f.boost(song(),c,[],now=NOW)==0
    c={};f.observe(c,song(),'ma',count=100,now=NOW)
    assert f.boost(song(),c,[],now=NOW+f.RETENTION+1)==0
    assert f.boost(song(),c,[],now=NOW-1)==0


def test_imported_history_does_not_become_a_trusted_seed_or_mood_training(tmp_path):
    m=lib.LocalModel(tmp_path/'x.sqlite3');c={}
    s=song();f.observe(c,s,'provider:sc',history=True,now=time.time())
    m.put_kv('music_familiarity',c)
    assert m.rank([dict(name='Song',artist='Artist',uri=s['uri'])],{},20)==[]
    assert m._history()==[]
    m.db.close()


def test_deliberate_listening_and_explicit_dislike_outweigh_counts(tmp_path):
    m=lib.LocalModel(tmp_path/'x.sqlite3');c={}
    f.observe(c,song('library://track/2',name='Popular'),'ma',count=1e9)
    m.put_kv('music_familiarity',c)
    chosen={'name':'Choice','artist':'Chosen artist','uri':'library://track/1','trusted':True}
    popular={'name':'Popular','artist':'Artist','uri':'library://track/2','trusted':True}
    m.record_listen(dict(chosen,selection_origin='manual',duration=200),150,{})
    # The existing two-hour repeat suppression must continue to apply independently.
    m.db.execute('UPDATE listens SET ts=?',(time.time()-3*3600,));m.db.commit()
    assert m.rank([popular,chosen],{},2)[0]['name']=='Choice'
    m.put_kv('music_feedback',{popular['uri']:'dislike'})
    assert [x['name'] for x in m.rank([popular,chosen],{},2)]==['Choice']
    m.db.close()


@pytest.mark.asyncio
async def test_collect_uses_only_explicit_personal_history_and_ignores_public_counts():
    calls=[]
    async def command(name,args):
        calls.append((name,args))
        if name=='music/tracks/library_items':
            return [song(play_count=9,last_played=NOW),song('library://track/2',name='Unplayed',last_played=0),
                    song('library://track/3',name='Rank only',last_played=NOW)]
        if name=='music/playlists/library_items':return []
        if name=='providers':return [{'type':'music','available':True,'instance_id':'soundcloud--test','domain':'soundcloud','supported_features':['browse']}]
        path=args.get('path','')
        if name=='music/browse' and path=='soundcloud--test://':
            return [{'media_type':'folder','item_id':'recommendations','path':path+'recommendations'}]
        if name=='music/browse' and path.endswith('recommendations'):
            return [{'media_type':'folder','item_id':'sc:recently-played:user','path':'soundcloud--test://history'},
                    {'media_type':'folder','item_id':'because_you_listened_to_song','path':'soundcloud--test://related'},
                    {'media_type':'folder','item_id':'recently-played','path':'other-provider://history'}]
        if name=='music/browse' and path.endswith('/history'):
            return [{'media_type':'playlist','item_id':'personal','provider':'soundcloud'}]
        if name=='music/playlists/playlist_tracks':return [song('soundcloud--test://track/9',name='Heard',playback_count=99999999)]
        raise AssertionError((name,args))
    c,status=await f.collect(command,{},now=NOW)
    assert status['ma_count_items']==2
    assert status['provider_history_items']==1
    assert 'library://track/2' not in c
    assert c['soundcloud--test://track/9']['signals']['provider:soundcloud--test']['count']==0
    assert f.boost(song('soundcloud--test://track/9',name='Heard'),c,[],now=NOW)==.1
    assert not any('related' in args.get('path','') or 'other-provider' in args.get('path','') for _,args in calls)
    assert {name for name,_ in calls} <= {'music/tracks/library_items','music/playlists/library_items','providers','music/browse','music/playlists/playlist_tracks'}
    again,_=await f.collect(command,c,now=NOW+900)
    assert again==c


@pytest.mark.asyncio
async def test_source_failures_retain_cache_and_do_not_rewrite_local_listens():
    c={};f.observe(c,song(),'ma',count=4,now=NOW)
    async def unavailable(*args):raise ConnectionError('offline')
    result,status=await f.collect(unavailable,c,now=NOW+10)
    assert result==c
    assert status['unavailable']==['music/tracks/library_items','music/playlists/library_items','providers']


@pytest.mark.asyncio
async def test_cancellation_is_propagated():
    async def cancelled(*args):raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):await f.collect(cancelled,{},now=NOW)


def test_library_merges_history_metadata_without_overwriting_favourites(tmp_path,monkeypatch,mock_config):
    monkeypatch.setattr(lib,'DB_PATH',tmp_path/'service.sqlite3')
    monkeypatch.setattr(lib,'CACHE_LIBRARY',tmp_path/'absent.json')
    mock_config({'device':'Test'})
    service=lib.LibraryService()
    service._seed_metadata={'library://track/1':{'uri':'library://track/1','name':'Song','artist':'Artist','favorite':True,'media_type':'track'}}
    c={};f.observe(c,song(),'ma',count=50)
    f.observe(c,song('soundcloud--test://track/7',name='Another'),'provider:sc',history=True)
    service.model.put_kv('music_familiarity',c)
    items={item['uri']:item for item in service._load_library()}
    assert items['library://track/1']['favorite'] is True
    assert not items['soundcloud--test://track/7'].get('favorite')
    assert not items['soundcloud--test://track/7'].get('trusted')
    assert service.model._history()==[]
    service.model.db.close()


@pytest.mark.asyncio
async def test_service_import_persists_separate_snapshot_with_read_only_ma_requests(tmp_path,monkeypatch,mock_config):
    monkeypatch.setattr(lib,'DB_PATH',tmp_path/'service.sqlite3')
    mock_config({'device':'Test'})
    service=lib.LibraryService();calls=[]
    class Response:
        def __init__(self,value):self.value=value
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        def raise_for_status(self):pass
        async def json(self):return self.value
    class Session:
        def post(self,url,headers,json):
            calls.append(json)
            return Response([song(play_count=5)] if json['command']=='music/tracks/library_items' else [])
    service.session=Session()
    await service._refresh_familiarity()
    assert service.model.get_kv('music_familiarity')['library://track/1']['signals']['ma:count']['count']==5
    assert service._familiarity_status['ma_count_items']==1
    assert service.model._history()==[]
    assert [x['command'] for x in calls]==['music/tracks/library_items','music/playlists/library_items','providers']
    assert calls[0]['args']['order_by']=='play_count_desc'
    service.model.db.close()

@pytest.mark.asyncio
async def test_most_played_playlists_are_paginated_case_insensitive_baselines():
    calls=[]
    async def command(name,args):
        calls.append((name,args))
        if name=='music/playlists/library_items':
            if args['offset']==0:
                return [{'name':'Editorial mix','item_id':str(i),'provider':'library'} for i in range(99)]+[{'name':' Most Played 2024','provider':'library','item_id':'one','uri':'library://playlist/one'}]
            return [{'name':'most played April','provider':'tidal--test','item_id':'two','uri':'tidal--test://playlist/two'},
                    {'name':'My most played guesses','item_id':'bad','provider':'library'}]
        if name=='music/playlists/playlist_tracks':
            assert args['item_id'] in ('one','two')
            return [song('tidal://track/1',name='Baseline',playback_count=1000000)]
        return []
    c,status=await f.collect(command,{},now=NOW)
    assert status['baseline_playlists']==2 and status['baseline_tracks']==2
    prior=f.Prior(c,[],now=NOW)
    assert prior.baseline(song('library://track/99',name='Baseline'))
    assert .1 <= prior.boost(song('library://track/99',name='Baseline')) <= f.MAX_BOOST
    assert c['tidal://track/1']['signals']['reference-rank']['count']==2
    again,_=await f.collect(command,c,now=NOW+900)
    assert again==c
    assert f.Prior(c,[],now=NOW+f.RETENTION+1).baseline(song('tidal://track/1',name='Baseline')) is False


def test_most_played_creates_familiar_pool_without_favourites_or_fake_listens(tmp_path):
    m=lib.LocalModel(tmp_path/'baseline.sqlite3');c={}
    base=song('tidal://track/1',name='Baseline')
    f.observe(c,base,'most-played:one',history=True,baseline=True)
    m.put_kv('music_familiarity',c)
    candidate={'name':'Baseline','artist':'Artist','uri':base['uri'],'duration':200}
    assert m.rank([candidate],{},1)==[candidate]
    assert not candidate.get('favorite') and not candidate.get('trusted')
    assert m._history()==[]
    m.put_kv('music_feedback',{base['uri']:'dislike'})
    assert m.rank([candidate],{},1)==[]
    m.db.close()


@pytest.mark.asyncio
async def test_mix_exclusions_are_applied_before_top_fifty(tmp_path,monkeypatch,mock_config):
    monkeypatch.setattr(lib,'DB_PATH',tmp_path/'service.sqlite3');mock_config({'device':'Test'})
    service=lib.LibraryService()
    items=[dict(uri=f'track://{i}',name=f'Track {i}',artist=f'Artist {i}',duration=200,trusted=True) for i in range(100)]
    service._load_library=lambda:items
    service._context_for_room=lambda room:{}
    picks=await service._mix_recommend('lounge',50,None,{}, {'exclude':[i['uri'] for i in items[:70]]})
    assert len(picks)==30
    assert not {i['uri'] for i in picks}&{i['uri'] for i in items[:70]}
    service.model.db.close()


@pytest.mark.asyncio
async def test_daily_check_survives_service_restart(tmp_path,monkeypatch,mock_config):
    monkeypatch.setattr(lib,'DB_PATH',tmp_path/'service.sqlite3');mock_config({'device':'Test'})
    service=lib.LibraryService();service.model.put_kv('music_familiarity_last_check',NOW)
    monkeypatch.setattr(lib.time,'time',lambda:NOW+60)
    waits=[]
    async def sleep(seconds):
        waits.append(seconds);raise asyncio.CancelledError()
    monkeypatch.setattr(lib.asyncio,'sleep',sleep)
    with pytest.raises(asyncio.CancelledError):await service._familiarity_loop()
    assert waits==[86340]
    service.model.db.close()
