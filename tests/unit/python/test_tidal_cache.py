import base64,json,socket,unittest
from unittest.mock import patch
from services.lib.tidal_cache import direct_flac,ProtectedAudio,validate_url,select,score

def asset(**changes):
    data={'encryptionType':'NONE','codecs':'flac','mimeType':'audio/flac','urls':['https://audio.tidal.com/song.flac']};data.update(changes)
    return {'audioQuality':'LOSSLESS','manifestMimeType':'application/vnd.tidal.bts','manifest':base64.b64encode(json.dumps(data).encode()).decode()}
def track(n,count,album='a'):
    return {'uri':f'library://track/{n}','name':str(n),'play_count':count,'metadata':{'explicit':True},'album':{'item_id':album,'provider':'library','name':album},'provider_mappings':[{'provider_domain':'tidal','provider_instance':'tidal--one','item_id':str(n)}]}
class CacheTests(unittest.TestCase):
    def test_plain_flac(self):self.assertEqual(direct_flac(asset()),'https://audio.tidal.com/song.flac')
    def test_reject_protection(self):
        for changes in [{'encryptionType':'AES'},{'encryptionType':None},{'securityToken':'secret'},{'keyId':'key'},{'licenseUrl':'https://example.com'}]:
            with self.subTest(changes=changes),self.assertRaises(ProtectedAudio):direct_flac(asset(**changes))
    def test_reject_dash_even_if_lossless(self):
        a=asset();a['manifestMimeType']='application/dash+xml'
        with self.assertRaises(ProtectedAudio):direct_flac(a)
    def test_reject_not_flac(self):
        with self.assertRaises(ValueError):direct_flac(asset(codecs='mp4a',mimeType='audio/mp4'))
    def test_reject_untrusted_url(self):
        for u in ['http://audio.tidal.com/a','https://tidal.com.evil.example/a','https://user:pass@tidal.com/a','https://127.0.0.1/a']:
            with self.subTest(url=u),self.assertRaises(ValueError):validate_url(u,resolve=False)
    def test_reject_private_dns(self):
        with patch('socket.getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))]),self.assertRaises(ValueError):validate_url('https://audio.tidal.com/a')
    def test_personal_ranking_and_album_aggregation(self):
        rows=[track(1,2,'a'),track(2,3,'a'),track(3,4,'b'),track(4,1,'c')]
        chosen,albums=select(rows,{'tidal://track/4':10},2,2)
        self.assertEqual([x['name'] for x in chosen],['4','3']);self.assertEqual([a['name'] for a in albums],['c','a'])
    def test_provider_alias_and_no_double_counting(self):self.assertEqual(score(track(1,10),{'tidal--one://track/1':15,'tidal://track/1':15}),15)
    def test_duplicate_tidal_id(self):
        a=track(1,2);b=track(1,1);b['uri']='library://track/99';self.assertEqual(len(select([a,b],{},250,20)[0]),1)

class WorkerTests(unittest.TestCase):
    def test_busy_transfer_cleans_partial_and_preserves_music(self):
        import sys,importlib.util,tempfile,io
        from pathlib import Path
        sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'services'))
        spec=importlib.util.spec_from_file_location('tidal_download_test',Path(__file__).resolve().parents[3]/'services/tidal_download.py');worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
        class Response(io.BytesIO):headers={'Content-Length':'8'}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);keep=root/'existing.flac';keep.write_bytes(b'existing music');dest=root/'new.flac'
            opener=unittest.mock.Mock();opener.open.return_value=Response(b'fLaC1234')
            with patch.object(worker,'ROOT',root),patch.object(worker,'validate_url'),patch.object(worker,'require_idle',side_effect=[None,worker.Busy()]),patch.object(worker.urllib.request,'build_opener',return_value=opener):
                with self.assertRaises(worker.Busy):worker.transfer({'kind':'direct','urls':['https://audio.tidal.com/a'],'bits':16,'sample_rate':44100},dest,0,{})
            self.assertEqual(keep.read_bytes(),b'existing music');self.assertFalse(dest.exists());self.assertFalse(dest.with_suffix('.part').exists())
    def test_unknown_player_state_is_busy(self):
        import sys,importlib.util
        from pathlib import Path
        sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'services'))
        spec=importlib.util.spec_from_file_location('tidal_download_idle_test',Path(__file__).resolve().parents[3]/'services/tidal_download.py');worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
        for values in [[],[{'available':True,'playback_state':'paused'}],[{'available':True,'playback_state':'unknown'}],[{'available':True,'playback_state':'playing'}]]:
            with patch.object(worker,'command',return_value=values):self.assertFalse(worker.idle())

class MaximumTests(unittest.TestCase):
    def manifest(self,protection=''):
        from services.lib.tidal_cache import maximum_asset
        text='<MPD type="static" mediaPresentationDuration="PT10S"><Period><AdaptationSet>'+protection+'<Representation id="low" codecs="flac" audioSamplingRate="44100" bandwidth="700000"><SegmentTemplate initialization="https://audio.tidal.com/init" media="https://audio.tidal.com/$RepresentationID$/$Number%03d$-$Time$" timescale="1"><SegmentTimeline><S d="2" r="4"/></SegmentTimeline></SegmentTemplate></Representation><Representation id="high" codecs="flac" audioSamplingRate="96000" bandwidth="2000000"><SegmentTemplate initialization="https://audio.tidal.com/init" media="https://audio.tidal.com/$RepresentationID$/$Number%03d$-$Time$" timescale="1"><SegmentTimeline><S d="2" r="4"/></SegmentTimeline></SegmentTemplate></Representation></AdaptationSet></Period></MPD>'
        return {'manifestMimeType':'application/dash+xml','manifest':base64.b64encode(text.encode()).decode(),'audioQuality':'HI_RES_LOSSLESS','bitDepth':24}
    def test_highest_representation_and_finite_segments(self):
        from services.lib.tidal_cache import maximum_asset
        a=maximum_asset(self.manifest());self.assertEqual(a['sample_rate'],96000);self.assertEqual(a['bits'],24);self.assertEqual(len(a['urls']),6);self.assertEqual(a['urls'][-1],'https://audio.tidal.com/high/005-8')
    def test_protected_dash_rejected(self):
        from services.lib.tidal_cache import maximum_asset
        with self.assertRaises(ProtectedAudio):maximum_asset(self.manifest('<ContentProtection schemeIdUri="test"/>'))
    def test_unknown_and_clean_excluded(self):
        from services.lib.tidal_cache import explicit
        self.assertFalse(explicit({'metadata':{'explicit':False}}));self.assertFalse(explicit({}));self.assertFalse(explicit({'metadata':{'explicit':True},'version':'Clean'}));self.assertTrue(explicit({'metadata':{'explicit':True}}))
    def test_transfer_preserves_24bit_96khz(self):
        import sys,importlib.util,tempfile,io,subprocess,shutil
        from pathlib import Path
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):self.skipTest('ffmpeg unavailable')
        sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'services'))
        spec=importlib.util.spec_from_file_location('tidal_download_quality_test',Path(__file__).resolve().parents[3]/'services/tidal_download.py');worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
        class Response(io.BytesIO):pass
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source.flac';dest=root/'saved.flac'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:duration=1','-ar','96000','-sample_fmt','s32','-c:a','flac',str(source)],check=True,capture_output=True)
            data=source.read_bytes();response=Response(data);response.headers={'Content-Length':str(len(data))};opener=unittest.mock.Mock();opener.open.return_value=response
            with patch.object(worker,'ROOT',root),patch.object(worker,'validate_url'),patch.object(worker,'require_idle'),patch.object(worker.urllib.request,'build_opener',return_value=opener):
                record=worker.transfer({'kind':'direct','urls':['https://audio.tidal.com/test'],'bits':24,'sample_rate':96000},dest,0,{'name':'Test','artists':[{'name':'Artist'}]})
            self.assertEqual(record['bit_depth'],24);self.assertEqual(record['sample_rate'],96000)
            def decode(p):return subprocess.check_output(['ffmpeg','-v','error','-i',str(p),'-f','s32le','-'])
            self.assertEqual(decode(source),decode(dest));self.assertFalse(dest.with_suffix('.part').exists())
