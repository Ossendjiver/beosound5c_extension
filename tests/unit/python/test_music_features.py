import json
import pytest
import library as lib
from lib import music_features as features, music_mood as mood


def test_metadata_merge_preserves_genres_duration_and_favorites():
    merged = features.merge({'genres':['ambient'], 'duration':200, 'favorite':True,
                             'audio_features':{'bpm':80}},
                            {'genres':[], 'duration':0, 'favorite':False,
                             'audio_features':{'bpm':float('nan')}})
    assert merged['genres'] == ['ambient']
    assert merged['duration'] == 200 and merged['favorite']
    assert merged['audio_features']['bpm'] == 80


def test_tempo_does_not_claim_valence_and_invalid_model_values_are_rejected():
    assert mood.profile({'audio_features':{'bpm':120, 'energy':.8, 'valence':.8}}, {}) is None
    assert mood.profile({'audio_features':{'model':'m', 'energy':float('nan'), 'valence':.8}}, {}) is None
    assert features.clean({'embedding':[float('inf')]*8}) == {}
    assert features.metadata({'metadata':{'genres':['ambient'],'bpm':90}})['audio_features']['bpm'] == 90


def test_acoustic_similarity_only_compares_same_embedding_model():
    root = {'audio_features':{'bpm':80,'embedding':[1]*8,'embedding_model':'m'}}
    item = {'audio_features':{'bpm':160,'embedding':[1]*8,'embedding_model':'m'}}
    assert features.similarity(item, root) == pytest.approx(1)
    item['audio_features']['embedding_model']='other'
    assert features.similarity(item, root) == pytest.approx(.4)


def test_high_history_cannot_admit_wrong_or_unknown_mood(tmp_path):
    model = lib.LocalModel(tmp_path/'db')
    for _ in range(40):
        model.record_listen({'name':'Popular','artist':'Known','uri':'bad','duration':200,
                             'selection_origin':'manual'}, 190, {})
    candidates = [dict(uri='bad',name='Popular',artist='Known',favorite=True,genre='dance'),
                  dict(uri='unknown',name='Unknown',artist='Known',favorite=True),
                  dict(uri='right',name='Right',artist='Known',trusted=True,genre='ambient')]
    assert [i['uri'] for i in model.rank(candidates, {'mood':mood.selection(270,0)},20)] == ['right']
    model.db.close()


def test_mood_distance_precedes_familiarity_within_eligible_pool(tmp_path):
    model = lib.LocalModel(tmp_path/'db')
    songs = [dict(uri='fav',name='Familiar',artist='Known',favorite=True,
                  mood_profile={'energy':.25,'valence':.5}),
             dict(uri='fit',name='Fit',artist='Known',trusted=True,
                  audio_features={'model':'emomusic','energy':0,'valence':.5})]
    assert model.rank(songs, {'mood':mood.selection(270,0)},20)[0]['uri'] == 'fit'
    model.db.close()


def test_audio_sidecar_enriches_cached_library_without_database_mutation(mock_config,tmp_path,monkeypatch):
    cache=tmp_path/'library.json'
    cache.write_text(json.dumps([{'id':'songs','tracks':[{'url':'song','name':'Song','artist':'A','genres':['ambient']}]}]))
    (tmp_path/'audio_features.json').write_text(json.dumps({'version':1,'tracks':{
        'song':{'audio_features':{'model':'m','energy':.1,'valence':.4,'bpm':80}}}}))
    mock_config({}); monkeypatch.setattr(lib,'DB_PATH',tmp_path/'db');monkeypatch.setattr(lib,'CACHE_LIBRARY',cache)
    service=lib.LibraryService()
    service._seed_metadata={'song':{'genres':[], 'favorite':True}}
    song=service._load_library()[0]
    assert song['genres']==['ambient'] and song['audio_features']['bpm']==80
    assert mood.profile(song,{})['source']=='audio_model'
    service.model.db.close()


@pytest.mark.asyncio
async def test_metadata_enrichment_is_bounded_and_preserves_cache(mock_config,tmp_path,monkeypatch):
    mock_config({'library':{'metadata_enrichment_batch':1}})
    monkeypatch.setattr(lib,'DB_PATH',tmp_path/'db')
    service=lib.LibraryService();calls=[]
    service._load_library=lambda:[{'uri':'song','favorite':True},{'uri':'other'}]
    service.model.put_kv('track_metadata',{'song':{'genres':['ambient']}})
    class Response:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        def raise_for_status(self):pass
        async def json(self):return {'uri':'song','metadata':{'genres':[], 'bpm':80}}
    class Session:
        def post(self,url,**kwargs):calls.append(kwargs);return Response()
    service.session=Session()
    await service._refresh_track_metadata()
    assert len(calls)==1
    assert calls[0]['json']['args']=={'uri':'song','allow_update_metadata':False}
    cached=service.model.get_kv('track_metadata',{})['song']
    assert cached['genres']==['ambient'] and cached['audio_features']['bpm']==80
    service.model.db.close()
