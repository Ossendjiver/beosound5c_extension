"""Optional, validated Essentia MSD-MusiCNN classifier heads."""
import hashlib
import json
from pathlib import Path

HEADS = ('mood_relaxed', 'mood_aggressive', 'mood_acoustic', 'mood_electronic', 'voice_instrumental', 'danceability')
LABELS = {'relaxed', 'aggressive', 'acoustic', 'electronic', 'voice', 'instrumental', 'danceability'}


class Heads:
    def __init__(self, models, runtime=None, options=None):
        if runtime is None:
            import onnxruntime as runtime
        models = Path(models)
        self.sessions = []
        digest = hashlib.sha256()
        for name in HEADS:
            path = models/(name+'-msd-musicnn-1.onnx')
            metadata = path.with_suffix('.json')
            if not path.exists() and not metadata.exists():
                continue
            info = json.loads(metadata.read_text())
            if info.get('inference', {}).get('embedding_model', {}).get('model_name') != 'msd-musicnn-1':
                raise ValueError('Classifier embedding family mismatch: '+name)
            labels = info.get('classes')
            if isinstance(labels, list):
                labels = ['danceability' if label == 'danceable' else label for label in labels]
            if not isinstance(labels, list) or len(labels) != 2 or not any(k in LABELS for k in labels):
                raise ValueError('Invalid classifier labels: '+name)
            session = runtime.InferenceSession(str(path), options, providers=['CPUExecutionProvider'])
            inputs = session.get_inputs()
            outputs = [o for o in session.get_outputs() if len(o.shape)==2 and o.shape[-1]==len(labels)]
            if len(inputs)!=1 or len(inputs[0].shape)!=2 or inputs[0].shape[-1]!=200 or len(outputs)!=1:
                raise ValueError('Classifier tensor schema mismatch: '+name)
            self.sessions.append((session, inputs[0].name, outputs[0].name, labels))
            digest.update(path.read_bytes()); digest.update(metadata.read_bytes())
        self.fingerprint = digest.hexdigest() if self.sessions else ''

    def predict(self, embeddings):
        import numpy as np
        result = {}
        for session, input_name, output_name, labels in self.sessions:
            values = np.asarray(session.run([output_name], {input_name: embeddings})[0])
            if values.shape != (len(embeddings), len(labels)) or not np.isfinite(values).all() or (values<0).any() or (values>1).any() or not np.allclose(values.sum(axis=1), 1., atol=.02):
                raise ValueError('Invalid classifier predictions')
            for index, label in enumerate(labels):
                if label in LABELS:
                    result[label] = round(float(values[:, index].mean()), 5)
        return result
