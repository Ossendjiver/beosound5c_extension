"""Provider suggestions expand candidates without becoming a second queue owner."""
import asyncio
import json
import time
from unittest.mock import AsyncMock

import aiohttp
import pytest

import library
from lib import music_mood
from lib.ma_recommendations import Candidates, FRESH_SECONDS, STALE_SECONDS, MAX_SEEDS, normalise, aliases


def track(n, *, genre='deep house', artist=None, duration=240, trusted=False, mood=None):
    item = {'uri': f'tidal--one://track/{n}', 'name': f'Selection {n}',
            'artist': artist or f'Performer {n}', 'duration': duration, 'genres': [genre] if genre else [],
            'trusted': trusted, 'media_type': 'track'}
    if mood:
        item['audio_features'] = {'model': 'test', 'energy': mood['energy'], 'valence': mood['valence'],
                                  'profile_confidence': .95, 'bpm': 100}
    return item


def populated(seed, tracks, command=None, clock=None):
    now = clock or time.time
    return Candidates(command or AsyncMock(), clock=now,
        load=lambda: {'version': 1, 'seeds': {seed['uri']: {'updated': now(), 'tracks': tracks}}})


def service(tmp_path, monkeypatch, catalogue):
    monkeypatch.setattr(library, 'DB_PATH', tmp_path/'library.sqlite3')
    result = library.LibraryService()
    result.starters.bind(catalogue, 'test')
    result._ma_candidate_index = {alias: item for item in catalogue for alias in aliases(item)}
    return result


@pytest.mark.asyncio
async def test_wheel_reads_cache_without_awaiting_provider_and_worker_uses_read_only_api():
    seed, candidate = track('root'), track('new')
    command = AsyncMock(return_value=[seed, candidate])
    source = Candidates(command)
    assert source.get([seed]) == []
    assert source.get([seed]) == []
    command.assert_not_called()
    assert list(source.pending) == [seed['uri']]
    assert await source.refresh_one()
    command.assert_awaited_once_with('music/tracks/similar_tracks',
        {'item_id': 'root', 'provider_instance_id_or_domain': 'tidal--one', 'limit': 40, 'allow_lookup': True})
    picks = source.get([seed])
    assert [i['uri'] for i in picks] == [candidate['uri']]
    assert picks[0]['_ma_recommendation'] == seed['uri']


def test_provider_popularity_and_favourites_never_create_local_trust_or_mood():
    raw = dict(track('new', genre=None), favorite=True, trusted=True, play_count=99999,
               metadata={'popularity': 999}, audio_features={'model': 'provider', 'energy': 1, 'valence': 1})
    item = normalise(raw)
    assert not item['trusted'] and not item['favorite']
    assert 'play_count' not in item and 'audio_features' not in item
    assert music_mood.profile(item, {}) is None
    assert normalise(dict(raw, media_type='podcast_episode')) is None
    assert normalise(dict(raw, uri='https://example.com/song')) is None
    assert normalise(dict(raw, duration=float('nan'))) is None
    assert normalise(dict(raw, is_playable=False)) is None


@pytest.mark.asyncio
async def test_metadata_hydration_is_bounded_and_does_not_copy_seed_mood():
    seed = track('root')
    async def command(name, args):
        if name == 'music/tracks/similar_tracks':
            return [track(i, genre=None) for i in range(8)]
        return dict(track(args['uri'].rsplit('/', 1)[-1]), genres=['ambient'])
    command = AsyncMock(side_effect=command)
    source = Candidates(command)
    source.get([seed]); assert await source.refresh_one()
    assert command.await_count == 4  # One provider query, at most three descriptor reads.
    result = source.get([seed])
    assert sum(i.get('genres') == ['ambient'] for i in result) == 3
    assert music_mood.profile(result[-1], {}) is None


@pytest.mark.asyncio
async def test_failure_retains_stale_pool_and_backs_off():
    now = [1000000.]
    seed = track('root')
    command = AsyncMock(side_effect=TimeoutError())
    source = populated(seed, [track('safe')], command, lambda: now[0])
    now[0] += FRESH_SECONDS + 1
    assert source.get([seed])
    assert not await source.refresh_one()
    assert source.get([seed])
    assert not source.pending
    assert command.await_count == 1
    now[0] += 901
    source.get([seed]); assert source.pending
    now[0] += STALE_SECONDS
    assert source.get([seed]) == []


@pytest.mark.asyncio
async def test_rate_limit_applies_globally_and_cancellation_propagates():
    now = [1000000.]
    error = aiohttp.ClientResponseError(None, (), status=429, headers={'Retry-After': '3600'})
    source = Candidates(AsyncMock(side_effect=error), clock=lambda: now[0])
    source.get([track('one'), track('two')])
    assert not await source.refresh_one()
    assert source.cooldown == now[0] + 3600
    assert not await source.refresh_one()
    assert source.command.await_count == 1
    now[0] += 3601
    source.command.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await source.refresh_one()


@pytest.mark.asyncio
async def test_failed_envelope_and_unsupported_api_fall_back_locally():
    source = Candidates(AsyncMock(return_value={'error_code': 999, 'details': 'unsupported'}))
    source.get([track('one')])
    assert not await source.refresh_one()
    assert not source.cache
    assert source.last_error == 'ValueError'


def test_cache_is_bounded_checkpointed_and_safe_on_restart():
    saved = []; now = [1000000.]
    source = Candidates(AsyncMock(), save=saved.append, clock=lambda: now[0])
    for n in range(MAX_SEEDS + 20):
        source.get([track(n)])
    assert len(source.pending) == MAX_SEEDS
    source = populated(track('root'), [track('safe')], clock=lambda: now[0])
    source.save = saved.append; source.dirty = True
    source.persist(); assert not saved
    now[0] += 61; source.persist(); assert len(saved) == 1
    restored = Candidates(AsyncMock(), load=lambda: json.loads(json.dumps(saved[0])), clock=lambda: now[0])
    assert restored.get([track('root')])
    assert Candidates(AsyncMock(), load=lambda: {'version': 1, 'seeds': []}).cache == {}
    disabled = Candidates(AsyncMock(), enabled=False)
    assert disabled.get([track('root')]) == [] and not disabled.pending


def test_song_radio_rejects_wrong_genre_length_duplicates_and_recent_skips(tmp_path, monkeypatch):
    root = track('root', trusted=True)
    skipped = track('skipped')
    suggestions = [track('good'), track('wrong', genre='death metal'), track('long', duration=2700),
                   dict(root, uri='soundcloud--one://track/alternate'), skipped]
    svc = service(tmp_path, monkeypatch, [root])
    svc.ma_recommendations = populated(root, suggestions)
    svc.model.record_listen(dict(skipped, uri='soundcloud--one://track/same-recording', selection_origin='manual', end_reason='skip'), 5,
                            svc._context_for_room('lounge'))
    context = dict(svc._context_for_room('lounge'), radio=True, seed_features=root, relation_seed=root,
                   queue_previous=[root], queue_versions=[root], starter_exclude=[root['uri']])
    candidates = svc._provider_candidates([], context, [root])
    assert {i['uri'] for i in candidates} == {track('good')['uri'], skipped['uri'], track('wrong')['uri']}
    picks = svc.model.rank(candidates, context, 10)
    assert [i['uri'] for i in picks] == [track('good')['uri']]
    svc.model.db.close()


def test_long_mix_root_admits_only_mix_lengths_and_never_inherits_seed_tags(tmp_path, monkeypatch):
    root = track('mix', duration=3600, trusted=True)
    svc = service(tmp_path, monkeypatch, [root])
    svc.ma_recommendations = populated(root, [track('normal'), track('long', duration=3500),
                                                track('too-long', duration=12000), track('unknown', genre=None, duration=3500)])
    ctx = dict(svc._context_for_room('lounge'), radio=True, seed_features=root)
    candidates = svc._provider_candidates([], ctx, [root])
    assert {i['uri'] for i in candidates} == {track('long')['uri'], track('unknown')['uri']}
    assert [i['uri'] for i in svc.model.rank(candidates, ctx, 5)] == [track('long')['uri']]
    svc.model.db.close()


def test_active_session_prefetch_takes_priority_over_background_sectors():
    source = Candidates(AsyncMock())
    source.get([track('background-1'), track('background-2')])
    source.get([track('root'), track('tail')], priority=True)
    assert list(source.pending)[:2] == [track('root')['uri'], track('tail')['uri']]


def test_new_candidates_only_retry_relevant_starved_session(tmp_path, monkeypatch):
    svc = service(tmp_path, monkeypatch, [])
    svc.mixes.sessions = {q: {'refresh_status': status, 'next_refill': 100, 'retry_after': 100}
        for q, status in [('starved', 'waiting_for_candidates'), ('other', 'insufficient_mood_data'), ('healthy', 'ready')]}
    svc._ma_session_seeds = {'starved': {'seed'}, 'other': {'different'}, 'healthy': {'seed'}}
    svc._provider_candidates_refreshed('seed')
    assert 'next_refill' not in svc.mixes.sessions['starved']
    assert svc.mixes.sessions['other']['next_refill'] == 100
    assert svc.mixes.sessions['healthy']['next_refill'] == 100
    svc.model.db.close()


@pytest.mark.asyncio
async def test_inflight_lookup_is_not_repeated_when_another_request_requeues_seed():
    seed = track('root')
    async def command(*_):
        source.get([seed])
        return [track('new')]
    source = Candidates(AsyncMock(side_effect=command))
    source.get([seed]); assert await source.refresh_one()
    assert seed['uri'] in source.pending
    assert not await source.refresh_one()
    assert source.command.await_count == 1


def test_mood_unknown_or_wrong_never_admitted_by_recommendation_or_counts(tmp_path, monkeypatch):
    mood = music_mood.selection(0, .85)
    local = [track(f'known-{n}', trusted=True, mood=mood) for n in range(8)]
    root = local[0]
    matching = track('new', genre='happy')
    wrong = track('wrong', genre='metal')
    unknown = track('unknown', genre=None)
    svc = service(tmp_path, monkeypatch, local)
    svc.ma_recommendations = populated(root, [matching, wrong, unknown])
    ctx = dict(svc._context_for_room('lounge'), mood=mood, seed_features=root)
    candidates = svc._provider_candidates(local, ctx, [root])
    picks = svc.model.rank(candidates, ctx, 20)
    uris = {i['uri'] for i in picks}
    assert matching['uri'] in uris
    assert wrong['uri'] not in uris and unknown['uri'] not in uris
    assert next(i for i in picks if i['uri'] == matching['uri'])['_selection_category'] == 'discovery'
    ctx['mood'] = music_mood.selection(0, 0)
    assert matching['uri'] not in {i['uri'] for i in svc.model.rank(candidates, ctx, 20)}
    svc.model.db.close()


def test_exact_provider_mapping_uses_canonical_profile_and_preference(tmp_path, monkeypatch):
    mood = music_mood.selection(0, .7)
    root = track('root', trusted=True, mood=mood)
    raw = track('new', genre=None)
    known = dict(raw, uri='library://track/42', trusted=True, audio_features=root['audio_features'],
                 provider_mappings=[{'provider_instance': 'tidal--one', 'item_id': 'new'}])
    svc = service(tmp_path, monkeypatch, [root, known])
    svc.ma_recommendations = populated(root, [raw])
    ctx = dict(svc._context_for_room('lounge'), seed_features=root)
    items = svc._provider_candidates([], ctx, [root])
    assert [i['uri'] for i in items] == [known['uri']]
    assert items[0]['trusted'] and items[0]['audio_features'] == root['audio_features']
    ctx['starter_exclude'] = [raw['uri']]
    assert svc._provider_candidates([], ctx, [root]) == []
    svc.model.db.close()


def test_radio_widens_only_from_locally_compatible_anchor(tmp_path, monkeypatch):
    root = track('root', trusted=True)
    wrong = track('metal', genre='metal')
    svc = service(tmp_path, monkeypatch, [root])
    svc.ma_recommendations = Candidates(AsyncMock())
    ctx = dict(svc._context_for_room('lounge'), radio=True, seed_features=root, relation_seed=wrong)
    svc._rank_starters([root], ctx, 5)
    assert list(svc.ma_recommendations.pending) == [root['uri']]
    assert svc.model.last_trace['ma_recommendations']['seeds'] == [root['uri']]
    svc.model.db.close()


@pytest.mark.asyncio
async def test_accepted_provider_track_can_seed_next_batch_without_being_imported(tmp_path, monkeypatch):
    root, accepted, local = track('root', trusted=True), track('accepted'), track('local', trusted=True)
    svc = service(tmp_path, monkeypatch, [root, local])
    svc.ma_recommendations = populated(root, [accepted])
    svc._mood_candidates = lambda *_: ([root, local], False)
    playing = {k: v for k, v in accepted.items() if k not in ('genres', 'trusted')}
    await svc._mix_recommend('lounge', 5, None, root,
        {'mode': 'radio', 'relation_seed': playing, 'exclude': [root['uri'], accepted['uri']], 'previous': [root, playing]})
    assert accepted['uri'] in svc.ma_recommendations.pending
    assert accepted['uri'] in svc.model.last_trace['ma_recommendations']['seeds']
    svc.model.db.close()


def test_pattern_predictions_seed_provider_queries_and_preserve_discovery_quota(tmp_path, monkeypatch):
    local = [track(f'known-{n}', trusted=True) for n in range(20)]
    svc = service(tmp_path, monkeypatch, local)
    svc.ma_recommendations = populated(local[0], [track(f'new-{n}') for n in range(12)])
    ctx = svc._context_for_room('lounge')
    items = svc._provider_candidates(local, ctx, [local[0]])
    picks = svc.model.rank(items, ctx, 20)
    new = [i for i in picks if i.get('_ma_recommendation')]
    assert 0 < len(new) <= 2  # Default 10%, never promoted by provider popularity.
    svc._rank_starters(local, svc._context_for_room('lounge'), 5)
    assert svc.model.last_trace['ma_recommendations']['seeds']
    svc.model.db.close()
