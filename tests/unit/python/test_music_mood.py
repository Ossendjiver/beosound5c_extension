import math
import time
import pytest
import library as lib
from lib import music_mood as mood


def test_wheel_axes_rings_and_invalid_coordinates():
    assert mood.selection(0, 0)['valence'] == 1
    assert mood.selection(90, .5)['energy'] == 1
    assert mood.selection(180, 1)['valence'] == 0
    assert mood.selection(270, .5)['energy'] == 0
    assert mood.selection(-90, 1)['angle'] == 270
    assert [mood.selection(0,r)['discovery_fraction'] for r in [0,.5,1]] == [0,.37,.9]
    for a,r in [(math.nan,.5),(0,math.inf),(0,-1),(0,2)]:
        with pytest.raises(ValueError): mood.selection(a,r)


def test_profiles_use_tags_or_explicit_annotation_not_title_guessing():
    assert mood.profile({'name':'Happy song','artist':'Metal band'}, {}) is None
    assert mood.profile({'genre':'ambient'}, {})['energy'] == .1
    assert mood.profile({'uri':'x','genre':'dance'}, {'x':{'energy':.2,'valence':.8}})['energy'] == .2


def test_mood_changes_order_but_keeps_trusted_baseline(tmp_path):
    m = lib.LocalModel(tmp_path/'db')
    songs = [{'uri':'calm','name':'Calm','artist':'Known','favorite':True,'genre':'ambient'},
             {'uri':'dance','name':'Dance','artist':'Known','favorite':True,'genre':'dance'},
             {'uri':'random','name':'Random','artist':'Other','genre':'metal'}]
    picks = m.rank(songs, {'mood':mood.selection(270,0)}, 20)
    assert picks[0]['uri'] == 'calm'
    assert len(picks) == 1  # Familiarity cannot keep the wrong mood eligible.
    assert m.rank(songs, {'mood':mood.selection(90,0)}, 20)[0]['uri'] == 'dance'
    assert m.rank([songs[-1]], {'mood':mood.selection(90,1)},20) == []


def test_continuous_discovery_honours_requested_fraction(tmp_path):
    m = lib.LocalModel(tmp_path/'db')
    familiar = [{'uri':f'f{i}','name':f'Known{i}','artist':'Known','trusted':True,'genre':'dance'} for i in range(20)]
    discovery = [{'uri':f'd{i}','name':f'New{i}','artist':'Other','genre':'dance'} for i in range(20)]
    for radius, maximum in [(0,0),(.5,7),(1,18)]:
        picks = m.rank(familiar+discovery, {'mood':mood.selection(90,radius)},20)
        assert sum(i['uri'].startswith('d') for i in picks) <= maximum
        if radius == 1: assert sum(i['uri'].startswith('d') for i in picks) == 18
    assert all(i['uri'].startswith('f') for i in m.rank(familiar+discovery, {},20))


def test_patternplay_and_wheel_share_history(mock_config, tmp_path, monkeypatch):
    mock_config({})
    monkeypatch.setattr(lib,'DB_PATH',tmp_path/'db')
    service = lib.LibraryService()
    for i in range(5):
        service.model.record_listen({'title':f'Chosen{i}','artist':'Known','uri':f'uri{i}',
            'selection_origin':'manual','listening_mood':mood.selection(270,.5)},150,service.context)
    suggested = service.pattern_mood('lounge')
    assert suggested['angle'] == pytest.approx(270)
    assert suggested['discovery_fraction'] == .1
    service._manual_choices[('known','chosen')] = time.time()
    assert service._learning_item({'name':'Chosen','artist':'Known'})['selection_origin'] == 'manual'
    service._manual_choices[('known','chosen')] -= 1801
    assert service._learning_item({'name':'Chosen','artist':'Known'})['selection_origin'] == 'unknown'


def test_mood_eligibility_validates_coordinates_without_scanning_embedding():
    class UnneededEmbedding(list):
        def __iter__(self):
            raise AssertionError('Mood eligibility should not scan acoustic vectors')
    assert mood.profile({'audio_features': {'model': 'm', 'energy': .2, 'valence': .4,
        'embedding': UnneededEmbedding([1]*200)}}, {}) == {
        'energy': .2, 'valence': .4, 'source': 'audio_model'}
    for bad in [True, float('nan'), -1, 2]:
        assert mood.profile({'audio_features': {'model': 'm', 'energy': bad, 'valence': .4}}, {}) is None
