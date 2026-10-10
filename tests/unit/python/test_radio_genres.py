from lib import radio_genres,music_features


def test_pop_symphony_title_does_not_create_classical_family():
    item={'name':'Symphony','genres':['Dance Pop'],'artist':'Clean Bandit'}
    assert 'classical' not in radio_genres.labels(item)


def test_repertoire_detection_requires_work_evidence():
    assert 'classical' not in radio_genres.labels({'name':'Sonata'})
    assert 'classical' in radio_genres.labels({'name':'Symphony No. 5 in C minor'})
    assert 'classical' in radio_genres.labels({'name':'Sonata for Cello and Piano'})


def test_rnb_subgenres_share_family():
    assert radio_genres.fallback({'genres':['Alternative R&B']},{'genres':['Contemporary R&B']})==1.4


def test_specific_metadata_genre_precedes_general_family():
    root={'metadata':{'genres':['Deep House']}}
    assert radio_genres.fallback({'genres':'Deep-House; Electronic'},root)==1.1
    assert radio_genres.fallback({'genres':['Techno']},root)==1.4


def test_pop_album_classical_tag_does_not_admit_crossover():
    assert music_features.radio_distance({'name':'Crossover pop','genres':['Pop','Classical']},{'name':'BWV 974'}) is None

def test_generic_experimental_tags_cannot_link_hiphop_to_electronic():
    assert radio_genres.fallback({'genres':['Experimental','Electronic']},{'genres':['Experimental','Hip Hop']}) is None

def test_jazz_album_tag_cannot_admit_pop_or_hiphop_to_pure_jazz_radio():
    root={'genres':['Jazz']}
    for tags in (['Hip Hop','Trap','Jazz'],['Pop','Jazz','Electronic']):
        assert radio_genres.conflicting({'genres':tags},root)

def test_strong_neo_soul_tags_anchor_broadly_tagged_seed():
    root={'genres':['Neo Soul','R&B','Contemporary R&B','Hip Hop','Jazz']}
    assert radio_genres.conflicting({'genres':['Trap','Rap','Contemporary R&B']},root)
    assert radio_genres.fallback({'genres':['Neo Soul','R&B']},root)<1.4

def test_artist_arrays_are_not_lost_in_ranking_identity():
    import library
    track={'name':'Song','artists':[{'name':'First Artist'},{'name':'Second Artist'}]}
    assert library.LocalModel._candidate_key(track)==('first artist, second artist','song')
    assert music_features.artist_name({'artist':{'name':'First'}})=='First'

def test_rap_substyles_prevent_album_soul_tags_overriding_hiphop_seed():
    seed={'genres':['Hip Hop','Hip-Hop/Rap','Trap','Jazzy Hip-Hop','Conscious','Neo Soul','Contemporary R&B','Jazz']}
    assert radio_genres.dominant(radio_genres.labels(seed))=={'hip hop'}
    assert radio_genres.conflicting({'genres':['Neo Soul','R&B']},seed)
