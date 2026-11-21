import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('external_listens',Path(__file__).resolve().parents[1]/'services/external_listens.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class InboxTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.box=m.Inbox(Path(self.temp.name)/'inbox.sqlite')
        self.event=dict(type='external_listen',event_id='test',title='Song',artist='Artist',
            album='Album',room='dining',source='line_in',first_observed_at=100,confirmed_at=120,
            ended_at=140,listened_seconds=35,observations=2,confirmation_method='time_separated_audio')
        self.track=dict(name='Song',artists=[{'name':'Artist'}],provider='library',uri='library://track/1')
    def tearDown(self):self.temp.cleanup()
    def status(self):
        with self.box.db() as db:return db.execute('SELECT status FROM listens').fetchone()[0]
    def test_dedup_and_conflict(self):
        self.assertEqual(self.box.accept(self.event),'accepted')
        self.assertEqual(self.box.accept(self.event),'duplicate')
        with self.assertRaises(ValueError):self.box.accept(dict(self.event,title='Other'))
    def test_prediction_single_and_invalid_timing_rejected(self):
        for changes in ({'predicted':True},{'observations':1},{'listened_seconds':100},{'ended_at':float('nan')}):
            with self.assertRaises(ValueError):self.box.accept(dict(self.event,**changes))
    def test_exact_unique_library_resolution(self):
        self.assertEqual(m.resolve(self.event,[self.track]),self.track)
        for change in ({'version':'Live'},{'provider':'builtin'},{'name':'Another Song'}):
            self.assertIsNone(m.resolve(self.event,[dict(self.track,**change)]))
        self.assertIsNone(m.resolve(self.event,[self.track,dict(self.track,uri='library://track/2')]))
    def test_partial_play_and_idempotence(self):
        self.box.accept(self.event);calls=[]
        def api(command,args):
            calls.append((command,args));return {'tracks':[self.track]}
        self.box.deliver_one(api);self.assertEqual(self.status(),'ma_recorded')
        self.assertFalse(calls[-1][1]['fully_played']);self.assertEqual(calls[-1][1]['seconds_played'],35)
        self.box.deliver_one(api);self.assertEqual(len(calls),2)
    def test_uncertain_mark_is_not_retried(self):
        self.box.accept(self.event);calls=[]
        def api(command,args):
            calls.append(command)
            if command=='music/search':return {'tracks':[self.track]}
            raise TimeoutError()
        self.box.deliver_one(api);self.assertEqual(self.status(),'uncertain')
        self.box.deliver_one(api);self.assertEqual(len(calls),2)
    def test_missing_track_retained(self):
        self.box.accept(self.event);self.box.deliver_one(lambda *args:{'tracks':[]})
        self.assertEqual(self.status(),'unresolved')
