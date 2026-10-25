import hashlib
import importlib.util
import json
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('continuity_prepare',ROOT/'prepare.py');prepare=importlib.util.module_from_spec(spec);spec.loader.exec_module(prepare)

@pytest.fixture
def setup(tmp_path,monkeypatch):
    source=tmp_path/'source';source.mkdir();(source/'a.py').write_text('value = 1\n')
    repair=tmp_path/'repair';repair.mkdir();(repair/'preimages.json').write_text(json.dumps({'a.py':hashlib.sha256((source/'a.py').read_bytes()).hexdigest()}))
    (repair/'music-assistant.patch').write_text('--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n')
    (repair/'helpers').mkdir()
    for name in ['tidal_dash_routes.py','stream_failure_recovery.py']:(repair/'helpers'/name).write_text('value = 1\n')
    monkeypatch.setattr(prepare,'ROOT',repair)
    return source,tmp_path/'output',repair

def test_preparer_copies_patched_sources_and_preserves_input(setup):
    source,output,_=setup
    assert len(prepare.prepare(source,output))==3
    assert (source/'a.py').read_text()=='value = 1\n'
    assert (output/'a.py').read_text()=='value = 2\n'

def test_changed_preimage_and_failed_patch_write_no_output(setup):
    source,output,repair=setup
    (source/'a.py').write_text('value = 3\n')
    with pytest.raises(ValueError):prepare.prepare(source,output)
    assert not output.exists()
    (source/'a.py').write_text('value = 1\n');(repair/'music-assistant.patch').write_text('invalid patch')
    with pytest.raises(ValueError):prepare.prepare(source,output)
    assert not output.exists() and (source/'a.py').read_text()=='value = 1\n'

def test_overlapping_paths_existing_output_or_helper_are_rejected(setup):
    source,output,_=setup
    for destination in [source,source/'nested',source.parent]:
        with pytest.raises(ValueError):prepare.prepare(source,destination)
    output.mkdir();(output/'precious.txt').write_text('keep')
    with pytest.raises(ValueError):prepare.prepare(source,output)
    assert (output/'precious.txt').read_text()=='keep'
    output.rename(output.with_name('old-output'));(source/'helpers').mkdir();(source/'helpers/tidal_dash_routes.py').write_text('existing')
    with pytest.raises(ValueError):prepare.prepare(source,output)
    assert not output.exists()
