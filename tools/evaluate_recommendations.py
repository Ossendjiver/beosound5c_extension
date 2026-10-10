#!/usr/bin/env python3
"""Offline saved-library evaluation. No MA/network/playback calls.

Snapshots stay local. Structural metrics do not establish musical relevance.
The optional history snapshot is the library service's export, never an MA DB.
"""
import argparse
import asyncio
import collections
import json
import os
from pathlib import Path
import sys
import tempfile
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'services'))
import library
from lib import mix_policy, music_mood, sequence_plan, recommendation_evaluation
from lib.session_feedback import SessionFeedback


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--features', type=Path)
    parser.add_argument('--history-snapshot', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seeds', type=int, default=8)
    parser.add_argument('--labels', type=Path, help='JSON seed URI -> relevant/irrelevant URI arrays')
    parser.add_argument('--history-cutoff', type=float, help='Only train on manual events before this Unix timestamp')
    args=parser.parse_args()
    labels=json.loads(args.labels.read_text()) if args.labels else {}
    withheld=0
    if not 1<=args.seeds<=20:raise ValueError('Seeds must be 1..20')
    with tempfile.TemporaryDirectory(prefix='recommendation-offline-') as scratch:
        model=library.LocalModel(Path(scratch)/'db')
        if args.history_snapshot:
            snapshot=json.loads(args.history_snapshot.read_text())
            for key,value in snapshot.get('kv',{}).items():model.put_kv(key,value)
            columns={r[1] for r in model.db.execute('PRAGMA table_info(listens)')}
            rows=snapshot.get('history',[])
            if args.history_cutoff is not None:
                rows, future=recommendation_evaluation.split_history(rows,args.history_cutoff)
                withheld=len(future)
                for key in ('music_familiarity','music_feedback','mood_calibration_v1'):
                    model.put_kv(key,{})  # Untimestamped priors cannot enter temporal evaluation.
            for row in rows:
                keys=[k for k in row if k in columns and k!='id']
                if keys:model.db.execute('INSERT INTO listens('+','.join(keys)+') VALUES ('+','.join('?' for _ in keys)+')',[row[k] for k in keys])
            model.db.commit()
        library.CACHE_LIBRARY=args.library
        if args.features:os.environ['BS5C_AUDIO_FEATURES_FILE']=str(args.features)
        service=object.__new__(library.LibraryService)
        service.cfg={};service._seed_metadata=model.get_kv('favorite_tracks',{});service.model=model
        service._automatic_keys=set();service._context_for_room=lambda room:{'room':room,'hour':12,'weekday':2}
        service.session_feedback=SessionFeedback(lambda:{},lambda data:None)
        service.mixes=type('Sessions',(),{'sessions':{}})()
        items=service._load_library()
        profiles=[i for i in items if i.get('audio_features') and i.get('duration')]
        profiles.sort(key=lambda i:(mix_policy.identity(i)[0],i['uri']))
        roots=[profiles[int(i*len(profiles)/min(args.seeds,len(profiles)))] for i in range(min(args.seeds,len(profiles)))]
        cases=[]
        for root in roots:
            for mode in ['radio','mood']:
                for angle in ([None] if mode=='radio' else [0,90,180,270]):
                    mood=None if angle is None else music_mood.selection(angle,.625)
                    start=time.perf_counter()
                    picks=asyncio.run(service._mix_recommend('offline',20,mood,root,{
                        'mode':mode,'exclude':[root['uri']],'previous':[root]}))
                    duration_bad=sum(not mix_policy.similar_length(i,root) for i in picks)
                    duplicates=sum(mix_policy.same_recording(a,b) for n,a in enumerate(picks) for b in picks[n+1:])
                    artists=[mix_policy.identity(i)[0] for i in picks]
                    repeat_adjacent=sum(a==b for a,b in zip(artists,artists[1:]))
                    metrics=recommendation_evaluation.labelled_metrics(picks,labels[root['uri']]) if root['uri'] in labels else {}
                    cases.append({**metrics,'mode':mode,'angle':angle,'root_duration':root['duration'],
                        'count':len(picks),'duration_violations':duration_bad,'recording_duplicates':duplicates,
                        'adjacent_artist_repeats':repeat_adjacent,
                        'largest_artist_share':max(collections.Counter(artists).values(),default=0)/max(1,len(picks)),
                        'mood_violations':sum(not music_mood.compatible(i,mood,{}) for i in picks) if mood else 0,
                        'mean_transition_cost':sum(sequence_plan.transition_cost(a,b) for a,b in zip([root]+picks,picks))/max(1,len(picks)),
                        'elapsed_ms':round((time.perf_counter()-start)*1000,2)})
        report={'catalogue_entries':len(items),'profiled_entries':len(profiles),'seeds':len(roots),
            'cases':cases,'withheld_manual_events':withheld,'scope':'Offline structural validation; no relevance labels or audible playback',
            'violations':sum(c['duration_violations']+c['recording_duplicates']+c['mood_violations'] for c in cases)}
        args.output.write_text(json.dumps(report,indent=2))
        model.db.close()
        print(json.dumps({k:v for k,v in report.items() if k!='cases'}))
        if report['violations']:return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
