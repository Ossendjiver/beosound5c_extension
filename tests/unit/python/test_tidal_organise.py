import sys,json,tempfile,unittest,threading,urllib.request,urllib.error
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'services'))
from lib.tidal_organise import destination,move_no_replace
import tidal_settings as settings

def record(mapping=None):return {'size':1,'bit_depth':24,'sample_rate':48000,'item':{'name':'Sticky','artists':[{'name':'Drake'}],'album':{'name':'Honestly, Nevermind'},'track_number':7,'provider_mappings':[mapping] if mapping else []}}
class OrganiseTests(unittest.TestCase):
 def test_existing_album_folder_reused(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);folder=root/'Drake/Drake - Honestly, Nevermind (2022) [FLAC] vtwin88cube';folder.mkdir(parents=True)
   self.assertEqual(destination(root,record(),'123'),folder/'07. Sticky.flac')
 def test_exact_mapping_beats_album_guess(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);folder=root/'Drake/Existing album';folder.mkdir(parents=True);(folder/'original.mp3').write_bytes(b'a')
   r=record({'provider_domain':'filesystem_local','item_id':'Drake/Existing album/original.mp3'})
   self.assertEqual(destination(root,r,'123').parent,folder)
 def test_collision_preserves_original(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);p=destination(root,record(),'123');p.parent.mkdir(parents=True);p.write_bytes(b'original')
   self.assertIn('Tidal 123',destination(root,record(),'123').name);self.assertEqual(p.read_bytes(),b'original')
 def test_atomic_move_never_overwrites(self):
  with tempfile.TemporaryDirectory() as tmp:
   a=Path(tmp)/'a';b=Path(tmp)/'b';a.write_bytes(b'new');b.write_bytes(b'old')
   with self.assertRaises(OSError):move_no_replace(a,b)
   self.assertEqual(a.read_bytes(),b'new');self.assertEqual(b.read_bytes(),b'old');b.unlink();move_no_replace(a,b);self.assertFalse(a.exists());self.assertEqual(b.read_bytes(),b'new')
 def test_escape_rejected(self):
  with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
   root=Path(tmp);(root/'Drake').symlink_to(outside,target_is_directory=True)
   with self.assertRaises(ValueError):destination(root,record(),'123')
 def test_counts_steps(self):
  for v in [0,49,51,251,10001,True,'250']:
   with self.assertRaises(ValueError):settings.valid_count(v)
  for v in [50,250,1000,10000]:self.assertEqual(settings.valid_count(v),v)
 def test_local_settings_endpoint_and_cors_protection(self):
  with tempfile.TemporaryDirectory() as tmp:
   cfg=Path(tmp)/'config.json';cfg.write_text(json.dumps({'tracks':250,'albums':20}));saved=Path(tmp)/'settings.json'
   from unittest.mock import patch
   with patch.object(settings,'CONFIG',cfg),patch.object(settings,'SETTINGS',saved):
    server=settings.ThreadingHTTPServer(('127.0.0.1',0),settings.Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    url=f'http://127.0.0.1:{server.server_port}/settings'
    try:
     req=urllib.request.Request(url,data=b'{"tracks":350}',headers={'Content-Type':'application/json','Origin':'http://127.0.0.1'})
     with urllib.request.urlopen(req) as r:self.assertEqual(json.load(r)['tracks'],350)
     self.assertEqual(json.loads(saved.read_text())['tracks'],350)
     req=urllib.request.Request(url,data=b'{"tracks":1000}',headers={'Content-Type':'application/json','Origin':'https://evil.example'})
     with self.assertRaises(urllib.error.HTTPError) as err:urllib.request.urlopen(req)
     self.assertEqual(err.exception.code,403);self.assertEqual(json.loads(saved.read_text())['tracks'],350)
    finally:server.shutdown();server.server_close();thread.join()
