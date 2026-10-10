#!/usr/bin/env python3
"""Offline audio analysis sidecar. No HA calls, streaming, or MA database access.

Manifest: [{"uri":"library://track/123", "path":"/music/song.flac"}]
Requires ffmpeg, numpy, essentia-tensorflow and the two documented model files.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile

MODEL = 'emomusic-msd-musicnn-2'
EMBEDDING = 'msd-musicnn-1'


def normalize_predictions(predictions):
    """emoMusic output order is valence, arousal, each on the [1,9] scale."""
    values = list(predictions)
    if not values or any(not hasattr(row, '__len__') or len(row) != 2 or
                         not all(math.isfinite(float(v)) for v in row) for row in values):
        raise ValueError('Invalid emoMusic output')
    valence, arousal = [max(0, min(1, (sum(float(row[i]) for row in values)/len(values)-1)/8)) for i in (0,1)]
    return {'energy': float(arousal), 'valence': float(valence)}


def rounded_embedding(vectors):
    import numpy as np
    # Convert before rounding: numpy.float32.tolist() otherwise expands the
    # binary approximation back to long decimals, doubling sidecar size.
    return [round(float(value), 6) for value in np.mean(vectors, axis=0)]


def atomic_write(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as target:
            temporary = target.name
            json.dump(payload, target, separators=(',', ':'), allow_nan=False)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
        # The sidecar should survive a reboot as well as the rename being atomic.
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def segments(path):
    import numpy as np
    duration = float(subprocess.check_output(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
         '-of', 'default=noprint_wrappers=1:nokey=1', str(path)], timeout=20))
    if not 15 <= duration <= 86400:
        raise ValueError('Audio duration outside supported range')
    # Sample intro/body/outro independently; do not join discontinuities for BPM.
    offsets = sorted({max(0, min(duration-30, duration*fraction)) for fraction in (.1,.45,.75)})
    for offset in offsets:
        raw = subprocess.check_output(
            ['ffmpeg', '-nostdin', '-v', 'error', '-threads', '1', '-ss', str(offset), '-i', str(path),
             '-t', '30', '-ac', '1', '-ar', '16000', '-f', 'f32le', 'pipe:1'], timeout=60)
        audio = np.frombuffer(raw, dtype='<f4').copy()
        if len(audio) < 15*16000 or not np.isfinite(audio).all():
            raise ValueError('Invalid or too-short audio sample')
        yield audio


def analyze(path, embedding_model, mood_model):
    import numpy as np
    import essentia.standard as es
    predictions, vectors, tempos, centroids, levels = [], [], [], [], []
    section_count = 0
    sample_seconds = 0
    for audio in segments(path):
        section_count += 1
        sample_seconds += len(audio)/16000
        embeddings = embedding_model(audio)
        predictions.extend(mood_model(embeddings))
        vectors.extend(embeddings)
        # RhythmExtractor expects 44.1kHz input; ML models expect 16kHz.
        rhythm_audio = es.Resample(inputSampleRate=16000, outputSampleRate=44100)(audio)
        bpm, _, confidence, _, _ = es.RhythmExtractor2013(method='multifeature')(rhythm_audio)
        if confidence > 0 and 30 <= bpm <= 300:
            tempos.append(float(bpm))
        levels.append(float(np.sqrt(np.mean(audio*audio))))
        frames = audio[:len(audio)//1024*1024].reshape(-1,1024)
        spectrum = abs(np.fft.rfft(frames*np.hanning(1024), axis=1))
        mass = spectrum.sum(axis=1)
        valid = mass > 1e-8
        if valid.any():
            centroids.append(float(np.mean((spectrum[valid]*np.fft.rfftfreq(1024,1/16000)).sum(axis=1)/mass[valid])))
    result = normalize_predictions(predictions)
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'services'))
    from lib import profile_quality
    result.update(profile_quality.summary([normalize_predictions([p]) for p in predictions], section_count, sample_seconds))
    result.update(model=MODEL, embedding_model=EMBEDDING,
                  embedding=rounded_embedding(vectors),
                  rms=float(np.mean(levels)), sample_seconds=sample_seconds)
    if tempos:
        result['bpm'] = float(np.median(tempos))
    if centroids:
        result['spectral_centroid_hz'] = float(np.mean(centroids))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--models', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    import essentia.standard as es
    embedding_path, mood_path = args.models/(EMBEDDING+'.pb'), args.models/(MODEL+'.pb')
    fingerprint = hashlib.sha256(b'profile-quality-v1'+embedding_path.read_bytes()+mood_path.read_bytes()).hexdigest()
    embedding_model = es.TensorflowPredictMusiCNN(graphFilename=str(embedding_path), output='model/dense/BiasAdd')
    mood_model = es.TensorflowPredict2D(graphFilename=str(mood_path), output='model/Identity')
    payload = json.loads(args.output.read_text()) if args.output.exists() else {'version':1, 'tracks':{}}
    if payload.get('version') != 1 or not isinstance(payload.get('tracks'), dict):
        raise ValueError('Unsupported feature cache')
    manifest = json.loads(args.manifest.read_text())
    if not isinstance(manifest, list):
        raise ValueError('Manifest must be a list')
    failed = 0
    for entry in manifest:
        try:
            uri = entry['uri']
            if not isinstance(uri, str) or not uri or len(uri) > 1024:
                raise ValueError('Invalid recording URI')
            path = Path(entry['path']).resolve(strict=True)
            if not path.is_file():
                raise ValueError('A local audio file is required')
            stat = path.stat()
            source = hashlib.sha256(f'{path}:{stat.st_size}:{stat.st_mtime_ns}:{fingerprint}'.encode()).hexdigest()
            if payload['tracks'].get(uri, {}).get('fingerprint') == source:
                continue
            features = analyze(path, embedding_model, mood_model)
            payload['tracks'][uri] = {'fingerprint':source, 'audio_features':features}
            atomic_write(args.output, payload)  # Restart-safe, only after actual changes.
            print('Analyzed', uri)
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            failed += 1
            print('Skipped record:', type(exc).__name__)
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
