import time
import hashlib,json,tempfile,threading,unittest,urllib.request,urllib.error
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'services'))
import ipod_phone as phone

class PhoneBridgeTests(unittest.TestCase):
 def test_lan_scope(self):
  for address in ['127.0.0.1','192.168.4.12','10.1.2.3','172.16.0.1']:self.assertTrue(phone.lan(address))
  for address in ['8.8.8.8','169.254.1.1','192.0.2.1','100.64.0.1','bad']:self.assertFalse(phone.lan(address))
 def test_sources_cannot_escape_music_root(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d)/'music';root.mkdir();outside=Path(d)/'private.flac';outside.write_bytes(b'secret')
   item={'provider_mappings':[{'provider_domain':'filesystem_local','item_id':'../private.flac'}]}
   self.assertIsNone(phone.file_source(item,[root],{}))
   with self.assertRaises(ValueError):phone.checked({'source':str(outside),'roots':[root],'size':6,'mtime':outside.stat().st_mtime_ns})
 def test_source_change_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   source=Path(d)/'song.flac';source.write_bytes(b'flac');st=source.stat()
   record=dict(source=str(source),roots=[Path(d)],size=st.st_size,mtime=st.st_mtime_ns)
   self.assertEqual(source,phone.checked(record));source.write_bytes(b'changed')
   with self.assertRaises(ValueError):phone.checked(record)
 def test_authenticated_file_api_and_local_pairing(self):
  with tempfile.TemporaryDirectory() as d:
   config=Path(d)/'config.json';token='scoped-ipod-token-1234';config.write_text(json.dumps({'token':token}))
   source=Path(d)/'audio.flac';source.write_bytes(b'FLAC fixture');st=source.stat();ident='a'*64
   files={ident:dict(source=str(source),roots=[Path(d)],size=st.st_size,mtime=st.st_mtime_ns,sha256=None)}
   with patch.object(phone,'CONFIG',config),patch.object(phone,'FILES',files):
    server=phone.ThreadingHTTPServer(('127.0.0.1',0),phone.Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base='http://127.0.0.1:'+str(server.server_port)
    def request(path,tokenvalue=token,origin=None):
     headers={'Authorization':'Bearer '+tokenvalue}
     if origin:headers['Origin']=origin
     return urllib.request.urlopen(urllib.request.Request(base+path,headers=headers),timeout=2)
    try:
     with self.assertRaises(urllib.error.HTTPError) as e:request('/metadata/'+ident,'wrong')
     self.assertEqual(401,e.exception.code)
     with self.assertRaises(urllib.error.HTTPError) as e:request('/pairing',origin='http://evil.invalid')
     self.assertEqual(403,e.exception.code)
     with request('/metadata/'+ident) as response:metadata=json.load(response)
     self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),metadata['sha256'])
     with request('/file/'+ident) as response:self.assertEqual(source.read_bytes(),response.read())
     with self.assertRaises(urllib.error.HTTPError) as e:request('/file/../../private')
     self.assertEqual(404,e.exception.code)
    finally:server.shutdown();server.server_close();thread.join()
 def test_first_phone_pairing_is_durable_and_cannot_change_device(self):
  with tempfile.TemporaryDirectory() as d:
   auth=Path(d)/'auth.json';auth.write_text('{"token":"scoped-ipod-token-1234"}')
   config=Path(d)/'sync.json';config.write_text('{"enabled":true}')
   with patch.object(phone,'CONFIG',auth),patch.object(phone,'SYNC_CONFIG',config):
    server=phone.ThreadingHTTPServer(('127.0.0.1',0),phone.Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def pair(ident):
     req=urllib.request.Request('http://127.0.0.1:'+str(server.server_port)+'/pair',data=json.dumps({'device_id':ident}).encode(),headers={'Authorization':'Bearer scoped-ipod-token-1234'})
     return urllib.request.urlopen(req)
    try:
     with pair('uuid:ABCD-1234') as response:self.assertEqual('uuid:ABCD-1234',json.load(response)['device_id'])
     self.assertEqual('uuid:ABCD-1234',json.loads(config.read_text())['phone_device_id'])
     self.assertTrue(json.loads(config.read_text())['enabled'])
     with self.assertRaises(urllib.error.HTTPError) as e:pair('uuid:DEAD-5678')
     self.assertEqual(409,e.exception.code)
    finally:server.shutdown();server.server_close();thread.join()
 def test_history_receipts_invalidate_cached_plan(self):
  with tempfile.TemporaryDirectory() as d:
   config=Path(d)/'config.json';config.write_text('{"token":"scoped-ipod-token-1234"}')
   with patch.object(phone,'CONFIG',config),patch.object(phone,'PLAN',{'state':'ready'}),patch.object(phone,'local_call',return_value={'ok':True,'accepted':1}) as history:
    server=phone.ThreadingHTTPServer(('127.0.0.1',0),phone.Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
     request=urllib.request.Request('http://127.0.0.1:'+str(server.server_port)+'/history',data=b'{"events":[]}',headers={'Authorization':'Bearer scoped-ipod-token-1234'})
     with urllib.request.urlopen(request) as response:self.assertTrue(json.load(response)['ok'])
     history.assert_called_once_with('/library/ipod/history',{'events':[]});self.assertEqual('idle',phone.PLAN['state'])
    finally:server.shutdown();server.server_close();thread.join()

 def test_plan_skips_mislabeled_podcast_and_broken_track_but_keeps_local_music(self):
  with tempfile.TemporaryDirectory() as d:
   source=Path(d)/'song.flac';source.write_bytes(b'fixture')
   items=[{'uri':'podcastfeed--x://podcast_episode/bad','media_type':'track','title':'Episode'},
          {'uri':'library://track/bad','media_type':'track','title':'Unavailable'},
          {'uri':'library://track/good','media_type':'track','title':'Music','artist':'Artist'}]
   def ma(command,args):
    if command=='config/providers':return []
    self.assertEqual(args['uri'],'library://track/bad')
    raise urllib.error.HTTPError('http://ma/api',500,'Unavailable recording',{},None)
   def file_source(item,roots,index):return source if item['uri']=='library://track/good' else None
   with patch.object(phone,'MUSIC',Path(d)),patch.object(phone,'read',return_value={}),patch.object(phone,'local_call',return_value={'items':items,'groups':{}}),patch.object(phone,'ma',side_effect=ma),patch.object(phone,'file_source',side_effect=file_source),patch.object(phone,'PLAN',{'state':'selecting'}),patch.object(phone,'FILES',{}):
    phone.build_plan('scoped-test-token')
    self.assertEqual('ready',phone.PLAN['state'])
    self.assertEqual(2,phone.PLAN['skipped'])
    self.assertEqual(['library://track/good'],[x['item']['uri'] for x in phone.PLAN['items']])

 def test_plan_does_not_hide_authentication_failure(self):
  def ma(command,args):raise urllib.error.HTTPError('http://ma/api',401,'Unauthorized',{},None)
  with patch.object(phone,'read',return_value={}),patch.object(phone,'local_call',return_value={'items':[],'groups':{}}),patch.object(phone,'ma',side_effect=ma),patch.object(phone,'PLAN',{'state':'selecting'}):
   phone.build_plan('scoped-test-token')
   self.assertEqual('error',phone.PLAN['state'])

def test_remote_transfer_status_validation_and_stale_state(tmp_path,monkeypatch):
 from lib import ipod_transfer_status as status
 import pytest
 monkeypatch.setattr(status,'STATUS',tmp_path/'status.json')
 data={'client':'ubuntu','device_id':'uuid:ABCD-1234','session':'11111111-1111-1111-1111-111111111111','sequence':1,'state':'copying','copied':12}
 assert status.update(data,'uuid:ABCD-1234')
 assert not status.update(data,'uuid:ABCD-1234')
 for payload in [dict(data,copied=-1),dict(data,device_id='wrong'),dict(data,path='/etc/shadow'),dict(data,client='other')]:
  with pytest.raises(ValueError):status.update(payload,'uuid:ABCD-1234')
 stored=json.loads(status.STATUS.read_text());assert status.public(stored,now=time.time()+901)['ubuntu']['state']=='interrupted'
 assert 'session' not in status.public(stored)['ubuntu']
 completed=dict(data,sequence=2,state='complete',completed_at=1700000000,retained=30)
 assert status.update(completed,'uuid:ABCD-1234')
 stored=json.loads(status.STATUS.read_text());assert stored['clients']['ubuntu']['last_sync']==1700000000
 assert len(stored['clients']['ubuntu']['history'])==1
 assert status.update(dict(completed,sequence=3),'uuid:ABCD-1234')
 assert len(json.loads(status.STATUS.read_text())['clients']['ubuntu']['history'])==1
