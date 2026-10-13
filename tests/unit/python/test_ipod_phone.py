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
