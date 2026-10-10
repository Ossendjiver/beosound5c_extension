"""Independent regression cases for eligibility, uncertainty and feedback."""
import math
import time
import pytest
import library
from lib import music_features, music_mood, profile_quality, sequence_plan, session_intent, taste_profiles
from lib import profile_priorities, recommendation_audit
from lib.session_feedback import SessionFeedback


def song(uri, e=.5, v=.5, vector=None, artist=None, trusted=True):
    return {'uri': uri, 'name': uri, 'artist': artist or uri, 'genre': 'deep house',
            'duration': 240, 'trusted': trusted, 'audio_features': {'model': 'm',
            'embedding_model': 'emb', 'embedding': vector or [1.]+[0.]*7,
            'energy': e, 'valence': v, 'profile_confidence': .9, 'bpm': 100}}


def test_balanced_recording_can_be_selected_by_wheel(tmp_path):
    track = song('balanced')
    assert any(music_mood.compatible(track, music_mood.selection(angle, 0), {}) for angle in range(360))
    model = library.LocalModel(tmp_path/'db')
    assert model.rank([track], {'mood': music_mood.selection(0, 0)}, 1)[0]['uri']=='balanced'


def test_known_audio_conflict_cannot_be_overruled_by_same_genre():
    root, other = song('seed', .2), song('other', .9, vector=[0., 1.]+[0.]*6)
    assessment = music_features.radio_audio_assessment(other, root)
    assert assessment['status']=='incompatible'
    assert music_features.radio_distance(other, root) is None
    other['audio_features']['profile_confidence']=.4
    root['audio_features']['profile_confidence']=.4
    # Short/uncertain previews still permit a conservative genre fallback.
    assert music_features.radio_distance(other, root) is not None
    assert music_features.radio_distance({'genre':'deep house'}, root) is not None


def test_nonfinite_quality_and_sections_cannot_enter_profiles():
    cleaned = music_features.clean({'model':'m','energy':.5,'valence':.5,
        'profile_confidence':float('nan'), 'profile_quality_version':1,
        'section_moods':[{'energy':.5,'valence':.6},{'energy':math.inf,'valence':.4}],
        'classifiers':{'voice':.8,'unknown':.9,'acoustic':-1}})
    assert 'profile_confidence' not in cleaned
    assert cleaned['section_moods']==[{'energy':.5,'valence':.6}]
    assert cleaned['classifiers']=={'voice':.8}
    assert profile_quality.summary([{'energy':.5,'valence':.5}],1,30)['profile_confidence'] < \
        profile_quality.summary([{'energy':.5,'valence':.5}]*3,3,90)['profile_confidence']


def test_personal_calibration_is_bounded_and_does_not_change_discovery():
    target = music_mood.selection(0, .8)
    adjusted = music_mood.resolve(target, {'anchors':[{'angle':0,'energy':.2,'valence':.2}],'revision':1})
    assert .2<adjusted['energy']<target['energy']
    assert adjusted['discovery_percent']==target['discovery_percent']
    assert music_mood.resolve(target,{'anchors':[{'angle':0,'energy':float('nan'),'valence':.2}]})==target


def test_gradual_steering_reaches_target():
    target=music_mood.selection(90, .8)
    path=music_mood.trajectory(target,{'energy':.1,'valence':.2})
    assert len(path)==3
    assert path[-1]['energy']==target['energy']
    assert all(path[i]['energy']<path[i+1]['energy'] for i in range(2))
    assert all(p['discovery_fraction']==target['discovery_fraction'] for p in path)


@pytest.mark.parametrize('reason',['pause','transfer','route_change','error','network_error','buffering','disconnect'])
def test_technical_events_do_not_train_taste(reason):
    assert session_intent.reward(250,300,'manual',reason)==0


def test_short_contact_with_long_mix_is_not_a_manual_preference():
    assert session_intent.reward(100,3600,'manual','stop')==0
    assert session_intent.reward(1000,3600,'manual','stop')>=1


def test_multiple_distinct_contextual_tastes_are_not_blended():
    a= song('classical',vector=[1.]+[0.]*7)
    b= song('techno',vector=[0.,1.]+[0.]*6)
    now=time.time()
    history=[{'uri': item['uri'],'artist':item['artist'],'title':item['name'],
              'origin':'manual','reward':1.5,'ts':now-i,'hour':hour,'room':'bedroom'}
             for item,hour in [(a,8),(b,20)] for i in range(3)]
    clusters=taste_profiles.fit(history,[a,b],now)
    assert len(clusters)==2
    morning=taste_profiles.select(clusters,{'hour':8,'room':'bedroom'},[],now)
    assert morning[0][1]['uri']=='classical'


def test_session_discovery_debt_and_surplus_affect_refill(tmp_path):
    model=library.LocalModel(tmp_path/'db')
    songs=[song('f'+str(i)) for i in range(20)]+[song('d'+str(i),trusted=False) for i in range(20)]
    context={'mood':music_mood.selection(0,1)}
    deficit=model.rank(songs,{**context,'discovery_balance':{'total':20,'discovery':0}},10)
    surplus=model.rank(songs,{**context,'discovery_balance':{'total':20,'discovery':20}},10)
    assert sum(i['_selection_category']=='discovery' for i in deficit)==10
    assert sum(i['_selection_category']=='discovery' for i in surplus)==7


def test_trace_is_persisted_and_bounded(tmp_path):
    model=library.LocalModel(tmp_path/'db')
    model.rank([song('seed')],{},1)
    for _ in range(110):recommendation_audit.store(model.db,model.last_trace)
    assert model.db.execute('SELECT count(*) FROM recommendation_runs').fetchone()[0]==100
    assert model.last_trace['selected'][0]['profile_confidence']==.9


def test_overplayed_reason_does_not_avoid_whole_sound_style():
    track=song('x')
    assert session_intent.bonus(track,[],[{'vote':-1,'reason':'overplayed','item':track}],None)==0
    assert session_intent.bonus(track,[],[{'vote':-1,'reason':'wrong_mood','item':track}],None)<0


def test_priority_orders_requested_seed_then_uncertain_records():
    records={'sure':{'metadata':{'audio_features':{'profile_confidence':.9}}},
             'unknown':{'metadata':{}}}
    entries=[{'uri':u,'aliases':[]} for u in ['sure','unknown','seed']]
    assert [e['uri'] for e in profile_priorities.ordered(entries,records,{'seed':3})]==['seed','unknown','sure']


def test_classifier_labels_schema_and_probabilities(tmp_path):
    np=pytest.importorskip('numpy')
    import json,sys
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'tools'))
    from music_classifiers import Heads
    path=tmp_path/'danceability-msd-musicnn-1.onnx';path.write_bytes(b'fixture')
    path.with_suffix('.json').write_text(json.dumps({'classes':['danceable','not_danceable'],
        'inference':{'embedding_model':{'model_name':'msd-musicnn-1'}}}))
    class Tensor:
        def __init__(self,name,shape):self.name,self.shape=name,shape
    class Session:
        def __init__(self,*a,**k):pass
        def get_inputs(self):return [Tensor('in',[None,200])]
        def get_outputs(self):return [Tensor('out',[None,2])]
        def run(self,*args):return [np.asarray([[.75,.25]],dtype=np.float32)]
    class Runtime:InferenceSession=Session
    heads=Heads(tmp_path,runtime=Runtime)
    assert heads.predict(np.zeros((1,200),dtype=np.float32))['danceability']==.75


def test_offline_feedback_split_does_not_train_on_future_or_autoplay():
    from lib.recommendation_evaluation import split_history, labelled_metrics
    before, after = split_history([{'origin':'manual','ts':1}, {'origin':'automatic','ts':1},
                                   {'origin':'manual','ts':2}],2)
    assert before==[{'origin':'manual','ts':1}]
    assert after==[{'origin':'manual','ts':2}]
    report=labelled_metrics([{'uri':'good'},{'uri':'unknown'},{'uri':'bad'}],
                            {'relevant':['good'],'irrelevant':['bad']})
    assert report['judged_precision']==.5 and report['judged_coverage']==pytest.approx(2/3)
    assert report['labelled_recall']==1
    with pytest.raises(ValueError):labelled_metrics([],{'relevant':['x'],'irrelevant':['x']})


@pytest.mark.asyncio
async def test_diagnostics_reads_persisted_traces_and_bounds_results(tmp_path):
    import json
    from types import SimpleNamespace
    model=library.LocalModel(tmp_path/'db')
    model.rank([song('one')],{'queue_id':'bedroom'},1)
    recommendation_audit.store(model.db,model.last_trace)
    service=object.__new__(library.LibraryService);service.model=model
    response=await service.handle_recommend_diagnostics(SimpleNamespace(query={'limit':'999','queue_id':'bedroom'}))
    assert json.loads(response.text)['runs'][0]['queue_id']=='bedroom'
    response=await service.handle_recommend_diagnostics(SimpleNamespace(query={'limit':'1','queue_id':'other'}))
    assert json.loads(response.text)['runs']==[]
    with pytest.raises(library.web.HTTPBadRequest):
        await service.handle_recommend_diagnostics(SimpleNamespace(query={'limit':'NaN'}))


def test_profile_upgrade_only_selects_missing_quality_without_bypassing_backoff():
    import sys
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'tools'))
    from profile_provider_library import pending_entries
    entries=[{'uri':u} for u in ['old','new','failed']]
    payload={'tracks':{'old':{'status':'sample_ready','metadata':{'audio_features':{'model':'m','energy':.5,'valence':.5}}},
        'new':{'status':'sample_ready','metadata':{'audio_features':{'profile_quality_version':1}}},
        'failed':{'status':'retry_pending','next_check':time.time()+3600}}}
    assert pending_entries(entries,payload,respect_retry_backoff=True)==[]
    assert [e['uri'] for e in pending_entries(entries,payload,upgrade_quality=True,respect_retry_backoff=True)]==['old']


def test_catalogue_cache_isolated_from_annotations_and_invalidates_metadata(mock_config,tmp_path,monkeypatch):
    import json
    mock_config({})
    monkeypatch.setattr(library,'DB_PATH',tmp_path/'db')
    path=tmp_path/'library.json'
    path.write_text(json.dumps([{'id':'songs','tracks':[{'uri':'a','name':'A','artist':'One'}]}]))
    monkeypatch.setattr(library,'CACHE_LIBRARY',path)
    service=library.LibraryService()
    first=service._load_library()
    first[0]['name']='Modified'
    first[0]['_selection_category']='discovery'
    again=service._load_library()
    assert again[0]['name']=='A' and '_selection_category' not in again[0]
    service.model.put_kv('track_metadata',{'a':{'genre':'ambient'}})
    assert service._load_library()[0]['genre']=='ambient'
    path.write_text(json.dumps([{'id':'songs','tracks':[{'uri':'b','name':'B','artist':'Two'}]}]))
    assert service._load_library()[0]['uri']=='b'
