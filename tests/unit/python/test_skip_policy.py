import time
import library as lib
from lib import skip_policy

def track(uri='tidal://song',title='Borderline',artist='Sufjan Stevens'):
    return dict(uri=uri,title=title,artist=artist,duration=240,end_reason='skip',selection_origin='reported')

def history(count,age):
    now=time.time()
    return now,[dict(track(),ts=now-age-i,reward=-1,origin='reported') for i in range(count)]

def test_cooldown_escalates_and_expires():
    for count,limit in [(1,6*3600),(2,2*86400),(3,7*86400)]:
        now,rows=history(count,limit-10)
        assert skip_policy.recent(track(),rows,now)[0]
        now,rows=history(count,limit+10)
        assert not skip_policy.recent(track(),rows,now)[0]

def test_provider_upload_and_versions_match_without_blocking_other_performers():
    now,rows=history(1,60)
    assert skip_policy.recent(track('soundcloud://upload','Sufjan Stevens - Borderline (Remastered)','Culturcide'),rows,now)[0]
    assert not skip_policy.recent(track('other://song',artist='Madonna'),rows,now)[0]

def test_report_receipt_survives_restart_and_late_skip_is_negative(tmp_path):
    path=tmp_path/'library.sqlite3'
    model=lib.LocalModel(path);stamp=time.time()-7200
    assert model.record_listen(track(),160,{},event_id='same-event-0123456789',event_ts=stamp)
    model.db.close();model=lib.LocalModel(path)
    assert model.record_listen(track(),160,{},event_id='same-event-0123456789',event_ts=stamp) is False
    rows=model.db.execute('SELECT * FROM listens').fetchall()
    assert len(rows)==1 and rows[0]['reward']<0 and rows[0]['ts']==stamp
    assert model.rank([dict(track(),name='Borderline',trusted=True,favorite=True,play_count=10000)],{},10)==[]
    model.db.close()

def test_pause_and_stop_never_trigger_cooldown(tmp_path):
    model=lib.LocalModel(tmp_path/'library.sqlite3')
    for reason in ['pause','stop','transfer','completed']:
        model.record_listen(dict(track(),end_reason=reason),160,{})
    rows=model.db.execute('SELECT * FROM listens').fetchall()
    assert not skip_policy.recent(track(),rows,time.time())[0]
    model.db.close()

async def _request(payload):
    return payload

import pytest
import json
from types import SimpleNamespace

@pytest.mark.asyncio
async def test_feedback_endpoint_accepts_self_contained_remote_report_once(tmp_path,monkeypatch,mock_config):
    monkeypatch.setattr(lib,'DB_PATH',tmp_path/'library.sqlite3');mock_config({'device':'Test'})
    service=lib.LibraryService();service._context_for_room=lambda room:{}
    payload=dict(track(),media_type='track',type='listen_feedback',reason='skip',seconds=160,
                 event_id='remote-event-0123456789',timestamp_ms=int(time.time()*1000))
    request=SimpleNamespace(json=lambda:_request(payload))
    assert json.loads((await service.handle_event(request)).text)['duplicate'] is False
    assert json.loads((await service.handle_event(request)).text)['duplicate'] is True
    assert service.model.db.execute('SELECT count(*) FROM listens').fetchone()[0]==1
    service.model.db.close()
