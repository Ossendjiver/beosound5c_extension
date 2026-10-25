import datetime as dt
import importlib.util
from pathlib import Path
spec=importlib.util.spec_from_file_location('night',Path(__file__).parents[3]/'tools/analysis_night_window.py')
night=importlib.util.module_from_spec(spec);spec.loader.exec_module(night)

def local(value): return dt.datetime.fromisoformat(value).replace(tzinfo=night.ZONE)

def test_boundaries():
    for stamp,allowed in [('2026-10-25T22:59:59',False),('2026-10-25T23:00:00',True),('2026-10-26T04:59:59',True),('2026-10-26T05:00:00',False)]:
        assert night.in_window(local(stamp)) == allowed

def test_same_night_once():
    assert night.night_key(local('2026-10-25T23:30')) == night.night_key(local('2026-10-26T04:30'))

def test_dst_and_standard_time():
    for stamp in ['2026-01-01T12:30:00+00:00','2026-07-01T13:30:00+00:00']:
        assert night.in_window(dt.datetime.fromisoformat(stamp))
    assert not night.in_window(dt.datetime.fromisoformat('2026-01-01T18:00:00+00:00'))
