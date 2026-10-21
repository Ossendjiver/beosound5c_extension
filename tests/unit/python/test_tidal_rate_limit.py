import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch,Mock
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'services'))
from lib.tidal_rate_limit import RateLimit,Cooldown

def test_numeric_retry_survives_restart(tmp_path):
 p=tmp_path/'rate.json'
 with patch('lib.tidal_rate_limit.time.time',return_value=1000),patch('lib.tidal_rate_limit.random.uniform',return_value=0):
  with pytest.raises(Cooldown) as e:RateLimit(p).limited({'Retry-After':'120'})
  assert e.value.retry_at==1120
  with pytest.raises(Cooldown):RateLimit(p).ready()
  with patch('lib.tidal_rate_limit.time.time',return_value=1121):RateLimit(p).ready()

def test_date_header_honoured(tmp_path):
 with patch('lib.tidal_rate_limit.time.time',return_value=0),patch('lib.tidal_rate_limit.random.uniform',return_value=0):
  with pytest.raises(Cooldown) as e:RateLimit(tmp_path/'rate').limited({'Retry-After':'Thu, 01 Jan 1970 01:00:00 GMT'})
  assert e.value.retry_at==3600

@pytest.mark.parametrize('header',[None,'invalid','NaN','inf','-5'])
def test_missing_invalid_headers_back_off(tmp_path,header):
 r=RateLimit(tmp_path/'rate')
 with patch('lib.tidal_rate_limit.time.time',return_value=1000),patch('lib.tidal_rate_limit.random.uniform',return_value=0):
  for delay in [300,600,1200]:
   with pytest.raises(Cooldown) as e:r.limited({'Retry-After':header})
   assert e.value.retry_at==1000+delay
  r.completed();assert r.state()['attempts']==0

def test_request_pacing(tmp_path):
 r=RateLimit(tmp_path/'rate')
 with patch('lib.tidal_rate_limit.time.monotonic',side_effect=[100,100.5,102]),patch('lib.tidal_rate_limit.time.sleep') as sleep:
  r.before_request();r.before_request();sleep.assert_called_once_with(1.5)

def test_manual_button_respects_cooldown(tmp_path,monkeypatch):
 from lib import maintenance_scans as scans
 import asyncio,json
 from unittest.mock import AsyncMock
 (tmp_path/'rate_limit.json').write_text(json.dumps({'retry_at':9999999999}))
 monkeypatch.setattr(scans,'LOCKS',{'tidal-download':tmp_path/'worker.lock'})
 cmd=AsyncMock();monkeypatch.setattr(scans,'_command',cmd)
 assert asyncio.run(scans.start_scan('tidal-download'))['status']=='cooldown'
 cmd.assert_not_called()

def worker():
 p=Path(__file__).resolve().parents[3]/'services/tidal_download.py'
 spec=importlib.util.spec_from_file_location('tidal_worker_rate_test',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def test_resume_starts_with_missing_and_does_not_repeat_completed(tmp_path,monkeypatch):
 import json
 m=worker();cache=tmp_path/'cache';cache.mkdir();root=tmp_path/'music';root.mkdir();file=root/'old.flac';file.write_bytes(b'fLaCold')
 state={'version':1,'tracks':{'1':{'path':str(file),'size':7,'quality_policy':2,'downloaded_at':m.time.time()}}}
 (cache/'index.json').write_text(json.dumps(state))
 items=[{'name':str(n),'uri':f'tidal://track/{n}','provider_mappings':[{'provider_domain':'tidal','item_id':str(n),'available':True}]} for n in [1,2,3]]
 monkeypatch.setattr(m,'CACHE',cache);monkeypatch.setattr(m,'ROOT',root);monkeypatch.setattr(m,'CONFIG',tmp_path/'config');monkeypatch.setattr(m,'RATE',RateLimit(cache/'rate_limit.json',spacing=0))
 monkeypatch.setattr(m.subprocess,'check_output',lambda *a,**k:'/media/local\n');monkeypatch.setattr(m,'require_idle',lambda:None);monkeypatch.setattr(m,'publish',lambda *a,**k:None);monkeypatch.setattr(m,'all_tracks',lambda:items);monkeypatch.setattr(m,'select',lambda *a:(items,[]));monkeypatch.setattr(m,'auth',lambda:{'country':'AU','token':'secret'});monkeypatch.setattr(m.time,'sleep',lambda _:None)
 calls=[]
 def request(url,*a,**kw):
  if 'ranking' in url:return {}
  calls.append(url)
  if '/tracks/2?' in url:return {'explicit':False}
  raise Cooldown(9999999999)
 monkeypatch.setattr(m,'request',request)
 with pytest.raises(Cooldown):m.main()
 assert '/tracks/2?' in calls[0] and '/tracks/3?' in calls[1]
 assert json.loads((cache/'index.json').read_text())['pending_ids']==['3']
 calls.clear()
 with pytest.raises(Cooldown):m.main()
 assert len(calls)==1 and '/tracks/3?' in calls[0]
