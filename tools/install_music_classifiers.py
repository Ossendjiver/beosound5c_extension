#!/usr/bin/env python3
"""Stage and validate optional public classifier heads on SSD; no playback control.

Weights have Essentia's CC BY-NC-SA licensing; see docs/mood-audio-analysis.md.
Download failures leave existing models untouched. Install while profiler is stopped.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import urllib.request
from analyze_music_audio import atomic_write
from music_classifiers import HEADS, Heads


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models', type=Path, default=Path('/media/local/cache/audio-analysis/models'))
    args = parser.parse_args()
    os.umask(0o077)
    args.models.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.classifier-staging-', dir=args.models) as temporary:
        staging = Path(temporary)
        manifest = {}
        for name in HEADS:
            stem = name+'-msd-musicnn-1'
            base = 'https://essentia.upf.edu/models/classification-heads/'+name+'/'+stem
            for extension in ('json', 'onnx'):
                request = urllib.request.Request(base+'.'+extension, headers={'User-Agent':'BS5cAudioProfiles/2.0'})
                with urllib.request.urlopen(request, timeout=30) as response:
                    if not response.geturl().startswith('https://essentia.upf.edu/'):
                        raise ValueError('Unexpected model redirect')
                    content = response.read(8*1024*1024+1)
                if len(content)>8*1024*1024:
                    raise ValueError('Model exceeds size limit')
                target = staging/(stem+'.'+extension)
                if extension=='json':
                    json.loads(content)
                with target.open('wb') as handle:
                    handle.write(content); handle.flush(); os.fsync(handle.fileno())
                manifest[target.name] = hashlib.sha256(content).hexdigest()
        heads = Heads(staging)
        # Validate the complete set before replacing any existing model.
        for name in manifest:
            os.replace(staging/name, args.models/name)
        atomic_write(args.models/'classifiers-manifest.json', {'version':1, 'files':manifest, 'fingerprint':heads.fingerprint})
    print('Validated classifier heads:', len(heads.sessions))


if __name__ == '__main__':
    main()
