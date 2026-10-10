"""Reproducible offline relevance/feedback helpers; never train on holdout data."""


def split_history(rows, cutoff):
    deliberate = [dict(r) for r in rows if dict(r).get('origin') == 'manual']
    return ([r for r in deliberate if r['ts'] < cutoff],
            [r for r in deliberate if r['ts'] >= cutoff])


def labelled_metrics(selected, labels):
    relevant = set(labels.get('relevant') or [])
    irrelevant = set(labels.get('irrelevant') or [])
    if relevant & irrelevant:
        raise ValueError('Contradictory relevance labels')
    uris = [i['uri'] for i in selected]
    judged = [u for u in uris if u in relevant or u in irrelevant]
    hits = sum(u in relevant for u in judged)
    return {'judged_precision': hits/len(judged) if judged else None,
            'judged_coverage': len(judged)/len(uris) if uris else 0.,
            'labelled_recall': len(set(uris)&relevant)/len(relevant) if relevant else None}
