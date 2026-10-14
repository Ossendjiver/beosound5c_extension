import importlib.util
import time
from pathlib import Path
from types import SimpleNamespace
import pytest
import library
from lib import ipod_sync, ipod_library


def item():
    return {'uri':'library://track/1','title':'Test song','artist':'Artist','media_type':'track','duration':200}


def test_logs_merge_resumes_and_keep_skips(tmp_path):
    rb=tmp_path/'.rockbox';rb.mkdir()
    record={'path':'HomeMedia/Music/test.mp3','item':item()}
    (rb/'playback.log').write_text('1700000000:20000:200000:/HomeMedia/Music/test.mp3\n1700000001:10000:200000:/HomeMedia/Music/test.mp3\n# comment\n')
    (rb/'playback_0001.log').write_text('1700000000:180000:200000:/HomeMedia/Music/test.mp3\n1700000002:180000:200000:/unmanaged.mp3\n')
    events,unmatched=ipod_sync.history(tmp_path,{'1':record},'device')
    assert [(e['seconds'],e['reason']) for e in events]==[(180,'complete'),(10,'skip')]
    assert unmatched==1
    assert events==ipod_sync.history(tmp_path,{'1':record},'device')[0]


def test_path_and_deletion_protect_unmanaged_files(tmp_path):
    unmanaged=tmp_path/'existing.mp3';unmanaged.write_bytes(b'original')
    with pytest.raises(ValueError):ipod_sync.safe_path(tmp_path,'../../outside')
    (tmp_path/'escape').symlink_to('/tmp')
    with pytest.raises(ValueError):ipod_sync.safe_path(tmp_path,'escape/file')
    with pytest.raises(ValueError):ipod_sync.prune(tmp_path,{'1':{'path':'existing.mp3'}},{})
    assert unmanaged.read_bytes()==b'original'


def test_atomic_copy_and_capacity(tmp_path):
    source=tmp_path/'source.mp3';source.write_bytes(b'a'*3000)
    mount=tmp_path/'ipod';mount.mkdir()
    assert not ipod_sync.copy_track(source,mount,'HomeMedia/Music/song.mp3',10**20)
    assert ipod_sync.copy_track(source,mount,'HomeMedia/Music/song.mp3',0)
    assert (mount/'HomeMedia/Music/song.mp3').read_bytes()==source.read_bytes()
    assert not list(mount.rglob('*.part'))


def test_import_receipts_survive_repeated_dock(tmp_path):
    model=library.LocalModel(tmp_path/'model.sqlite')
    service=SimpleNamespace(model=model,_context_for_room=lambda room:{'room':room,'hour':12,'time_bucket':'day','weekpart':'weekday'})
    event=dict(item(),event_id='ipod_'+'a'*64,timestamp_ms=int(time.time()*1000),seconds=180,reason='complete')
    assert ipod_library.record_history(service,[event])['accepted']==1
    assert ipod_library.record_history(service,[event])['accepted']==0
    assert model.db.execute('SELECT COUNT(*) FROM listens').fetchone()[0]==1
    model.db.close()


def test_unplugged_device_refuses_host_directory(tmp_path,monkeypatch):
    spec=importlib.util.spec_from_file_location('ipod_worker',Path(__file__).parents[3]/'services/ipod_sync.py')
    worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
    monkeypatch.setattr(worker,'run',lambda args:'/dev/unrelated')
    with pytest.raises(RuntimeError,match='mount changed'):worker.require_mount(tmp_path,{'name':'/dev/ipod1'})

@pytest.mark.asyncio
async def test_yoga_uses_valid_file_browse_parameters():
    service=object.__new__(library.LibraryService)
    service.session=object();service.cfg={}
    service._yoga_target_url=lambda room:'http://kodi:8080/jsonrpc'
    calls=[]
    async def rpc(url,method,args):
        calls.append((method,args))
        if method=='Files.GetDirectory':
            assert args=={'directory':'smb://LOUNGE/SSD/Exercise/','media':'files'}
            return {'files':[{'file':'smb://LOUNGE/SSD/Exercise/Yoga For Runners_ 7 Minute Post-Run Yoga.mp4','label':'Yoga For Runners_ 7 Minute Post-Run Yoga.mp4'}]}
        return 'OK'
    service._kodi_rpc=rpc
    await service._run_yoga('bedroom')
    assert calls[-1][0]=='Player.Open'
    assert 'Post-Run' in calls[-1][1]['item']['file']

@pytest.mark.asyncio
async def test_catalogue_includes_predicted_radio_without_playback(tmp_path):
    model=library.LocalModel(tmp_path/'model.sqlite')
    songs=[dict(item(),uri='library://track/'+str(i),title='Song '+str(i),artist='Artist '+str(i),favorite=i<3,trusted=True,playlist_tags='Selected') for i in range(8)]
    calls=[]
    async def recommend(room,limit,mood,seed,policy):
        calls.append(policy);return [songs[5],songs[6]]
    service=SimpleNamespace(model=model,cfg={'trusted_playlists':['Selected']},_load_library=lambda:songs,
        _context_for_room=lambda r:{'room':r,'hour':12,'time_bucket':'day','weekpart':'weekday'},_mix_recommend=recommend)
    plan=await ipod_library.catalogue(service,8)
    assert len({i['uri'] for i in plan['items']})==len(plan['items'])
    assert calls and all(c=={'mode':'radio'} for c in calls)
    assert any(g.startswith('Radio') for g in plan['groups'])
    model.db.close()

@pytest.mark.asyncio
async def test_ipod_strict_priority_and_trial_exposure(tmp_path):
    model=library.LocalModel(tmp_path/'model.sqlite')
    songs=[dict(item(),uri='library://track/'+str(i),title='Song '+str(i),artist='Artist '+str(i),favorite=i==0) for i in range(7)]
    context={'room':'ipod','hour':12,'time_bucket':'afternoon','weekpart':'weekday'}
    model.record_listen(dict(songs[4],end_reason='complete'),200,context)
    model.record_listen(dict(songs[5],end_reason='complete'),200,context)
    model.rank=lambda candidates,ctx,limit: [songs[1]] if limit==3 else [songs[4],songs[3]]
    async def recommend(*args):return [songs[2]]
    service=SimpleNamespace(model=model,cfg={'trusted_playlists':[]},_load_library=lambda:songs,_context_for_room=lambda r:context,_mix_recommend=recommend)
    plan=await ipod_library.catalogue(service,7)
    assert [x['uri'] for x in plan['items']]==[songs[i]['uri'] for i in [0,1,2,3,4,5,6]]
    model.db.close()

def test_trial_exposure_survives_log_cleanup_and_counts_provider_aliases_once(tmp_path):
    model=library.LocalModel(tmp_path/'model.sqlite');context={'room':'ipod'}
    song=item()
    model.record_listen(dict(song,end_reason='skip'),5,context)
    model.record_listen(dict(song,uri='tidal://track/2',end_reason='skip'),8,context)
    exposure=ipod_library.recorded_exposure(model.db)
    assert exposure[ipod_library.exposure_key(song)]==(0,2)
    assert ipod_library.recorded_exposure(model.db)==exposure
    model.db.execute('DELETE FROM listens');model.db.commit()
    assert ipod_library.recorded_exposure(model.db)==exposure
    model.db.close()

@pytest.mark.asyncio
async def test_small_selection_balances_morning_afternoon_evening_radios(tmp_path):
    model=library.LocalModel(tmp_path/'model.sqlite')
    songs=[dict(item(),uri='library://track/'+str(i),title='Song '+str(i),artist='Artist '+str(i)) for i in range(9)]
    model.rank=lambda candidates,ctx,limit: songs[{'morning':0,'afternoon':3,'evening':6}.get(ctx.get('time_bucket'),0):][:limit] if limit==3 else []
    async def recommend(*args):return []
    service=SimpleNamespace(model=model,cfg={'trusted_playlists':[]},_load_library=lambda:songs,_context_for_room=lambda r:{},_mix_recommend=recommend)
    plan=await ipod_library.catalogue(service,3)
    assert [x['uri'] for x in plan['items']]==[songs[i]['uri'] for i in [0,3,6]]
    model.db.close()
