import asyncio
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result
history=module('ma_history_sync','services/lib/ma_history_sync.py')
video=module('video_metadata_policy','services/lib/video_metadata_policy.py')

class Model:
    def __init__(self):self.values={}
    def get_kv(self,key,default):return deepcopy(self.values.get(key,default))
    def put_kv(self,key,value):self.values[key]=deepcopy(value)

class Familiarity:
    @staticmethod
    async def collect(command,previous):return deepcopy(previous),{}
    @staticmethod
    def track(item):return item if isinstance(item,dict) and item.get('artist') else None
    @staticmethod
    def observe(cache,item,source,**values):
        entry=cache.setdefault(item['uri'],{'item':dict(item),'signals':{}})
        entry['signals'].setdefault(source,values)

class HistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_only_recent_sync_deduplicates_without_fake_listens(self):
        model=Model();calls=[]
        item=dict(uri='library://track/1',name='Song',artist='Artist',media_type='track')
        async def command(name,args):
            calls.append(name)
            if name=='music/recently_played_items':return [dict(uri=item['uri'],media_type='track')]*2
            if name=='music/item_by_uri':return item
            raise AssertionError('Unexpected write')
        cache,status=await history.collect(command,{},model,Familiarity)
        again,status=await history.collect(command,cache,model,Familiarity)
        self.assertEqual(cache,again);self.assertEqual(len(again),1)
        self.assertEqual(calls.count('music/item_by_uri'),1)
        snapshot=model.values['ma_recent_history_v1']
        self.assertEqual(len(snapshot['items']),1);self.assertFalse(snapshot['timestamps_available'])
        self.assertEqual(status['refresh_interval_seconds'],3600)
        self.assertNotIn('reward',snapshot['items'][0]);self.assertNotIn('room',snapshot['items'][0])

    async def test_failure_retains_last_snapshot(self):
        model=Model();model.values['ma_recent_history_v1']={'items':[{'uri':'old'}]}
        async def failed(*args):raise TimeoutError()
        _,status=await history.collect(failed,{},model,Familiarity)
        self.assertEqual(model.values['ma_recent_history_v1']['items'],[{'uri':'old'}])
        self.assertEqual(status['ma_recent_error'],'TimeoutError')

    async def test_hourly_loop_refreshes_immediately_then_each_hour(self):
        class Service:
            calls=0
            async def _refresh_familiarity(self):self.calls+=1
        service=Service();waits=[]
        async def sleep(seconds):
            waits.append(seconds)
            if len(waits)==2:raise asyncio.CancelledError()
        times=iter([100,102,3700,3702])
        with patch.object(history,'time',SimpleNamespace(monotonic=lambda:next(times))),patch.object(history.asyncio,'sleep',side_effect=sleep):
            with self.assertRaises(asyncio.CancelledError):await history.run_hourly(service)
        self.assertEqual(service.calls,2);self.assertEqual(waits,[3598,3598])

class VideoTests(unittest.TestCase):
    def test_analogue_labels_never_become_video_queries(self):
        for source in ('AUX','A.AUX','CD','Tape','Phono','Line In','—'):
            payload=dict(title=source,artist=source,app_name=source,music_video_url='stale',canvas_url='stale')
            result=video.sanitize(payload)
            self.assertFalse(result['music_video_allowed']);self.assertEqual(result['music_video_url'],'')
            self.assertEqual(result['canvas_url'],'')

    def test_real_metadata_allowed_but_missing_expired_or_predicted_is_blocked(self):
        payload=dict(title='Real Song',artist='Real Artist',app_name='A.AUX',metadata_available=True)
        self.assertTrue(video.allowed(payload))
        for change in ({'metadata_available':False},{'artist':''},{'predicted':True},{'metadata_expires_at':1}):
            self.assertFalse(video.allowed(dict(payload,**change)))

    def test_network_metadata_and_video_urls_remain_available(self):
        payload=dict(title='Song',artist='Artist',music_video_url='https://example/video')
        self.assertEqual(video.sanitize(payload)['music_video_url'],payload['music_video_url'])
