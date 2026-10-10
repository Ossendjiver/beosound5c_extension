"""Portable MusiCNN/emoMusic inference. Optional numpy/onnxruntime only.

Model weights: Essentia CC BY-NC-SA 4.0; see mood-audio-analysis.md.
Frontend follows the published TensorflowInputMusiCNN parameters (16 kHz,
512-point Hann, 256 hop, 96 Slaney mel bands, log10(1+10000*power)).
"""
import hashlib
from pathlib import Path

import numpy as np

from analyze_music_audio import normalize_predictions, rounded_embedding
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'services'))
from lib import profile_quality
from music_classifiers import Heads

RATE = 16000
EMBEDDING = 'msd-musicnn-1'
MODEL = 'emomusic-msd-musicnn-2'


def mel_spectrogram(audio):
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim != 1 or len(audio) < 15*RATE or not np.isfinite(audio).all():
        raise ValueError('Invalid audio sample')
    def mel(f):
        return np.where(f < 1000, f/(200/3), 15+np.log(np.maximum(f, 1)/1000)/(np.log(6.4)/27))
    def hz(m):
        return np.where(m < 15, m*(200/3), 1000*np.exp((m-15)*(np.log(6.4)/27)))
    edges = hz(np.linspace(mel(np.array(0)), mel(np.array(8000)), 98))
    frequency = np.fft.rfftfreq(512, 1/RATE)
    weights = np.array([np.maximum(0, np.minimum(
        (frequency-edges[i])/(edges[i+1]-edges[i]),
        (edges[i+2]-frequency)/(edges[i+2]-edges[i+1])))*2/(edges[i+2]-edges[i]) for i in range(96)])
    # Essentia's centred first frame includes leading zero padding.
    frames = np.lib.stride_tricks.sliding_window_view(np.pad(audio, (256, 256)), 512)[::256]
    spectrum = abs(np.fft.rfft(frames*np.hanning(512), axis=1))**2
    return np.log10(1+10000*(spectrum@weights.T)).astype(np.float32)


def patches(mels):
    # Match explicitly configured reference: stride187, final incomplete patch discarded.
    return np.asarray([mels[start:start+187] for start in range(0, len(mels)-186, 187)], dtype=np.float32)


def tempo(mels):
    """Conservative spectral-flux pulse estimate; no mood inference from BPM."""
    onset = np.maximum(0, np.diff(mels, axis=0)).mean(axis=1)
    onset -= onset.mean()
    if np.linalg.norm(onset) < .01:
        return None
    n = len(onset)
    transform = np.fft.rfft(onset, n=2*n)
    correlation = np.fft.irfft(transform*transform.conjugate())[:n]
    correlation /= np.arange(n, 0, -1)
    low, high = int(60*RATE/256/180), int(60*RATE/256/60)
    lag = low+int(np.argmax(correlation[low:high+1]))
    confidence = float(correlation[lag]/max(correlation[0], 1e-12))
    return (60*RATE/256/lag, confidence) if confidence >= .15 else None


class Analyzer:
    def __init__(self, models):
        import onnxruntime as ort
        models = Path(models)
        paths = [models/(EMBEDDING+'.onnx'), models/(MODEL+'.onnx')]
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self.heads = Heads(models, options=options)
        self.fingerprint = hashlib.sha256(b'bs5c-onnx-frontend-v3-profile-quality'+b''.join(p.read_bytes() for p in paths)+self.heads.fingerprint.encode()).hexdigest()
        self.embedding = ort.InferenceSession(str(paths[0]), options, providers=['CPUExecutionProvider'])
        self.mood = ort.InferenceSession(str(paths[1]), options, providers=['CPUExecutionProvider'])

    def analyze(self, samples):
        tempos, tempo_confidences, levels, centroids, windows_per_section = [], [], [], [], []
        seconds = 0
        for audio in samples:
            audio = np.asarray(audio, dtype=np.float32)
            mels = mel_spectrogram(audio)
            if float(np.sqrt(np.mean(audio*audio))) < 1e-5:
                continue  # Silence has no defensible emotional classification.
            windows = patches(mels)
            # Four evenly spaced 3-second patches per section. Tempo and spectral
            # measures still use the entire sample; inference stays Pi-friendly.
            if len(windows) > 4:
                windows = windows[np.linspace(0, len(windows)-1, 4, dtype=int)]
            windows_per_section.append(windows)
            pulse = tempo(mels)
            if pulse:
                tempos.append(pulse[0])
                tempo_confidences.append(pulse[1])
            seconds += len(audio)/RATE
            levels.append(float(np.sqrt(np.mean(audio*audio))))
            frames = audio[:len(audio)//1024*1024].reshape(-1, 1024)
            spectrum = abs(np.fft.rfft(frames*np.hanning(1024), axis=1))
            mass = spectrum.sum(axis=1)
            valid = mass > 1e-8
            if valid.any():
                centroids.append(float(np.mean((spectrum[valid]*np.fft.rfftfreq(1024, 1/RATE)).sum(axis=1)/mass[valid])))
        if not windows_per_section:
            raise ValueError('No usable audio sections')
        vectors = self.embedding.run(['embeddings'], {'melspectrogram': np.concatenate(windows_per_section)})[0]
        predictions = self.mood.run(['activations'], {'embeddings': vectors})[0]
        result = normalize_predictions(predictions)
        result.update(profile_quality.summary([normalize_predictions([p]) for p in predictions],
            len(windows_per_section), seconds, tempo_confidences))
        classifiers = self.heads.predict(vectors)
        if classifiers:
            result.update(classifiers=classifiers, classifier_fingerprint=self.heads.fingerprint)
        result.update(model=MODEL, embedding_model=EMBEDDING,
                      embedding=rounded_embedding(vectors),
                      rms=float(np.mean(levels)), sample_seconds=seconds,
                      analysis='onnx-multi-section-four-patches-v3', inference_patches=len(predictions))
        if tempos:
            result.update(bpm=float(np.median(tempos)), bpm_method='spectral-flux-autocorrelation')
        if centroids:
            result['spectral_centroid_hz'] = float(np.mean(centroids))
        return result
