import importlib.util
from pathlib import Path
import json
import pytest

spec=importlib.util.spec_from_file_location('audio_worker', Path(__file__).parents[3]/'tools/analyze_music_audio.py')
worker=importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


def test_documented_model_output_order_and_scale():
    assert worker.normalize_predictions([[1,9],[1,9]]) == {'energy':1,'valence':0}
    assert worker.normalize_predictions([[5,5]]) == {'energy':.5,'valence':.5}
    for value in [[], [[1]], [[1,float('nan')]], [1,2]]:
        with pytest.raises(ValueError): worker.normalize_predictions(value)


def test_sidecar_atomic_replacement_preserves_full_cache(tmp_path):
    path=tmp_path/'features.json'
    first={'version':1,'tracks':{'a':{'audio_features':{'model':'m','energy':.2,'valence':.4}}}}
    worker.atomic_write(path,first)
    second=json.loads(path.read_text())
    second['tracks']['b']={'audio_features':{'bpm':100}}
    worker.atomic_write(path,second)
    assert json.loads(path.read_text())==second
    assert len(list(tmp_path.iterdir()))==1
    with pytest.raises(ValueError):worker.atomic_write(path,{'bad':float('nan')})
    assert json.loads(path.read_text())==second
    assert len(list(tmp_path.iterdir()))==1


def test_samples_are_bounded_and_short_recordings_are_not_triplicated(monkeypatch):
    np=pytest.importorskip('numpy')
    calls=[]
    def output(command,timeout):
        calls.append(command)
        if command[0]=='ffprobe':return b'20\n'
        assert command[command.index('-t')+1]=='30'
        assert command[command.index('-ar')+1]=='16000'
        return np.zeros(20*16000,dtype='<f4').tobytes()
    monkeypatch.setattr(worker.subprocess,'check_output',output)
    assert len(list(worker.segments(Path('/music/file.flac'))))==1
    assert len(calls)==2


def test_embedding_rounding_does_not_expand_float32_binary_noise():
    np=pytest.importorskip('numpy')
    assert worker.rounded_embedding(np.array([[.12345679, .3]], dtype=np.float32)) == [.123457, .3]
