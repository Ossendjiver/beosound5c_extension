"""Compact, bounded decision traces stored in one transaction per ranking run."""
import collections
import json
import time

VERSION = 'hybrid-v2'


class Trace:
    def __init__(self, context, count):
        self.started = time.perf_counter()
        self.reasons = collections.Counter()
        self.examples = {}
        self.data = {'version': VERSION, 'ts': time.time(), 'room': context.get('room', ''),
                     'queue_id': context.get('queue_id', ''), 'mode': 'radio' if context.get('radio') else 'mood' if context.get('mood') else 'pattern',
                     'seed_uri': (context.get('seed_features') or {}).get('uri'), 'input_count': count,
                     'mood': context.get('mood'), 'selected': []}

    def reject(self, reason, item):
        self.reasons[reason] += 1
        examples = self.examples.setdefault(reason, [])
        if len(examples) < 3:
            examples.append(str(item.get('uri') or '')[:512])

    def finish(self, selected, details):
        self.data.update(rejected=dict(self.reasons), exclusion_examples=self.examples,
                         elapsed_ms=round((time.perf_counter()-self.started)*1000, 2),
                         selected=[dict(uri=i['uri'], **details.get(i['uri'], {})) for i in selected])
        return self.data


def store(db, trace):
    encoded = json.dumps(trace, separators=(',', ':'), allow_nan=False)
    if len(encoded.encode()) > 65536:
        return
    with db:
        db.execute('INSERT INTO recommendation_runs(ts,queue_id,payload) VALUES(?,?,?)',
                   (trace['ts'], trace.get('queue_id', ''), encoded))
        db.execute('DELETE FROM recommendation_runs WHERE ts<? OR id NOT IN (SELECT id FROM recommendation_runs ORDER BY id DESC LIMIT 100)',
                   (time.time()-7*86400,))
