import json
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
import library as lib
from lib import provider_profiles as p, music_features
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'tools'))
import profile_provider_library as worker

ITEM={'uri':'tidal--p://track/1','name':'Song','artists':[{'name':'Artist'}],'duration':200,'aliases':['library://track/5','tidal--p://track/1']}
RECORD={'id':'12345678-1234-1234-1234-123456789abc','title':'Song','artist-credit':[{'name':'Artist'}],'length':200000}
FEATURES={'model':'emomusic','energy':.4,'valence':.6}
class Analyzer:
    fingerprint='model-v1'
    def analyze(self,audio):return dict(FEATURES)
class Online:
    token=''
    def get(self,url):return None

def detail(meta=None):return {**ITEM,'media_type':'track','item_id':'1','metadata':meta or {}}

def run(entries=None,meta=None,players=None,payload=None,**kw):
    calls=[]
    def command(name,args):
        calls.append(name)
        if name=='music/tracks/get':return {**detail(meta),'item_id':args['item_id']}
        if name=='players/all':return players if players is not None else []
        if name=='music/tracks/preview':return '/preview?token=private'
        raise AssertionError(name)
    state=payload or {'version':1,'tracks':{}}
    counts=worker.update(entries or [dict(ITEM)],state,command,Online(),Analyzer(),set(),now=100,preview=kw.pop('preview',lambda *a:b'bytes'),decode=kw.pop('decode',lambda a:['audio']),**kw)
    return state,counts,calls

@pytest.mark.parametrize('change',[{'length':500000},{'title':'Song (Live)'},{'artist-credit':[{'name':'Someone'}]}])
def test_recording_match_rejects_other_duration_version_artist(change):
    assert not p.recording_match(ITEM,{**RECORD,**change})

def test_ambiguous_recording_does_not_choose_first():
    assert p.choose_recording(ITEM,[RECORD,{**RECORD,'id':'other'}]) is None
    assert p.choose_recording(ITEM,[RECORD])==RECORD

def test_version_words_are_not_dropped():
    assert not p.recording_match({**ITEM,'version':'Remix'},RECORD)

def acoustic():
    return {'metadata':{'audio_properties':{'length':200}},'highlevel':{
        'mood_'+k:{'all':{k:v,'not_'+k:1-v}} for k,v in [('happy',.8),('sad',.1),('relaxed',.2),('aggressive',.7)]}}

def test_archive_coordinates_require_consistent_duration_and_confident_classifiers():
    d=acoustic();assert p.useful(p.acoustic_metadata(ITEM,d,RECORD['id']))
    d['metadata']['audio_properties']['length']=400;assert not p.acoustic_metadata(ITEM,d,RECORD['id'])
    d=acoustic();d['highlevel']['mood_happy']['all']={'happy':.5,'not_happy':.5};assert not p.acoustic_metadata(ITEM,d,RECORD['id'])
    d=acoustic();d['metadata']['tags']={'musicbrainz_recordingid':['different']};assert not p.acoustic_metadata(ITEM,d,RECORD['id'])

def test_online_metadata_prevents_any_preview():
    state,counts,calls=run(meta={'mood':'relaxed','style':'Ambient','bpm':80,'audio_features':FEATURES})
    assert counts['metadata']==1 and 'music/tracks/preview' not in calls
    assert state['tracks'][ITEM['uri']]['metadata']['audio_features']['bpm']==80

@pytest.mark.parametrize('state',['playing','paused','unknown',None])
def test_busy_players_defer_sampling(state):
    result,counts,calls=run(players=[{'playback_state':state,'available':True}])
    assert counts['busy']==1 and 'music/tracks/preview' not in calls

def test_preview_is_last_resort_and_private_url_not_persisted():
    state,counts,calls=run()
    assert counts['sampled']==1 and counts['sample_attempts']==1
    assert 'private' not in json.dumps(state)
    assert state['tracks'][ITEM['uri']]['source']=='ma_preview'

def test_sample_attempt_budget_also_counts_failures():
    def failing(*a):raise OSError('signed-private-url')
    entries=[{**ITEM,'uri':f'tidal--p://track/{i}','aliases':[]} for i in [1,2,3]]
    def command(name,args):
        if name=='music/tracks/get':return {**detail(),'item_id':args['item_id']}
        return [] if name=='players/all' else '/private'
    state={'version':1,'tracks':{}}
    count=worker.update(entries,state,command,Online(),Analyzer(),set(),now=100,max_samples=1,preview=failing)
    assert count['sample_attempts']==1 and count['failed']==1
    assert count['deferred']==2 and 'signed-private' not in json.dumps(state)

def test_previous_good_profile_survives_preview_failure():
    old={'metadata':{'audio_features':FEATURES},'source':'ma_preview','analysis_fingerprint':'old','input_fingerprint':p.fingerprint(ITEM)}
    payload={'version':1,'tracks':{ITEM['uri']:old}}
    def failing(*a):raise OSError('secret')
    state,counts,calls=run(payload=payload,preview=failing)
    assert state['tracks'][ITEM['uri']]['metadata']['audio_features']==FEATURES


def test_refresh_preserves_aliases_and_reuses_unchanged_profile():
    old={'metadata':{'genres':['ambient']},'next_check':500,'input_fingerprint':p.fingerprint(ITEM)}
    state,counts,calls=run(payload={'version':1,'tracks':{ITEM['uri']:old}})
    assert counts['unchanged']==1 and not calls
    assert state['tracks'][ITEM['uri']]['aliases']==ITEM['aliases']


def test_optional_online_failure_does_not_block_preview():
    class Failed(Online):
        def get(self,url):raise RuntimeError('offline')
    meta,sources,_=worker.online_metadata(ITEM,Failed())
    assert not p.useful(meta) and 'musicbrainz:unavailable' in sources


def test_inventory_deduplicates_exact_provider_mappings_preserving_favorites():
    canonical=[{**ITEM,'uri':'library://track/5','favorite':False,'provider_mappings':[{'provider_domain':'tidal','provider_instance':'tidal--p','item_id':'1'}]}]
    result=p.inventory(canonical,[{**ITEM,'favorite':True}])
    assert len(result)==1 and result[0]['favorite'] and 'library://track/5' in result[0]['aliases']


def test_metadata_credentials_cannot_redirect_to_other_origin():
    import urllib.request
    req=urllib.request.Request('https://api.discogs.com/test',headers={'Authorization':'secret'})
    with pytest.raises(ValueError):worker.MetadataRedirect().redirect_request(req,None,302,'',{},'https://evil.example/test')


def test_preview_auth_removed_on_public_redirect(monkeypatch):
    import urllib.request
    monkeypatch.setattr(worker,'safe_audio_url',lambda *a:None)
    req=urllib.request.Request('http://localhost:8095/preview',headers={'Authorization':'secret'})
    result=worker.PreviewRedirect('http://localhost:8095').redirect_request(req,None,302,'',{},'https://cdn.example/a')
    assert not result.has_header('Authorization')


def test_private_external_preview_refused():
    with pytest.raises(ValueError):worker.safe_audio_url('https://127.0.0.1/a','http://localhost:8095')
    with pytest.raises(ValueError):worker.safe_audio_url('http://example.org/a','http://localhost:8095')


def test_sidecar_enriches_exact_alias_and_local_profile_wins_over_preview(mock_config,tmp_path,monkeypatch):
    cache=tmp_path/'library.json';cache.write_text(json.dumps([{'id':'songs','tracks':[{'url':'exact','name':'Song','artist':'Artist'},{'url':'different','name':'Song','artist':'Artist'}]}]))
    (tmp_path/'audio_features.json').write_text(json.dumps({'version':1,'tracks':{'exact':{'audio_features':FEATURES}}}))
    sidecar=tmp_path/'provider_profiles.json'
    payload={'version':1,'tracks':{ITEM['uri']:{'aliases':['exact'],'source':'ma_preview','metadata':{'genres':['ambient'],'audio_features':{'model':'other','energy':.1,'valence':.2}}}}}
    sidecar.write_text(json.dumps(payload))
    mock_config({});monkeypatch.setattr(lib,'DB_PATH',tmp_path/'db');monkeypatch.setattr(lib,'CACHE_LIBRARY',cache)
    service=lib.LibraryService();songs={i['uri']:i for i in service._load_library()}
    assert songs['exact']['audio_features']==FEATURES and songs['exact']['genres']==['ambient']
    assert 'audio_features' not in songs['different']
    payload['tracks'][ITEM['uri']]['source']='online_metadata';sidecar.write_text(json.dumps(payload))
    assert service._load_library()[0]['audio_features']['model']=='other'
    service.model.db.close()


def test_canonical_ma_response_requires_exact_requested_provider_mapping():
    canonical={'item_id':'999','provider':'library','provider_mappings':[{'provider_instance':'tidal--p','provider_domain':'tidal','item_id':'1'}]}
    assert p.provider_identity(canonical,'tidal--p','1')
    assert p.provider_identity(canonical,'tidal','1')
    assert not p.provider_identity(canonical,'tidal--different','1')
    assert not p.provider_identity(canonical,'tidal--p','2')


def test_full_sweep_ignores_ttl_and_resumes_completed_entries_without_repeat():
    old={'metadata':{'genres':['ambient']},'next_check':500,'input_fingerprint':p.fingerprint(ITEM)}
    payload={'version':1,'tracks':{ITEM['uri']:old},'sweep':{'id':'sweep','total':1,'visited':0}}
    state,counts,calls=run(meta={'genres':['ambient'],'audio_features':FEATURES},payload=payload,full_sweep=True)
    assert counts['metadata']==1 and calls and state['sweep']['visited']==1
    state,counts,calls=run(meta={'genres':['ambient']},payload=state,full_sweep=True)
    assert counts['unchanged']==1 and not calls


def test_full_sweep_reuses_unchanged_recent_preview_before_sampling():
    old={'metadata':{'audio_features':FEATURES},'source':'ma_preview','analysis_fingerprint':Analyzer.fingerprint,'next_check':500,'input_fingerprint':p.fingerprint(ITEM)}
    payload={'version':1,'tracks':{ITEM['uri']:old},'sweep':{'id':'full','total':1,'visited':0}}
    state,counts,calls=run(payload=payload,full_sweep=True)
    assert counts['preview_reused']==1 and 'music/tracks/preview' not in calls


def test_audio_provenance_retained_when_online_tags_refresh_preview():
    old={'metadata':{'audio_features':FEATURES},'source':'ma_preview','analysis_fingerprint':Analyzer.fingerprint,'input_fingerprint':p.fingerprint(ITEM)}
    state,counts,calls=run(meta={'genres':['ambient']},payload={'version':1,'tracks':{ITEM['uri']:old}})
    metadata,source=p.load(state)[ITEM['uri']]
    assert source=='ma_preview' and metadata['audio_features']==FEATURES
def test_pending_retry_keeps_ready_profiles_and_bypasses_only_needed_ttls():
    from profile_provider_library import pending_entries
    entries=[{'uri':str(n)} for n in range(4)]
    payload={'tracks':{'0':{'status':'sampling_deferred_busy','next_check':999},
                       '1':{'status':'awaiting_sample','next_check':999},
                       '2':{'status':'sample_ready','next_check':999},
                       '3':{'status':'metadata_ready','next_check':999,'metadata':{'audio_features':FEATURES}}}}
    assert pending_entries(entries,payload)==entries[:2]
    assert payload['tracks']['0']['next_check']==0
    assert payload['tracks']['2']['next_check']==999

def test_progress_status_counts_successful_samples_and_pending_retries():
    payload={'tracks':{'a':{'status':'sample_ready'},'b':{'status':'metadata_ready'},'c':{'status':'retry_pending','error':'Timeout'},'d':{'status':'sampling_deferred_busy'}}}
    status=worker.progress_status(payload,running=True)
    assert status['successful_samples']==1
    assert status['sweep']['pending_samples']==3
    assert status['sweep']['errors']==1
    assert status['running'] is True

def test_truncated_preview_is_retryable_and_next_track_continues():
    import http.client
    entries=[dict(ITEM),{**ITEM,'uri':'tidal--p://track/2'}]
    attempts=[]
    def preview(*args):
        attempts.append(1)
        if len(attempts)==1:raise http.client.IncompleteRead(b'')
        return b'bytes'
    state,counts,_=run(entries=entries,preview=preview)
    assert state['tracks'][entries[0]['uri']]['status']=='retry_pending'
    assert state['tracks'][entries[0]['uri']]['error']=='IncompleteRead'
    assert counts['sampled']==1 and counts['failed']==1

def test_automatic_resume_respects_failed_track_backoff():
    import time
    entries=[{'uri':'a'},{'uri':'b'}]
    payload={'tracks':{'a':{'status':'retry_pending','next_check':time.time()+86400},'b':{'status':'sampling_deferred_busy','next_check':time.time()+86400}}}
    assert worker.pending_entries(entries,payload,respect_retry_backoff=True)==[entries[1]]
    assert payload['tracks']['a']['next_check']>time.time()

def test_genre_hints_do_not_suppress_needed_audio_sampling():
    state,counts,_=run(meta={'genres':['Classical']})
    assert counts['sampled']==1
    assert state['tracks'][ITEM['uri']]['metadata']['genres']==['Classical']
    assert not p.useful({'genres':['Classical']})
    payload={'tracks':{'a':{'status':'metadata_ready','metadata':{'genres':['Jazz']},'next_check':999}}}
    assert worker.pending_entries([{'uri':'a'}],payload)==[{'uri':'a'}]
