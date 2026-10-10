import json
import library as lib
from lib import track_enrichment as e, music_features as f, radio_genres as g, provider_profiles as p


def test_performers_not_composers():
    assert e.performer_metadata({'artists': [], 'metadata': {'performers': ['Beaux Arts Trio'], 'composer': 'Beethoven'}})['artist']=='Beaux Arts Trio'
    assert not e.performer_metadata({'composer':'Beethoven','album_artist':'Beethoven'})
    assert e.performer_metadata({'artists':[{'name':'Performer'}],'metadata':{'performers':['Other']}})['artist']=='Performer'


def test_exact_uri_recovery_and_ambiguous_credits(mock_config,tmp_path,monkeypatch):
    cache=tmp_path/'library.json'
    cache.write_text(json.dumps([{'id':'artists','tracks':[{'name':'Composer','tracks':[
        {'url':'library://track/1','name':'Sonata','artist':'Ensemble'},
        {'url':'library://track/2','name':'Sonata','artist':'Different'}]}]},
        {'id':'songs','tracks':[{'url':'library://track/1','name':'Sonata','artist':' '},
                               {'url':'library://track/2','name':'Sonata','artist':'Existing'}]}]))
    mock_config({});monkeypatch.setattr(lib,'DB_PATH',tmp_path/'db');monkeypatch.setattr(lib,'CACHE_LIBRARY',cache)
    service=lib.LibraryService();items={i['uri']:i for i in service._load_library()}
    assert items['library://track/1']['artist']=='Ensemble'
    assert items['library://track/1']['artist_source']=='cached-exact-uri'
    assert items['library://track/2']['artist']=='Existing'
    assert e.cached_performers([{'uri':'library://track/1','artist':'A'}, {'uri':'library://track/1','artist':'B'}])=={}
    service.model.db.close()


def mix(style,genres=None):
    return {'uri':'soundcloud://track/1','artist':'Finnebassen','duration':7220,
            'metadata':{'genres':genres or ['Finnebassen'],'style':style}}


def test_soundcloud_tags_are_not_artist_genres():
    result=f.metadata(mix('"Andre Bratten" Disclosure "Mind Against"'))
    assert result['genres']==[]
    assert not g.families(g.labels(result))
    assert f.metadata(mix('"Deep House" "Steve Bug"'))['genres']==['deep house']


def test_long_mix_consensus_is_separate_and_requires_three_artists():
    index=e.artist_genres([{'artist':name,'genres':['deep house']} for name in ['Andre Bratten','Disclosure','Mind Against']])
    item=mix('"Andre Bratten" Disclosure "Mind Against"')
    result=e.mix_metadata(item,index)
    assert result['genres']==[] and result['inferred_genres']==['deep house']
    assert result['genre_evidence']['matched_artists']==3
    assert 'deep house' in g.labels(result)
    assert not p.useful(result)  # Genre consensus is not an audio/mood profile.
    assert 'inferred_genres' not in e.mix_metadata(item,dict(list(index.items())[:2]))
    assert 'inferred_genres' not in e.mix_metadata(dict(item,duration=200),index)
    assert 'inferred_genres' not in e.mix_metadata(mix('"Deep House"'),index)


def test_conflicting_artist_evidence_and_substrings_rejected():
    index={e.normal('A'): {'house'},e.normal('B'):{'house'},e.normal('C'):{'rock'}}
    assert 'inferred_genres' not in e.mix_metadata(mix('"A" "B" "C"'),index)
    assert 'inferred_genres' not in e.mix_metadata(mix('"AA" "BB" "CC"'),index)


def test_inference_survives_sidecar_without_becoming_supplied_genre():
    result=e.mix_metadata(mix('A B C'),{'a':{'house'},'b':{'house'},'c':{'house'}})
    loaded=p.load({'version':1,'tracks':{'soundcloud://track/1':{'metadata':result}}})['soundcloud://track/1'][0]
    assert loaded['genres']==[] and loaded['inferred_genres']==['house']
    assert g.labels(loaded)=={'house'}


def test_file_recovery_rejects_traversal_and_uses_performer(tmp_path):
    import sys
    from pathlib import Path
    from types import SimpleNamespace
    sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'tools'))
    import enrich_library_metadata as worker
    root=tmp_path/'music';root.mkdir();(root/'song.flac').write_bytes(b'')
    calls=[]
    def probe(args,**kwargs):
        calls.append(args)
        return SimpleNamespace(stdout=json.dumps({'format':{'tags':{'ARTIST':'Composer','PERFORMER':'Ensemble'}}}))
    item={'provider_mappings':[{'provider_domain':'filesystem_local','item_id':'song.flac'}]}
    assert worker.file_credits(item,root,probe)['artist']=='Ensemble'
    item['provider_mappings'][0]['item_id']='../outside.flac';(tmp_path/'outside.flac').write_bytes(b'')
    assert worker.file_credits(item,root,probe)=={} and len(calls)==1


def test_sidecar_missing_performer_recovery(mock_config,tmp_path,monkeypatch):
    cache=tmp_path/'library.json';cache.write_text(json.dumps([{'id':'songs','tracks':[{'url':'library://track/1','name':'Work'}]}]))
    (tmp_path/'track_enrichment.json').write_text(json.dumps({'version':1,'tracks':{'library://track/1':{'metadata':{'artist':'Quartet','artist_source':'embedded-performer'}}}}))
    mock_config({});monkeypatch.setattr(lib,'DB_PATH',tmp_path/'db');monkeypatch.setattr(lib,'CACHE_LIBRARY',cache)
    service=lib.LibraryService();assert service._load_library()[0]['artist']=='Quartet';service.model.db.close()


def test_classical_composer_artist_is_not_a_recovered_performer(tmp_path):
    import enrich_library_metadata as worker
    from types import SimpleNamespace
    (tmp_path/'work.flac').write_bytes(b'')
    item={'name':'Sonata No. 2 in G minor','provider_mappings':[{'provider_domain':'filesystem_local','item_id':'work.flac'}]}
    def probe(*args,**kwargs):return SimpleNamespace(stdout=json.dumps({'format':{'tags':{'artist':'Beethoven'}}}))
    assert worker.file_credits(item,tmp_path,probe)=={}
    def composer(*args,**kwargs):return SimpleNamespace(stdout=json.dumps({'format':{'tags':{'artist':'Beethoven','composer':'Beethoven'}}}))
    assert worker.file_credits(dict(item,name='Other'),tmp_path,composer)=={}


def test_uploader_genres_need_three_distinct_recordings_and_remain_inferred():
    reference=[{'artist':'Finnebassen','name':n,'genres':['deep house','electronic']} for n in ('First','Second','Third')]
    index=e.artist_genres(reference+[dict(reference[0],uri='other-provider')])
    result=e.mix_metadata(mix('Unrecognised tags'),index)
    assert result['inferred_genres']==['deep house'] and result['genres']==[]
    assert result['genre_evidence']['matched_recordings']==3
    assert g.labels(result)=={'deep house'} and not p.useful(result)
    assert 'inferred_genres' not in e.mix_metadata(mix('Unknown'),e.artist_genres(reference[:2]))
    contradictory=e.artist_genres(reference+[{'artist':'Finnebassen','name':'Rock track','genres':['rock']}, {'artist':'Finnebassen','name':'Jazz track','genres':['jazz']}])
    assert 'inferred_genres' not in e.mix_metadata(mix('Unknown'),contradictory)
