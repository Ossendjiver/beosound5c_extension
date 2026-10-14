import sys,unittest,tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'services'))
from lib.library_tag_policy import *
from lib.tidal_organise import destination
import tidal_download as t
class Tests(unittest.TestCase):
 def test_generic_composer_is_not_an_artist(self):self.assertEqual(classical_composers({'genre':['Classical'],'composer':['Various']}),[])
 def test_stale_album_role_id_is_not_reused_for_composer(self):
  tags={'albumartist':['Ludwig van Beethoven'],'musicbrainz_albumartistid':['various-id']}
  original={'albumartist':['Various Artists']}
  self.assertEqual(album_identity_updates(tags,'Ludwig van Beethoven',original,{key('Ludwig van Beethoven'):['composer-id']}),{'musicbrainz_albumartistid':['composer-id']})
  self.assertEqual(album_identity_updates(tags,'Ludwig van Beethoven',original,{}),{'musicbrainz_albumartistid':None})
 def test_existing_verified_album_identity_preserved(self):
  tags={'albumartist':['Aitch'],'musicbrainz_albumartistid':['aitch-id']}
  self.assertEqual(album_identity_updates(tags,'Aitch',tags,{}),{})
 def test_old_albumartist_list_does_not_override_main_composer(self):
  self.assertEqual(album_identity_updates({'albumartists':['Composer; Performer']},'Composer',{},{}),{'albumartists':['Composer']})
 def test_primary_not_guest(self):self.assertEqual(provider_album_artist({'artist':{'name':'Aitch'},'artists':[{'name':'Ashanti'}]}),'Aitch')
 def test_band_not_split(self):self.assertEqual(folder_artist('Earth, Wind & Fire',['Earth, Wind & Fire']),'Earth, Wind & Fire')
 def test_pop_composer(self):self.assertEqual(classical_composers({'genre':['Pop'],'composer':['Taylor Swift']}),[])
 def test_composer_over_performer(self):self.assertEqual(classical_composers({'genre':['Classical'],'composer':['L. van Beethoven'],'artist':['Alfred Brendel']}),['Ludwig van Beethoven'])
 def test_ambiguous_bach(self):self.assertEqual(canonical('Bach'),'Bach')
 def test_handel(self):self.assertEqual(canonical('George Freidrich Handel'),'George Frideric Handel')
 def test_multi_composer(self):self.assertEqual(classical_composers({'genre':['Classical'],'composer':['Mozart; Beethoven']}),['Wolfgang Amadeus Mozart','Ludwig van Beethoven'])
 def test_album_metadata_cached_and_route_uses_primary(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(t,'CACHE',Path(tmp)),patch.object(t,'request',return_value={'artist':{'name':'Aitch'}}) as request:
   item={'name':'Baby','artists':[{'name':'Ashanti'},{'name':'Aitch'}],'album':{'name':'Baby'}}
   result=t.with_album_artist(item,{'album':{'id':123}},{'country':'AU','token':'unused-test'})
   self.assertEqual(result['album_artist'],'Aitch');self.assertEqual(result['artists'],item['artists'])
   self.assertEqual(destination(Path(tmp),{'item':result},'1').parent,Path(tmp)/'Aitch/Baby')
   t.with_album_artist(item,{'album':{'id':123}},{'country':'AU','token':'unused-test'});self.assertEqual(request.call_count,1)
 def test_classical_download_preserves_performer_and_prefers_composer(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(t,'CACHE',Path(tmp)),patch.object(t,'request',return_value={'artist':{'name':'Alfred Brendel'}}):
   result=t.with_album_artist({'artists':[{'name':'Ludwig van Beethoven'},{'name':'Alfred Brendel'}]}, {'album':{'id':123}}, {'country':'AU','token':'unused-test'})
   self.assertEqual(result['album_artist'],'Ludwig van Beethoven');self.assertEqual(result['performer'],'Alfred Brendel')
 def test_uncertain_provider_metadata_rejected(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(t,'CACHE',Path(tmp)),patch.object(t,'request',return_value={'artists':[{'name':'Guest'}]}):
   with self.assertRaises(ValueError):t.with_album_artist({}, {'album':{'id':123}}, {'country':'AU','token':'unused-test'})
if __name__=='__main__':unittest.main()
