import pytest
from lib import genre_style, radio_genres

def item(artist, tag='deep house', vec=None, duration=200):
    return dict(uri=artist+'://song',name='Song',artist=artist,genres=[tag],duration=duration,
        audio_features=dict(embedding=vec or [1.,0.,0.,0.,0.,0.,0.,0.],embedding_model='test'))

def test_known_style_distance_and_radio_fallback():
    assert radio_genres.fallback(item('a','deep house'),item('b','house')) < 1.4
    assert radio_genres.fallback(item('a','ambient'),item('b','house')) is None
    assert radio_genres.conflicting(item('a','ambient'),item('b','deep house'))
    assert not radio_genres.conflicting(item('a','dub techno'),item('b','downtempo'))

def test_reject_invalid_evidence_and_do_not_train_on_predictions():
    x=item('a','');x['inferred_genres']=['house'];x['genre_evidence']=dict(source=genre_style.SOURCE,confidence=.99,matched_artists=3,validated=False)
    assert not radio_genres.labels(x)
    x['genre_evidence']['validated']=True
    assert 'house' in radio_genres.labels(x)
    assert not genre_style.explicit(x)

def test_same_artist_versions_and_long_mixes_cannot_supply_votes():
    pytest.importorskip('numpy')
    m=genre_style.Model([item('a'), item('a'),item('b'),item('c')])
    assert m.predict(item('a')) is None
    assert m.predict(item('x',duration=3000)) is None
    assert m.predict(item('x'))['genre_evidence']['matched_artists']==3
    assert not m.evaluate([item('x')])['enabled']  # One class cannot validate a model.

def test_embedding_model_and_nonfinite_values():
    x=item('a');x['audio_features']['embedding'][0]=float('nan')
    assert genre_style.vector(x) is None
    x=item('a');x['audio_features']['embedding_model']=''
    assert genre_style.vector(x) is None

def test_ranking_deduplicates_across_versions_and_categories(tmp_path):
    import library
    model=library.LocalModel(tmp_path/'model.sqlite3')
    candidates=[dict(item('Artist'), uri='library://track/1',name='Same Song',trusted=True),
                dict(item('Artist'), uri='tidal://track/2',name='Same Song (Remaster)',trusted=True),
                dict(item('Other'), uri='tidal://track/3',name='Other Song',trusted=True)]
    picks=model.rank(candidates, {'room':'lounge','hour':12,'weekday':1}, 3)
    assert len(picks)==2
    assert not any(genre_style.mix_policy.same_recording(a,b) for n,a in enumerate(picks) for b in picks[n+1:])
    model.db.close()


def test_numbered_works_are_not_versions_of_the_same_song():
    a=dict(item('Composer'),name='Symphony No 1 in C')
    b=dict(item('Composer'),name='Symphony No 2 in C',uri='other')
    assert not genre_style.mix_policy.same_recording(a,b)
    a=dict(item('Artist'),name='Song (2011 Remaster)')
    b=dict(item('Artist'),name='Song',uri='other')
    assert genre_style.mix_policy.same_recording(a,b)
