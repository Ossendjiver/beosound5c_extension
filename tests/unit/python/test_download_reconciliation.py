import importlib.util,json,hashlib,sys,time
from pathlib import Path
from unittest.mock import Mock
import pytest

SERVICES=Path(__file__).resolve().parents[3]/'services'
sys.path.insert(0,str(SERVICES))

def worker():
    spec=importlib.util.spec_from_file_location('tidal_reconciliation_test',SERVICES/'tidal_download.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def cached(path,now=100):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'fLaCdata')
    return {'path':str(path),'size':8,'quality_policy':2,'quality_checked_at':now,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def test_false_complete_and_empty_pending_cannot_hide_missing_files(tmp_path):
    m=worker();state={'tracks':{'a':cached(tmp_path/'a.flac')},'last_completed':100,'pending_ids':[]}
    summary=m.reconcile(state,{'a':{},'b':{}},now=101)
    assert state['pending_ids']==['b'] and summary['missing']==1 and summary['cached_selected']==1
    (tmp_path/'a.flac').unlink();assert m.reconcile(state,{'a':{},'b':{}},now=101)['missing']==2


def test_invalid_file_size_is_not_cached(tmp_path):
    m=worker();r=cached(tmp_path/'a.flac');r['size']=99
    state={'tracks':{'a':r}};assert m.reconcile(state,{'a':{}},101)['remaining']==1


def test_explicit_policy_exclusions_are_visible_and_expire():
    m=worker();s={'tracks':{},'outcomes':{'a':{'state':'non_explicit','retry_at':200}}}
    assert m.reconcile(s,{'a':{}},100)=={'cached_selected':0,'excluded':1,'missing':0,'remaining':0,'retry_pending':0}
    assert m.reconcile(s,{'a':{}},201)['missing']==1


def test_transient_errors_remain_pending_and_backoff_escalates():
    m=worker();s={'tracks':{}}
    m.record_retry(s,'a','HTTP_503',now=100);assert s['outcomes']['a']['retry_at']==400
    m.record_retry(s,'a','HTTP_503',now=500);assert s['outcomes']['a']['retry_at']==1100
    assert m.reconcile(s,{'a':{}},501)['remaining']==1
    assert s['pending_ids']==['a']


def test_protected_audio_is_unresolved_instead_of_silent_completion():
    m=worker();s={'tracks':{}};m.record_retry(s,'a','protected_audio',now=100,minimum_delay=86400)
    assert m.reconcile(s,{'a':{}},101)['retry_pending']==1


def test_recovery_adopts_verified_file_without_redownloading(tmp_path,monkeypatch):
    m=worker();root=tmp_path/'music';cache=tmp_path/'cache';cache.mkdir()
    monkeypatch.setattr(m,'ROOT',root);monkeypatch.setattr(m,'CACHE',cache)
    record=cached(root/'Artist/Album/Song.flac');(cache/'commit-journal.json').write_text(json.dumps({'id':'1','record':record}))
    state={'tracks':{}};m.recover_commit(state)
    assert state['tracks']['1']==record and json.loads((cache/'commit-journal.json').read_text())=={}


def test_recovery_refuses_unverified_or_escaped_files(tmp_path,monkeypatch):
    m=worker();root=tmp_path/'music';cache=tmp_path/'cache';cache.mkdir()
    monkeypatch.setattr(m,'ROOT',root);monkeypatch.setattr(m,'CACHE',cache)
    record=cached(tmp_path/'outside.flac');(cache/'commit-journal.json').write_text(json.dumps({'id':'1','record':record}))
    with pytest.raises(ValueError):m.recover_commit({'tracks':{}})
    assert (tmp_path/'outside.flac').is_file()


def test_next_refresh_uses_saved_plan_and_reports_pending_without_requesting_before_backoff(tmp_path,monkeypatch):
    m=worker();cache=tmp_path/'cache';cache.mkdir();root=tmp_path/'music';root.mkdir()
    plan={'1':{'uri':'tidal://track/1','name':'Song'}}
    s={'tracks':{},'selection_key':[250,20],'selected_items':plan,'selected_at':time.time(),'outcomes':{'1':{'state':'retry_pending','retry_at':time.time()+3600}}}
    (cache/'index.json').write_text(json.dumps(s))
    monkeypatch.setattr(m,'CACHE',cache);monkeypatch.setattr(m,'ROOT',root);monkeypatch.setattr(m,'CONFIG',tmp_path/'config')
    monkeypatch.setattr(m.subprocess,'check_output',lambda *a,**k:'/media/local\n')
    monkeypatch.setattr(m,'RATE',Mock());monkeypatch.setattr(m,'require_idle',lambda:None)
    req=Mock(side_effect=AssertionError('No provider call before backoff'));monkeypatch.setattr(m,'request',req)
    events=[];monkeypatch.setattr(m,'publish',lambda status,**kw:events.append((status,kw)))
    m.main();assert events[-1][0]=='retry_pending' and events[-1][1]['remaining']==1;req.assert_not_called()


@pytest.mark.parametrize('fail',[False,True])
def test_main_writes_into_existing_album_and_keeps_failed_download_pending(tmp_path,monkeypatch,fail):
    m=worker();cache=tmp_path/'cache';cache.mkdir();root=tmp_path/'music';root.mkdir()
    album=root/'Artist/Album';album.mkdir(parents=True);existing=album/'unmanaged.mp3';existing.write_bytes(b'original')
    item={'uri':'tidal://track/1','name':'Song','album':{'name':'Album'},'artists':[{'name':'Artist'}],'album_artist':'Artist','track_number':1}
    state={'tracks':{},'selection_key':[250,20],'selected_items':{'1':item},'selected_at':time.time()}
    (cache/'index.json').write_text(json.dumps(state))
    monkeypatch.setattr(m,'CACHE',cache);monkeypatch.setattr(m,'ROOT',root);monkeypatch.setattr(m,'CONFIG',tmp_path/'config')
    monkeypatch.setattr(m.subprocess,'check_output',lambda *a,**k:'/media/local\n');monkeypatch.setattr(m,'RATE',Mock())
    monkeypatch.setattr(m,'require_idle',lambda:None);monkeypatch.setattr(m,'auth',lambda:{'country':'AU','token':'not-real'})
    monkeypatch.setattr(m,'request',lambda *a,**k:{'explicit':True});monkeypatch.setattr(m,'with_album_artist',lambda item,*a:item)
    monkeypatch.setattr(m,'maximum_asset',lambda *a:{'bits':24,'sample_rate':96000});monkeypatch.setattr(m.time,'sleep',lambda _:None)
    dests=[]
    def transfer(asset,dest,reserve,item,before_publish=None):
        dests.append(dest)
        if fail:raise OSError('truncated')
        r=cached(dest,now=time.time());r.update(bit_depth=24,sample_rate=96000)
        before_publish(r);return r
    monkeypatch.setattr(m,'transfer',transfer);events=[];monkeypatch.setattr(m,'publish',lambda status,**kw:events.append((status,kw)))
    m.main();saved=json.loads((cache/'index.json').read_text())
    assert dests==[album/'01. Song.flac'] and existing.read_bytes()==b'original'
    assert not (root/'HomeMedia-Tidal').exists()
    if fail:
        assert events[-1][0]=='retry_pending' and saved['pending_ids']==['1']
        assert saved['outcomes']['1']['reason']=='OSError'
    else:
        assert events[-1][0]=='complete' and saved['pending_ids']==[]
        assert saved['tracks']['1']['path']==str(dests[0])
