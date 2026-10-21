import importlib.util,sys,json
from pathlib import Path
from types import SimpleNamespace
TOOLS=Path(__file__).resolve().parents[3]/'tools'
spec=importlib.util.spec_from_file_location('sampling_resume_test',TOOLS/'resume_library_profiles.py')
resume=importlib.util.module_from_spec(spec);spec.loader.exec_module(resume)


def test_bounded_resume_keeps_local_and_provider_backlogs_moving(tmp_path,monkeypatch):
    (tmp_path/'audio_features.status.json').write_text(json.dumps({'deferred':250,'failed':1}))
    monkeypatch.setenv('BS5C_AUDIO_CACHE',str(tmp_path));calls=[]
    monkeypatch.setattr(resume.subprocess,'run',lambda args,**kw:calls.append(args) or SimpleNamespace(returncode=0))
    assert resume.main()==0 and len(calls)==2
    assert 'profile_music_library.py' in calls[0][1] and 'profile_provider_library.py' in calls[1][1]
    assert '--respect-retry-backoff' in calls[1] and calls[1][-1]=='1800'


def test_finished_local_scan_is_not_restarted_and_provider_fatal_error_propagates(tmp_path,monkeypatch):
    (tmp_path/'audio_features.status.json').write_text(json.dumps({'complete':True,'deferred':0,'failed':0}))
    monkeypatch.setenv('BS5C_AUDIO_CACHE',str(tmp_path));calls=[]
    monkeypatch.setattr(resume.subprocess,'run',lambda args,**kw:calls.append(args) or SimpleNamespace(returncode=-9))
    assert resume.main()==1 and len(calls)==1


def test_recovery_alternates_first_worker_to_avoid_starvation(tmp_path,monkeypatch):
    (tmp_path/'audio_features.status.json').write_text(json.dumps({'deferred':100}))
    monkeypatch.setenv('BS5C_AUDIO_CACHE',str(tmp_path));calls=[]
    monkeypatch.setattr(resume.subprocess,'run',lambda args,**kw:calls.append(args[1]) or SimpleNamespace(returncode=0))
    resume.main();resume.main()
    assert Path(calls[0]).name=='profile_music_library.py'
    assert Path(calls[2]).name=='profile_provider_library.py'
