#!/usr/bin/env python3
"""Rebuild conservative style evidence from existing profiles; no sampling/network/playback."""
import argparse, json, os, sys, tempfile, time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services'))
import library
from lib import genre_style
from lib.session_feedback import SessionFeedback
from analyze_music_audio import atomic_write


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--library',type=Path,default=library.CACHE_LIBRARY)
    p.add_argument('--features',type=Path)
    p.add_argument('--output',type=Path,default=Path('/media/local/cache/genre_style_profiles.json'))
    args=p.parse_args()
    library.CACHE_LIBRARY=args.library
    if args.features: os.environ['BS5C_AUDIO_FEATURES_FILE']=str(args.features)
    os.environ['BS5C_GENRE_STYLE_FILE']='/nonexistent/training-does-not-learn-own-predictions'
    with tempfile.TemporaryDirectory(dir=args.output.parent,prefix='genre-style-') as scratch:
        model=library.LocalModel(Path(scratch)/'db')
        service=object.__new__(library.LibraryService);service.cfg={};service._seed_metadata={};service.model=model
        items=service._load_library()
        learner=genre_style.Model(items)
        report=learner.evaluate(items)
        tracks={}
        if report['enabled']:
            for item in items:
                # Explicit specific styles always win; only supplement missing detail.
                if genre_style.radio_genres.specific(genre_style.explicit(item)): continue
                prediction=learner.predict(item)
                if prediction:
                    prediction['inferred_genres']=[tag for tag in prediction['inferred_genres'] if tag in report['validated_labels']]
                    if not prediction['inferred_genres']: continue
                    known=genre_style.radio_genres.dominant(genre_style.explicit(item))
                    inferred=genre_style.radio_genres.dominant(set(prediction['inferred_genres']))
                    if known and inferred and not known & inferred: continue
                    prediction['genre_evidence']['validated']=True
                    tracks[item['uri']]=prediction
        atomic_write(args.output,{'version':1,'updated':time.time(),'report':report,'tracks':tracks})
        model.db.close()
    print(json.dumps(dict(report,annotated_entries=len(tracks))))

if __name__=='__main__':main()
