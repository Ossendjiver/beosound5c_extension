import sqlite3
import pytest
from lib.reference_plays import weights,prepare,apply


def database():
    db=sqlite3.connect(':memory:')
    db.executescript("CREATE TABLE tracks(item_id INTEGER PRIMARY KEY,play_count INTEGER,last_played INTEGER);INSERT INTO tracks VALUES(1,5,99),(2,0,0),(3,NULL,30);CREATE TABLE provider_mappings(item_id INTEGER,media_type TEXT,provider_instance TEXT,provider_domain TEXT,provider_item_id TEXT);INSERT INTO provider_mappings VALUES(1,'track','tidal--x','tidal','one'),(2,'track','tidal--x','tidal','two'),(3,'track','tidal--x','tidal','three');")
    return db


def item(i):return {'media_type':'track','provider':'library','item_id':str(i)}

def test_weights_match_confirmed_endpoints_and_rounding():
    assert weights(5)==[6,5,4,2,1]
    assert weights(25)[0]==26 and weights(25)[-1]==1
    for n in range(2,101):
        w=weights(n);assert len(w)==n and w[0]==n+1 and w[-1]==1
        assert all(a>=b for a,b in zip(w,w[1:]))
    assert weights(0)==[] and weights(1)==[1]


def test_overlapping_playlists_add_counts_once_without_changing_recency():
    db=database()
    plan=prepare(db,[({'uri':'a'},[item(1),item(2),item(3)]),({'uri':'b'},[item(1),item(3)])])
    out=apply(db,plan)
    assert out=={'applied_playlists':2,'skipped_playlists':0,'updated_tracks':3,'added_plays':12}
    assert db.execute('SELECT * FROM tracks ORDER BY item_id').fetchall()==[(1,12,99),(2,3,0),(3,2,30)]
    assert apply(db,plan)=={'applied_playlists':0,'skipped_playlists':2,'updated_tracks':0,'added_plays':0}


def test_changed_playlist_is_still_once_only_and_new_playlist_can_apply():
    db=database();apply(db,prepare(db,[({'uri':'a'},[item(1),item(2)])]))
    out=apply(db,prepare(db,[({'uri':'a'},[item(3),item(2)]),({'uri':'new'},[item(2)])]))
    assert out['applied_playlists']==1 and out['skipped_playlists']==1
    assert db.execute('SELECT play_count FROM tracks WHERE item_id=2').fetchone()[0]==2


def test_provider_identity_resolves_library_track_and_missing_items_abort():
    db=database()
    plan=prepare(db,[({'uri':'a'},[{'provider':'tidal','item_id':'one','media_type':'track'}])])
    assert plan[0]['entries']==[(1,1)]
    with pytest.raises(ValueError):prepare(db,[({'uri':'b'},[item(999)])])
    assert db.execute('SELECT play_count FROM tracks WHERE item_id=1').fetchone()[0]==5


def test_failed_import_rolls_back_counts_and_ledger():
    db=database();plan=prepare(db,[({'uri':'a'},[item(1),item(2)])])
    plan[0]['entries'].append((999,4))
    with pytest.raises(ValueError):apply(db,plan)
    assert db.execute('SELECT play_count FROM tracks WHERE item_id=1').fetchone()[0]==5
    assert db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='homemedia_reference_play_imports'").fetchone()[0]==0
