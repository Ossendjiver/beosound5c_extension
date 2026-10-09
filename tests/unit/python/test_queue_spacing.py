from lib import queue_spacing as q

def item(artist):return {'artist':artist,'name':'Song'}
def pool(*artists):return [(0,item(a)) for a in artists]

def test_discovery_spread_and_exact_quota():
    assert q.slots(10,10)==['familiar','discovery']*10
    assert [i for i,v in enumerate(q.slots(18,2)) if v=='discovery']==[9,19]
    assert q.slots(0,0)==[]
    assert q.slots(0,3)==['discovery']*3

def test_three_intervening_tracks_preferred():
    assert [q.artist(i) for _,i in q.spaced(pool('A','B','C','D'),['a','b','c'])]==['d']

def test_shortage_relaxes_oldest_first_without_dropping_tracks():
    assert [q.artist(i) for _,i in q.spaced(pool('A','B','C'),['a','b','c'])]==['a']
    assert q.spaced(pool('A'),['a'])==pool('A')

def test_artist_matching_uses_normalized_identity():
    assert q.spaced(pool('HOT CHIP','Robyn'),['hot chip'])==pool('Robyn')

def test_rank_spaces_seed_artist_across_refill_boundary(tmp_path):
    import library
    model=library.LocalModel(tmp_path/'db')
    tracks=[dict(artist=a,name=f'Song {n}',uri=f'track://{n}',media_type='track',duration=200,favorite=True)
            for n,a in enumerate(['Hot Chip']*8+['Robyn','Grace Jones','Daft Punk','Jenny Wilson']*3)]
    picks=model.rank(tracks,{'seed_artist':'hot chip','relation_seed':item('Hot Chip'),
                            'queue_previous':[item('Hot Chip')]},12)
    artists=['hot chip']+[q.artist(i) for i in picks]
    for n,a in enumerate(artists[1:],1):
        assert a not in artists[max(0,n-3):n]
    model.db.close()
