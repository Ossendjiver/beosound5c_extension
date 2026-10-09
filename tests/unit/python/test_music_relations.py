import json
import time
import pytest
import library as lib
from lib import music_relations as r, music_mood

def song(name,artist='Artist',**kw):return dict(name=name,title=name,artist=artist,uri='track://'+name,duration=200,media_type='track',**kw)
A,B,C=song('A'),song('B'),song('C')

def playlist(identifier,tracks):return {'id':identifier,'media_type':'playlist','tracks':tracks}

def cache(*lists):return [{'id':'playlists','tracks':list(lists)}]

def row(item,ts,seconds=200,origin='manual',reward=1,room='lounge',reason='unknown',identifier=1):
    return {**item,'id':identifier,'ts':ts,'seconds':seconds,'origin':origin,'reward':reward,'room':room,'end_reason':reason}

def test_small_playlist_overlap_stronger_than_large_all_favourites():
    large=r.Playlists(cache(playlist('playlist:big',[A,B]+[song(str(i)) for i in range(500)])))
    small=r.Playlists(cache(playlist('playlist:small',[A,B])))
    assert large.overlap(A,B)<.003 and small.overlap(A,B)==1
    assert small.overlap(A,C)==0 and small.overlap(A,A)==0

def test_playlist_duplicates_and_repeat_entries_do_not_inflate_overlap():
    p=playlist('playlist:one',[A,A,B])
    index=r.Playlists(cache(p,p))
    assert len(index.sizes)==1 and index.overlap(A,B)==1

def test_playlists_are_not_inferred_from_artist_album_folders():
    index=r.Playlists([{'id':'songs','tracks':[{'id':'album','tracks':[A,B]}]}])
    assert not index.sizes

def test_recording_identity_shared_across_providers_preserves_versions():
    assert r.key(A)==r.key({**A,'uri':'other://1','artist':'ARTIST'})
    assert r.key(A)!=r.key({**A,'version':'Live'})

@pytest.mark.parametrize('origin',['automatic','unknown','legacy'])
def test_automatic_unknown_legacy_destination_cannot_create_positive_edge(origin):
    assert r.evidence(row(A,1000),row(B,1200,origin=origin,reward=.05)) is None

def test_manual_accepted_destination_can_follow_accepted_automatic_source():
    assert r.evidence(row(A,1000,origin='automatic',reward=.05),row(B,1200))[2:]==(1,1)

@pytest.mark.parametrize('change',[{'room':'bedroom'},{'reward':-2},{'ts':100}])
def test_wrong_room_skipped_source_and_out_of_order_records_do_not_learn(change):
    assert r.evidence({**row(A,1000),**change},row(B,1200)) is None

def test_long_breaks_and_replays_do_not_create_pair():
    assert r.evidence(row(A,1000),row(B,3000)) is None
    assert r.evidence(row(A,1000),row(A,1200)) is None

def test_explicit_skip_learns_directional_penalty_but_pause_does_not():
    assert r.evidence(row(A,1000),row(B,1010,seconds=10,origin='reported',reward=-2,reason='skip'))[2:]==(-1,1)
    assert r.evidence(row(A,1000),row(B,1010,seconds=10,origin='unknown',reward=0,reason='pause')) is None

def test_repeated_pair_skips_escalate_and_do_not_penalize_different_predecessor():
    now=time.time();p=r.Playlists()
    def evidence(count):return [{'source_key':r.key(A),'target_key':r.key(B),'ts':now,'sign':-1,'weight':1,'room':'lounge'}]*count
    single=r.Prior(p,evidence(1),'lounge',now);repeated=r.Prior(p,evidence(4),'lounge',now)
    assert repeated.boost(A,B)<single.boost(A,B)<0
    assert repeated.boost(C,B)==0
    assert repeated.boost(B,A)==0
    assert repeated.components(A,B)[2]<=8

def test_directional_acceptance_is_smoothed_and_decays():
    now=time.time();e=[{'source_key':r.key(A),'target_key':r.key(B),'ts':now,'sign':1,'weight':1,'room':'lounge'}]
    prior=r.Prior(r.Playlists(),e,'lounge',now)
    assert 0<prior.boost(A,B)<.4 and prior.boost(B,A)==0
    assert r.Prior(r.Playlists(),e,'lounge',now+90*86400).boost(A,B)<prior.boost(A,B)

def test_duplicate_observer_finishes_record_one_pair_and_persist(tmp_path):
    m=lib.LocalModel(tmp_path/'db');now=time.time()
    m.record_listen({**A,'selection_origin':'manual'},200,{'room':'lounge'},event_ts=now-400)
    m.record_listen({**B,'selection_origin':'manual'},200,{'room':'lounge'},event_ts=now-200)
    m.record_listen({**B,'selection_origin':'manual'},200,{'room':'lounge'},event_ts=now-199)
    assert m.db.execute('SELECT COUNT(*) FROM track_relations').fetchone()[0]==1
    m.db.close();m=lib.LocalModel(tmp_path/'db')
    assert m.db.execute('SELECT COUNT(*) FROM track_relations').fetchone()[0]==1
    m.db.close()

def test_backfill_excludes_auto_sequences_and_is_idempotent(tmp_path):
    m=lib.LocalModel(tmp_path/'db');now=time.time()
    for i,(item,origin) in enumerate([(A,'manual'),(B,'automatic'),(C,'manual')]):
        m.record_listen({**item,'selection_origin':origin},200,{'room':'lounge'},event_ts=now-600+i*200)
    m.db.execute('DELETE FROM track_relations')
    r.backfill(m.db);r.backfill(m.db)
    rows=list(m.db.execute('SELECT * FROM track_relations'))
    assert len(rows)==1 and rows[0]['source_key']==r.key(B) and rows[0]['target_key']==r.key(C)
    m.db.close()

def test_overlap_and_relations_do_not_override_wrong_mood_or_recent_skips(tmp_path):
    m=lib.LocalModel(tmp_path/'db');m.playlist_relations=r.Playlists(cache(playlist('playlist:p',[A,B])))
    items=[dict(B,favorite=True,genres=['dance']),dict(C,favorite=True,genres=['ambient'])]
    assert [s['uri'] for s in m.rank(items,{'seed_features':A,'mood':music_mood.selection(270,0)},5)]==[C['uri']]
    m.record_listen({**C,'selection_origin':'reported','end_reason':'skip'},10,{'room':'lounge'})
    assert not m.rank(items,{'seed_features':A,'mood':music_mood.selection(270,0)},5)
    m.db.close()

@pytest.mark.asyncio
async def test_queued_intent_persists_consumes_once_and_respects_room(mock_config,tmp_path,monkeypatch):
    mock_config({});monkeypatch.setattr(lib,'DB_PATH',tmp_path/'db');s=lib.LibraryService()
    class Request:
        async def json(self):return {**B,'type':'selection','action':'queue_item','room':'lounge'}
    await s.handle_event(Request());s.model.db.close();s=lib.LibraryService()
    assert s._start_learning_item(dict(B,room='bedroom'))['selection_origin']=='unknown'
    assert s._start_learning_item(dict(B,room='lounge'))['selection_origin']=='manual'
    assert s._start_learning_item(dict(B,room='lounge'))['selection_origin']=='unknown'
    s.model.db.close()

def test_playlist_cache_rebuilds_for_updates(mock_config,tmp_path,monkeypatch):
    path=tmp_path/'library.json';path.write_text(json.dumps(cache(playlist('playlist:1',[A,B]))))
    mock_config({});monkeypatch.setattr(lib,'DB_PATH',tmp_path/'db');monkeypatch.setattr(lib,'CACHE_LIBRARY',path)
    s=lib.LibraryService();s._load_library();assert s.model.playlist_relations.overlap(A,B)==1
    path.write_text(json.dumps(cache(playlist('playlist:1',[A,C]))));s._load_library()
    assert s.model.playlist_relations.overlap(A,B)==0 and s.model.playlist_relations.overlap(A,C)==1
    s.model.db.close()


def test_batch_ranking_recomputes_pair_for_each_immediate_predecessor(tmp_path):
    m=lib.LocalModel(tmp_path/'db');now=time.time();source=0
    for a,b,sign,count in [(A,B,-1,5),(A,C,1,20),(C,B,1,20)]:
        for _ in range(count):
            source+=1
            m.db.execute('INSERT INTO track_relations VALUES(?,?,?,?,?,?,?,?)',(source,source+100,r.key(a),r.key(b),'lounge',now,sign,1))
    m.db.commit()
    D=song('D')
    picks=m.rank([dict(i,favorite=True,genres=['ambient']) for i in [B,C,D]],{'room':'lounge','relation_seed':A},3)
    assert [i['name'] for i in picks]==['C','B','D']
    m.db.close()
