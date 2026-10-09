from lib.mix_policy import similar_length, distinct

def item(name='Song', artist='Artist', duration=240):
    return dict(name=name, artist=artist, duration=duration)

def test_long_mixes_never_enter_song_mix_or_the_reverse():
    assert not similar_length(item(duration=2700), item())
    assert not similar_length(item(), item(duration=2700))
    assert similar_length(item(duration=3000), item(duration=2700))
    assert not similar_length(item(duration=8200), item(duration=2700))

def test_song_lengths_match_seed_and_unknown_lengths_are_not_guessed():
    assert similar_length(item(duration=200), item())
    assert not similar_length(item(duration=900), item())
    assert not similar_length(item(duration=0), item())

def test_versions_same_artist_deduplicate_but_other_artist_is_allowed():
    seed=item('Borderline')
    assert distinct([item('Borderline (Remastered)'), item('Borderline - Live'), item('Borderline', 'Other'), item('Next song')], [seed]) == [item('Borderline', 'Other'), item('Next song')]
    assert len(distinct([item('The beautiful song'),item('The beautiful songs')], []))==1


def test_old_library_cache_refreshes_duration_metadata_once_available():
    from sources.mass.service import MassSource
    source=object.__new__(MassSource)
    source._library_node_by_uri={'one':{'media_type':'track'},'two':{'media_type':'radio'}}
    assert source._cached_music_needs_duration_sync()
    source._library_node_by_uri['one']['duration']=240
    assert not source._cached_music_needs_duration_sync()


def test_root_duration_boundaries_are_inclusive_and_unknown_root_rejected():
    root=item(duration=300)
    assert similar_length(item(duration=100),root)
    assert similar_length(item(duration=900),root)
    assert not similar_length(item(duration=99),root)
    assert not similar_length(item(duration=901),root)
    assert not similar_length(item(),item(duration=0))
    assert not similar_length(item(duration=float('nan')),root)
    assert not similar_length(item(duration=float('inf')),root)


def test_provider_upload_versions_are_excluded_from_same_session():
    from lib.mix_policy import same_recording
    assert same_recording(item('Borderline','Sufjan Stevens'),item('Sufjan Stevens - Borderline','Culturcide'))
