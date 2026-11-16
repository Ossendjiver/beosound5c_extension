import json
import time
from unittest.mock import patch
import pytest
import library
from lib import listening_variety, mix_policy


def track(n, artist=None):
    return {'uri': f'tidal://track/{n}', 'name': f'Track {n}', 'artist': artist or f'Artist {n}',
            'duration': 240, 'media_type': 'track', 'trusted': True, 'genre': 'deep house'}


def listen(item, now, age=60, seconds=200, **extra):
    return dict(item, title=item['name'], ts=now-age, seconds=seconds, end_reason='complete', origin='automatic', **extra)


def test_recent_play_cooldown_follows_recording_across_providers_and_versions():
    now=time.time(); original=track(1, 'Artist')
    alternate=dict(original, uri='soundcloud://track/other', name=original['name']+' (Remastered)')
    prior=listening_variety.Prior([listen(original,now)], now=now)
    assert prior.describe(alternate)['tier']==3
    assert prior.describe(alternate)['penalty']>=12


def test_recent_play_cooldowns_relax_oldest_first_and_ignore_brief_contacts():
    now=time.time()
    rows=[listen(track(n),now,age=age) for n,age in [(1,60),(2,8*3600),(3,2*86400),(4,4*86400)]]
    prior=listening_variety.Prior(rows+[listen(track(5),now,seconds=2)],now=now)
    assert [prior.describe(track(n))['tier'] for n in range(1,6)]==[3,2,1,0,0]


def test_proposals_survive_reload_without_creating_listening_rewards(tmp_path):
    now=time.time();model=library.LocalModel(tmp_path/'db')
    model.put_kv('play_music_proposals',listening_variety.remember([], [track(1)],now))
    restored=library.LocalModel(tmp_path/'db')
    assert listening_variety.Prior([],restored.get_kv('play_music_proposals'),now).describe(track(1))['tier']==4
    assert not restored._history()
    assert not listening_variety.remember(restored.get_kv('play_music_proposals'),[],now+13*3600)


def test_positive_learning_has_a_real_cap_but_keeps_preference_ordering():
    assert 0 < listening_variety.learning_bonus(1) < listening_variety.learning_bonus(4)
    assert listening_variety.learning_bonus(100000)==6


def test_consecutive_starts_rotate_before_reusing_the_same_recordings(tmp_path):
    model=library.LocalModel(tmp_path/'db'); songs=[track(n) for n in range(35)]
    now=time.time(); proposals=[]; heads=[]
    for n in range(6):
        picks=model.rank(songs,dict(play_music=True,rotation_token=str(n),recent_proposals=proposals),5)
        heads.append(picks[0]['uri'])
        assert len(picks)==5
        prior=listening_variety.Prior([],proposals,now)
        assert all(prior.describe(i)['tier']==0 for i in picks)
        proposals=listening_variety.remember(proposals,picks,now)
    assert len(set(heads))==6


def test_small_library_relaxes_cooldown_instead_of_returning_empty(tmp_path):
    model=library.LocalModel(tmp_path/'db');songs=[track(1)]
    proposals=listening_variety.remember([],songs,time.time())
    assert model.rank(songs,dict(play_music=True,recent_proposals=proposals),5)[0]['uri']==songs[0]['uri']


def test_artist_avoidance_is_exact_normalized_and_includes_collaborations():
    prefs={'avoided artist':'Avoided Artist'}
    assert listening_variety.avoided({'artists':[{'name':'AVOIDED ARTIST'},{'name':'Other'}]},prefs)
    assert listening_variety.avoided({'artist':'Avoided Artist feat. Other'},prefs)
    assert listening_variety.avoided({'artist':'Upload Channel','name':'Avoided Artist - Song'},prefs)
    assert not listening_variety.avoided({'artist':'Avoided Artist Tribute Band'},prefs)
    assert listening_variety.artist_key('Björk')==listening_variety.artist_key('Bjork')


def test_avoided_artist_cannot_win_via_favourites_counts_radio_or_sparse_fallback(tmp_path):
    model=library.LocalModel(tmp_path/'db');bad=track(1,'Avoided Artist');bad['favorite']=True
    model.put_kv('avoided_artists',{'avoided artist':'Avoided Artist'})
    assert model.rank([bad],{'play_music':True},20)==[]
    assert model.last_trace['rejected']['avoided_artist']==1
    good=track(2)
    assert all(i['artist']!='Avoided Artist' for i in model.rank([bad,good],{'seed_features':bad,'radio':True},20))


@pytest.mark.asyncio
async def test_artist_avoid_and_allow_persist_without_mutating_playback(tmp_path):
    model=library.LocalModel(tmp_path/'db');service=object.__new__(library.LibraryService);service.model=model
    class Request:
        async def json(self): return {'type':'artist_feedback','artist':'Avoided Artist','action':self.action}
    request=Request(); request.action='avoid'
    response=await service.handle_event(request)
    assert json.loads(response.text)['avoided']
    assert library.LocalModel(tmp_path/'db').get_kv('avoided_artists')=={'avoided artist':'Avoided Artist'}
    request.action='allow'; response=await service.handle_event(request)
    assert not json.loads(response.text)['avoided']
    assert model.get_kv('avoided_artists')=={}
