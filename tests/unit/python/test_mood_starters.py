"""Prepared pools speed requests without becoming a second recommendation model."""
import asyncio
import json
import time
from unittest.mock import Mock
import pytest
import library
from lib import music_mood, mix_policy
from lib.mood_starters import MoodStarters, SECTORS, ACTIVE, REFRESH_SECONDS


def song(n, mood=None, duration=240, discovery=False):
    mood = mood or music_mood.selection(0, 0)
    return {'uri': f'library://track/{n}', 'name': f'Composition {n}', 'artist': f'Performer {n}',
            'duration': duration, 'trusted': not discovery, 'favorite': not discovery,
            '_selection_category': 'discovery' if discovery else 'familiar',
            'audio_features': {'model': 'test', 'energy': mood['energy'], 'valence': mood['valence'],
                               'profile_confidence': .95, 'bpm': 100}}


def service(tmp_path, monkeypatch, songs):
    monkeypatch.setattr(library, 'DB_PATH', tmp_path/'library.db')
    result = library.LibraryService()
    result.starters.bind(songs, 'revision')
    result.starters.refresh('song:0', songs)
    result._load_library = Mock(side_effect=AssertionError('Warm mood request read the full library'))
    return result


def test_sixteen_five_track_sectors_and_compact_restart_checkpoint():
    saved = []
    now = [1000]
    cache = MoodStarters(save=saved.append, clock=lambda: now[0])
    songs = [song(f'{sector}-{n}', music_mood.selection(sector*360/SECTORS, 0))
             for sector in range(SECTORS) for n in range(8)]
    cache.bind(songs, 'one')
    for sector in range(SECTORS):
        cache.refresh(f'song:{sector}', songs[sector*8:(sector+1)*8])
    assert len(cache.bands) == 16
    assert all(len(b['active']) == ACTIVE for b in cache.bands.values())
    cache.persist()
    assert not saved  # no per-selection writes
    now[0] += 61
    cache.persist()
    assert len(saved) == 1
    assert 'audio_features' not in json.dumps(saved[0])
    restored = MoodStarters(load=lambda: saved[0], clock=lambda: now[0])
    restored.bind(songs, 'one')
    assert restored.candidates(music_mood.selection(0, 0))
    assert restored.bands == cache.bands


def test_backend_adds_fresh_options_beyond_original_reserve():
    cache = MoodStarters(clock=lambda: 1000)
    songs = [song(i) for i in range(70)]
    cache.bind(songs, 'one')
    cache.refresh('song:0', songs)
    old = set(cache.bands['song:0']['reserve'])
    for offset in range(0, 20, 5):
        cache.reserve(music_mood.selection(0, 0), songs[offset:offset+5])
    cache.refresh('song:0', songs)
    assert set(cache.bands['song:0']['reserve']) - old
    assert all(u not in old for u in cache.bands['song:0']['active'])


def test_deleted_tracks_and_profile_changes_are_hydrated_without_old_features():
    cache = MoodStarters()
    songs = [song(i) for i in range(6)]
    cache.bind(songs, 'one'); cache.refresh('song:0', songs)
    changed = dict(songs[1], audio_features={'model': 'new', 'energy': .2, 'valence': .1})
    cache.bind([changed], 'two')
    picks = cache.candidates(music_mood.selection(0, 0))
    assert [i['uri'] for i in picks] == [changed['uri']]
    assert picks[0]['audio_features']['model'] == 'new'
    assert cache.due() is not None


def test_background_refresh_schedule_and_request_priority():
    now = [1000]
    cache = MoodStarters(clock=lambda: now[0])
    cache.bind([], 'revision')
    for key in cache.keys(): cache.refresh(key, [])
    assert cache.due() is None
    now[0] += REFRESH_SECONDS
    assert cache.due() is not None
    cache.reserve(music_mood.selection(270, 0), [song(1)])
    assert cache.due() == 'song:12'


def test_familiar_and_discovery_reserves_cannot_crowd_each_other_out():
    cache = MoodStarters()
    songs = [song(i) for i in range(50)] + [song(f'd{i}', discovery=True) for i in range(50)]
    cache.bind(songs, 'revision'); cache.refresh('song:0', songs)
    candidates = cache.candidates(music_mood.selection(0, 1))
    assert sum(i['trusted'] for i in candidates) == 20
    assert sum(not i['trusted'] for i in candidates) == 20


@pytest.mark.asyncio
async def test_consecutive_warm_starts_rotate_without_library_load(tmp_path, monkeypatch):
    songs = [song(i) for i in range(30)]
    app = service(tmp_path, monkeypatch, songs)
    try:
        first = await app.recommend_music('bedroom', 5, music_mood.selection(0, 0))
        second = await app.recommend_music('bedroom', 5, music_mood.selection(0, 0))
        assert len(first) == len(second) == 5
        assert not {i['uri'] for i in first} & {i['uri'] for i in second}
        assert app.model.last_trace['starter_cache']['used']
        app._load_library.assert_not_called()
        assert not app.model._history()  # reservations do not fabricate learning
    finally: app.model.db.close()


@pytest.mark.asyncio
async def test_small_pool_changes_opening_even_after_all_five_were_reserved(tmp_path, monkeypatch):
    app = service(tmp_path, monkeypatch, [song(i) for i in range(5)])
    try:
        a = await app.recommend_music('bedroom', 5, music_mood.selection(0, 0))
        b = await app.recommend_music('bedroom', 5, music_mood.selection(0, 0))
        assert a and b and a[0]['uri'] != b[0]['uri']
    finally: app.model.db.close()


@pytest.mark.asyncio
async def test_live_skips_dislikes_and_mood_changes_override_cached_selection(tmp_path, monkeypatch):
    songs = [song(i) for i in range(8)]
    app = service(tmp_path, monkeypatch, songs)
    try:
        app.model.put_kv('music_feedback', {songs[0]['uri']: 'dislike'})
        app.model.record_listen(dict(songs[1], selection_origin='manual', end_reason='skip'), 10, {'room':'bedroom'})
        picks = await app.recommend_music('bedroom', 5, music_mood.selection(0, 0))
        assert picks and not {songs[0]['uri'], songs[1]['uri']} & {i['uri'] for i in picks}
        app.starters.bind([dict(i, audio_features={'model':'changed','energy':.95,'valence':.05}) for i in songs], 'changed')
        assert await app.recommend_music('bedroom', 5, music_mood.selection(0, 0)) == []
    finally: app.model.db.close()


@pytest.mark.asyncio
async def test_discovery_radius_stays_live_with_same_cached_sector(tmp_path, monkeypatch):
    songs = [song(i) for i in range(30)] + [song(f'd{i}', discovery=True) for i in range(30)]
    app = service(tmp_path, monkeypatch, songs)
    try:
        low = await app.recommend_music('bedroom', 10, music_mood.selection(0, 0))
        high = await app.recommend_music('bedroom', 10, music_mood.selection(0, 1))
        assert low and all(i['_selection_category'] == 'familiar' for i in low)
        assert high and sum(i['_selection_category'] == 'discovery' for i in high) >= 8
    finally: app.model.db.close()


@pytest.mark.asyncio
async def test_seed_length_and_versions_still_filter_cached_candidates(tmp_path, monkeypatch):
    songs = [song(i, duration=duration) for i, duration in enumerate([60, 180, 240, 1200, 3600])]
    app = service(tmp_path, monkeypatch, songs)
    app.starters.refresh('mix:0', songs)
    try:
        seed = song('seed', duration=240)
        picks = await app._mix_recommend('bedroom', 10, music_mood.selection(0, 0), seed,
            {'exclude':[seed['uri']], 'mode':'mood', 'previous':[songs[1]]})
        assert picks and all(mix_policy.similar_length(i, seed) for i in picks)
        assert songs[1]['uri'] not in [i['uri'] for i in picks]
        seed = song('long-seed', duration=1800)
        picks = await app._mix_recommend('bedroom', 10, music_mood.selection(0, 0), seed,
            {'exclude':[seed['uri']], 'mode':'mood'})
        assert picks and all(mix_policy.similar_length(i, seed) for i in picks)
    finally: app.model.db.close()


@pytest.mark.asyncio
async def test_rank_failure_keeps_previous_sector_and_retries_in_background(tmp_path, monkeypatch):
    app = service(tmp_path, monkeypatch, [song(i) for i in range(8)])
    before = dict(app.starters.bands)
    app._rank_starter_background = Mock(side_effect=RuntimeError('failed learning update'))
    app.starters.urgent.add('song:0')
    app._load_library = Mock(return_value=app.starters.catalogue)
    slept = []
    async def sleep(seconds):
        slept.append(seconds)
        raise asyncio.CancelledError
    monkeypatch.setattr(library.asyncio, 'sleep', sleep)
    try:
        with pytest.raises(asyncio.CancelledError): await app._starter_loop()
        assert app.starters.bands == before
        assert app.starters.last_error == 'RuntimeError'
        assert slept == [30]
    finally: app.model.db.close()


def test_background_ranker_uses_read_only_connection_and_preserves_live_trace(tmp_path, monkeypatch):
    songs = [song(i) for i in range(8)]
    app = service(tmp_path, monkeypatch, songs)
    app.model.last_trace = {'live': 'kept'}
    database = app.model.db.execute('PRAGMA database_list').fetchone()[2]
    context = {'mood': music_mood.selection(0, .72), 'offline_priority_only': True, 'intent_catalogue': songs}
    try:
        picks = app._rank_starter_background(database, songs, context, app.model.playlist_relations)
        assert picks and app.model.last_trace == {'live': 'kept'}
        original = library.LocalModel.rank
        def forbidden_write(model, *args):
            model.db.execute("INSERT INTO kv(key,value) VALUES('forbidden','true')")
        monkeypatch.setattr(library.LocalModel, 'rank', forbidden_write)
        import sqlite3
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            app._rank_starter_background(database, songs, context, app.model.playlist_relations)
    finally: app.model.db.close()


@pytest.mark.asyncio
async def test_warm_steering_replaces_tail_not_current_song_or_position(tmp_path, monkeypatch):
    import copy
    from lib.mood_mix import MoodMixes
    moods = [music_mood.selection(a, 0) for a in (0, 90, 180, 270)]
    songs = [song(f'{a}-{n}', mood) for a, mood in enumerate(moods) for n in range(12)]
    app = service(tmp_path, monkeypatch, songs)
    for n in range(4): app.starters.refresh(f'song:{n*4}', songs[n*12:(n+1)*12])
    root = songs[0]
    state = {'state':'playing', 'current_index':0, 'elapsed_time':47,
             'current_item':{'queue_item_id':'root-id','duration':240,'media_item':root},
             'items':[{'queue_item_id':'root-id','duration':240,'media_item':root}]}
    calls=[]
    async def command(cmd, **payload):
        calls.append(cmd)
        if cmd=='mood_snapshot': return {'state':'ok','snapshot':copy.deepcopy(state)}
        assert cmd=='mood_replace_upcoming'
        assert payload['expected_item_id']=='root-id'
        state['items']=state['items'][:1]+[{'queue_item_id':u,'media_item':app.starters.index[u]} for u in payload['media']]
        return {'state':'queued'}
    mixes = MoodMixes(command, app._mix_recommend)
    app.mixes = mixes
    try:
        await mixes.begin('offline', 'bedroom', root)
        for angle in (0, 90, 180):
            await mixes.update('offline', angle, 0)
            assert state['elapsed_time']==47 and state['current_item']['queue_item_id']=='root-id'
            assert len(state['items'])>1
            assert all(mix_policy.similar_length(i['media_item'],root) for i in state['items'][1:])
        assert set(calls)=={'mood_snapshot','mood_replace_upcoming'}
        app._load_library.assert_not_called()
    finally: app.model.db.close()


def test_duration_diversity_survives_high_scoring_normal_length_favourites():
    cache=MoodStarters()
    tracks=[song(i) for i in range(40)]+[song('tiny',duration=30),song('extended',duration=1000)]
    cache.bind(tracks,'revision');cache.refresh('song:0',tracks)
    durations={i['duration'] for i in cache.candidates(music_mood.selection(0,0))}
    assert {30,240,1000} <= durations


@pytest.mark.asyncio
async def test_exhausted_session_requests_new_backend_candidates_and_releases_retry(tmp_path, monkeypatch):
    songs=[song(i) for i in range(60)]
    app=service(tmp_path,monkeypatch,songs)
    mood=music_mood.selection(0,0)
    app.starters.bands['song:0']['updated']=0
    app.starters.urgent={'song:0'}
    blocked=songs[:20]
    root=song('root')
    app.mixes.sessions['q']={'pending_refresh':True,'mood':mood,'seed':root,'retry_after':time.time()+900,'next_refill':time.time()+900}
    try:
        assert await app._mix_recommend('bedroom',20,mood,root,{'mode':'mood','queue_id':'q',
            'exclude':[i['uri'] for i in blocked]+[root['uri']],'previous':blocked}) == []
        assert 'song:0' in app.starters.requests
        app._load_library=Mock(return_value=songs)
        async def stop_after_step(seconds):raise asyncio.CancelledError
        monkeypatch.setattr(library.asyncio,'sleep',stop_after_step)
        with pytest.raises(asyncio.CancelledError):await app._starter_loop()
        assert 'retry_after' not in app.mixes.sessions['q']
        assert not {i['uri'] for i in blocked} & set(app.starters.bands['song:0']['active'])
        picks=await app._mix_recommend('bedroom',20,mood,root,{'mode':'mood','queue_id':'q',
            'exclude':[i['uri'] for i in blocked]+[root['uri']],'previous':blocked})
        assert picks and all(i['uri'] not in {b['uri'] for b in blocked} for i in picks)
    finally:app.model.db.close()
