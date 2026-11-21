import asyncio
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('presence_limit',Path(__file__).resolve().parents[1]/'services/lib/volume_adapters/presence_limit.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class Response:
    status=200
    def __init__(self,state):self.state=state
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass
    async def json(self):return {'state':self.state}
class Session:
    def __init__(self,state):self.state=state;self.calls=0
    def get(self,*args,**kwargs):self.calls+=1;return Response(self.state)

class PresenceTests(unittest.IsolatedAsyncioTestCase):
    def test_adapter_patcher_is_scoped_and_idempotent(self):
        spec=importlib.util.spec_from_file_location('patcher',Path(__file__).resolve().parents[1]/'tools/apply_dining_presence_limit.py')
        patcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(patcher)
        text='''from .base import VolumeAdapter
# unrelated customisation is preserved
class H:
    def __init__(self):
        self._ha_token = os.getenv("HA_TOKEN", "").strip()
    async def read(self):
        return level * 100 if level is not None else None
    async def apply(self):
        limit = max(level, self._max_volume / 100)
'''
        result=patcher.patched(text)
        self.assertIn('# unrelated customisation is preserved',result)
        self.assertEqual(patcher.patched(result),result)
        with self.assertRaises(ValueError):patcher.patched('incompatible version')
    async def test_only_home_dining_is_exempt(self):
        for state,expected in [('home',100),('not_home',70),('unknown',70),('unavailable',70),('work',70)]:
            with patch.dict(os.environ,BS5C_DINING_HOME_EXEMPT='1'):
                session=Session(state);policy=m.PresenceLimit(session,'http://ha/api',lambda:{},70)
            self.assertEqual(await policy.limit('media_player.dining'),expected)
            self.assertEqual(await policy.limit('media_player.the_frame'),70)
            self.assertEqual(session.calls,1)
    async def test_cached_read_and_disabled_default(self):
        with patch.dict(os.environ,BS5C_DINING_HOME_EXEMPT='1'):
            session=Session('home');policy=m.PresenceLimit(session,'http://ha/api',lambda:{},70)
        await policy.limit('media_player.dining');await policy.limit('media_player.dining')
        self.assertEqual(session.calls,1)
        with patch.dict(os.environ,BS5C_DINING_HOME_EXEMPT='0'):
            disabled=m.PresenceLimit(session,'http://ha/api',lambda:{},70)
        self.assertEqual(await disabled.limit('media_player.dining'),70)
