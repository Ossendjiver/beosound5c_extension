"""Offline updater tests. No network, MA database or speaker interactions."""
import importlib.util
from pathlib import Path
import sys
import subprocess
import pytest

TOOLS = Path(__file__).parents[3]/'tools'
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location('profiler', TOOLS/'profile_music_library.py')
profiler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profiler)


class Analyzer:
    fingerprint = 'model-v1'
    def analyze(self, samples):
        list(samples)
        return {'model': 'test', 'energy': .2, 'valence': .4}


def entry(path, uri='library://track/1'):
    return {'uri': uri, 'aliases': [uri, 'tidal://track/2'], 'path': str(path)}


def test_exact_aliases_but_no_traversal(tmp_path):
    song = tmp_path/'song.flac'
    song.write_bytes(b'audio')
    outside = tmp_path.parent/'outside.flac'
    outside.write_bytes(b'other')
    songs = [{'uri': 'library://track/1', 'provider_mappings': [
        {'provider_domain': 'filesystem_local', 'provider_instance': 'files', 'item_id': 'song.flac', 'available': True},
        {'provider_domain': 'tidal', 'provider_instance': 'tidal', 'item_id': '2'}]},
        {'uri': 'library://track/3', 'provider_mappings': [
        {'provider_domain': 'filesystem_local', 'provider_instance': 'files', 'item_id': '../outside.flac', 'available': True}]}]
    entries = profiler.inventory(songs, tmp_path)
    assert entries[0]['path'] == str(song)
    assert entries[0]['aliases'] == ['files://track/song.flac', 'library://track/1', 'tidal://track/2']
    assert entries[1]['path'] is None


def test_unchanged_file_skipped_new_alias_integrated_and_new_file_sampled(tmp_path):
    song = tmp_path/'song'; song.write_bytes(b'first')
    entries = [entry(song)]
    payload = {'version': 1, 'tracks': {}}
    calls = []
    def sample(path):
        calls.append(path)
        return []
    assert profiler.update(entries, payload, Analyzer(), sample=sample)[0]['analysed'] == 1
    entries[0]['aliases'].append('other://track/8')
    counts, changed = profiler.update(entries, payload, Analyzer(), sample=sample)
    assert counts['unchanged'] == 1 and changed and len(calls) == 1
    song.write_bytes(b'changed longer')
    assert profiler.update(entries, payload, Analyzer(), sample=sample)[0]['analysed'] == 1
    assert len(calls) == 2


def test_failures_retry_next_day_work_bounded_and_provider_audio_not_requested(tmp_path):
    paths = [tmp_path/str(n) for n in range(3)]
    for path in paths: path.write_bytes(b'a')
    entries = [entry(p, str(n)) for n,p in enumerate(paths)] + [{'uri': 'cloud', 'path': None}]
    payload = {'version': 1, 'tracks': {}}
    def fail(path): raise subprocess.CalledProcessError(1, 'decoder')
    counts, _ = profiler.update(entries, payload, Analyzer(), max_tracks=1, sample=fail, now=100)
    assert counts == {'unchanged': 0, 'analysed': 0, 'failed': 1, 'no_local_audio': 1, 'deferred': 2}
    counts, _ = profiler.update(entries[:1], payload, Analyzer(), sample=fail, now=200)
    assert counts['deferred'] == 1 and counts['failed'] == 0
    counts, _ = profiler.update(entries[:1], payload, Analyzer(), sample=fail, now=86501)
    assert counts['failed'] == 1


def test_modified_during_analysis_keeps_previous_good_profile(tmp_path):
    song = tmp_path/'song';song.write_bytes(b'a')
    payload = {'version': 1, 'tracks': {'library://track/1': {'fingerprint': 'previous', 'audio_features': {'energy': .1}}}}
    def sample(path):
        path.write_bytes(b'changed')
        return []
    counts, _ = profiler.update([entry(song)], payload, Analyzer(), sample=sample)
    assert counts['failed'] == 1
    assert payload['tracks']['library://track/1']['fingerprint'] == 'previous'


def test_catalogue_reads_supported_api_only():
    calls = []
    def command(name,args):
        calls.append((name,args))
        return [{'uri': str(n)} for n in range(500)] if args['offset'] == 0 else []
    assert len(profiler.catalogue(command)) == 500
    assert [c[1]['offset'] for c in calls] == [0, 500]
    assert all(c[0] == 'music/tracks/library_items' for c in calls)


def test_onnx_frontend_shapes_and_tempo():
    np = pytest.importorskip('numpy')
    from music_audio_onnx import mel_spectrogram, patches, tempo
    with pytest.raises(ValueError):mel_spectrogram(np.zeros(10))
    with pytest.raises(ValueError):mel_spectrogram(np.full(30*16000, np.nan))
    audio = np.zeros(30*16000, dtype=np.float32)
    for second in np.arange(0,30,.5):
        start = int(second*16000)
        audio[start:start+500] = np.sin(np.arange(500)*.4)*np.exp(-np.arange(500)/100)
    mels = mel_spectrogram(audio)
    assert patches(mels).shape == (10, 187, 96)
    bpm, confidence = tempo(mels)
    assert abs(bpm-120) < 3 and confidence >= .15


def test_library_calibration_retains_raw_scores_and_ignores_familiarity():
    from lib.music_features import library_calibration, calibrate
    records = [{'audio_features': {'model': 'm', 'energy': .3+i*.004, 'valence': .35+i*.003},
                'play_count': 100000 if i == 0 else 0} for i in range(100)]
    before = [dict(i['audio_features']) for i in records]
    calibration = library_calibration(records)
    assert calibration['models']['m']['count'] == 100
    assert records[0]['audio_features'] == before[0]
    assert calibrate(before[0], calibration)['energy'] == 0
    assert calibrate(before[-1], calibration)['energy'] == 1
    assert calibrate({'model': 'other', 'energy': .4}, calibration)['energy'] == .4
    for r in records:r['play_count'] = 0
    assert library_calibration(records) == calibration
    assert library_calibration(records[:99])['models'] == {}
    assert library_calibration([{'audio_features': {'model':'m','energy':.5,'valence':.5}}]*100)['models'] == {}


def test_batched_inference_preserves_section_coverage_and_rejects_silence():
    np=pytest.importorskip('numpy')
    from music_audio_onnx import Analyzer
    calls=[]
    class Embedding:
        def run(self,outputs,inputs):
            shape=inputs['melspectrogram'].shape
            calls.append(shape)
            return [np.tile(np.arange(200,dtype=np.float32), (shape[0],1))]
    class Mood:
        def run(self,outputs,inputs):
            return [np.tile(np.array([5,7],dtype=np.float32), (len(inputs['embeddings']),1))]
    analyzer=object.__new__(Analyzer)
    from types import SimpleNamespace
    analyzer.heads=SimpleNamespace(predict=lambda values: {})
    analyzer.embedding=Embedding();analyzer.mood=Mood()
    audio=np.sin(np.arange(30*16000,dtype=np.float32)*.04)*.1
    result=analyzer.analyze([audio]*3)
    assert calls==[(12,187,96)]
    assert result['sample_seconds']==90 and result['inference_patches']==12
    assert result['energy']==.75 and result['valence']==.5
    with pytest.raises(ValueError,match='No usable audio'):
        analyzer.analyze([np.zeros(30*16000,dtype=np.float32)])
    assert len(calls)==1
