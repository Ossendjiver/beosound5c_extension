import datetime as dt
import json
import library
from lib.prompt_playback import Guard

def iso(stamp):return dt.datetime.fromtimestamp(stamp,dt.timezone.utc).isoformat()

def test_playing_always_blocks_then_stop_releases():
    g=Guard();g.observe('bedroom','playing',10000);assert g.blocked(20000)
    g.observe('bedroom','idle',20000);assert not g.blocked(20000)

def test_pause_window_does_not_reset_on_poll():
    g=Guard();g.observe('bedroom','paused',10000,iso(9900))
    g.observe('bedroom','paused',11000,iso(9900))
    assert g.blocked(11699);assert not g.blocked(11700)

def test_old_pause_does_not_block_and_other_room_still_does():
    g=Guard();g.observe('bedroom','paused',10000,iso(8000));assert not g.blocked(10000)
    g.observe('kitchen','buffering',10000);assert g.blocked(10000)

def test_router_pause_without_timestamp_and_resume():
    g=Guard();g.observe('router','paused',10000);assert g.blocked(11799)
    assert not g.blocked(11800)
    g.observe('router','playing',12000);g.observe('router','paused',12100)
    assert g.blocked(13000)

def test_only_music_prompt_suppressed(tmp_path,monkeypatch):
    monkeypatch.setattr(library,'DB_PATH',tmp_path/'db')
    s=library.LibraryService();s.context['hour']=15
    s._prompt_playback.observe('kitchen','playing',10000)
    assert s.suggestion('bs5c')=={'clear':True,'kind':'music'}
    s.context['hour']=8;assert s.suggestion('bs5c')['kind']=='news'
    s.model.db.close()

def test_status_exposes_counts_and_provider_progress_without_secrets(tmp_path,monkeypatch):
    import asyncio
    monkeypatch.setattr(library,'DB_PATH',tmp_path/'db')
    monkeypatch.setenv('BS5C_PROVIDER_PROFILES_FILE',str(tmp_path/'provider_profiles.json'))
    (tmp_path/'provider_profiles.status.json').write_text(json.dumps({'running':True,'sweep':{'visited':25,'total':100}}))
    s=library.LibraryService()
    response=asyncio.run(s.handle_status(None));data=json.loads(response.text)
    assert data['library']['status']=='running'
    assert data['provider']['sweep']['visited']==25
    (tmp_path/'provider_profiles.status.json').unlink()
    assert json.loads(asyncio.run(s.handle_status(None)).text)['provider']=={}
    s.model.db.close()
