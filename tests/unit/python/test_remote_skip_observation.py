import json
import time
from types import SimpleNamespace
import pytest
import library as lib


async def request(payload):
    return payload


@pytest.mark.asyncio
async def test_remote_skip_clears_outgoing_observers_and_receipt_is_persistent(tmp_path, monkeypatch, mock_config):
    monkeypatch.setattr(lib, 'DB_PATH', tmp_path/'library.sqlite3')
    mock_config({'device': 'Test'})
    service = lib.LibraryService()
    service._context_for_room = lambda room: {'room': room}
    item = dict(title='Outgoing', artist='Performer', uri='tidal://one', duration=240, media_type='track')
    service._current_media = dict(item)
    service._current_started = time.monotonic()-40
    service._external_media = dict(item)
    service._external_started = time.monotonic()-40
    payload = dict(item, type='listen_feedback', reason='skip', seconds=40,
                   event_id='ha_remote_20261030_123456', timestamp_ms=int(time.time()*1000), room='link')
    req = SimpleNamespace(json=lambda: request(payload))
    assert not json.loads((await service.handle_event(req)).text)['duplicate']
    assert service._current_media is None and service._external_media is None
    assert service._skip_already_reported(dict(item, uri='other://same'))
    assert not service._skip_already_reported(dict(item, title='Next song', uri='tidal://two'))
    service._finish_current()
    assert json.loads((await service.handle_event(req)).text)['duplicate']
    rows=service.model.db.execute('SELECT * FROM listens').fetchall()
    assert len(rows)==1 and rows[0]['end_reason']=='skip' and rows[0]['reward']<0
    assert lib.skip_policy.recent(item, rows, time.time())[0]
    service.model.db.close()
    service=lib.LibraryService()
    service._context_for_room=lambda room:{'room':room}
    assert json.loads((await service.handle_event(req)).text)['duplicate']
    assert service.model.db.execute('SELECT count(*) FROM listens').fetchone()[0]==1
    service.model.db.close()


def test_observer_suppression_expires(tmp_path, monkeypatch, mock_config):
    monkeypatch.setattr(lib, 'DB_PATH', tmp_path/'library.sqlite3')
    mock_config({'device':'Test'})
    service=lib.LibraryService()
    item={'title':'Outgoing','artist':'Performer','uri':'tidal://one'}
    service._reported_skip_observers=[(time.monotonic()-1,item)]
    assert not service._skip_already_reported(item)
    assert service._reported_skip_observers==[]
    service.model.db.close()
