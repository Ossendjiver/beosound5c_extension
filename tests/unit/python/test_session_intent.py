import copy
import time
import pytest
import library
from lib import session_intent, music_mood, music_features, queue_spacing
from lib.session_feedback import SessionFeedback


def track(uri, artist='Artist', title='Song', vector=None):
    return {'uri': uri, 'name': title, 'artist': artist, 'duration': 200, 'trusted': True,
            'audio_features': {'bpm': 110, 'embedding_model': 'test', 'embedding': vector or [1.]*8}}


def snapshot(item, id='a', state='playing', known=('a','b')):
    return {'state': state, 'current_index': 0,
            'current_item': {'queue_item_id': id, 'media_item': item},
            'items': [{'queue_item_id': k, 'media_item': item} for k in known]}


def test_votes_replace_clear_persist_and_match_other_provider():
    stored={}; writes=[]
    def save(value):
        stored['value']=copy.deepcopy(value);writes.append(1)
    feedback=SessionFeedback(lambda: {},save)
    a=track('tidal://a')
    state=feedback.state('q',snapshot(a),'mix-one')
    feedback.vote('q',state,state['session_id'],'a',1)
    feedback.vote('q',state,state['session_id'],'a',1)
    assert len(feedback.votes('q','mix-one'))==1
    restored=SessionFeedback(lambda:stored['value'],save)
    other=restored.state('q',snapshot(track('sc://a'),'b'),'mix-one')
    assert other['vote']==1
    restored.vote('q',other,other['session_id'],'b',-1)
    assert len(restored.votes('q','mix-one'))==1 and restored.votes('q','mix-one')[0]['vote']==-1
    restored.vote('q',other,other['session_id'],'b',0)
    assert restored.votes('q','mix-one')==[]
    before=len(writes)
    restored.state('q',snapshot(track('sc://a'),'b'),'mix-one')
    assert len(writes)==before
    assert restored.state('q',snapshot(a),'mix-two')['session_id']!=state['session_id']
    assert feedback.state('other',snapshot(a),'mix-one')['vote']==0


def test_votes_survive_unattended_mix_and_do_not_leak_to_new_mix(monkeypatch):
    f=SessionFeedback(lambda:{},lambda _:None)
    s=f.state('q',snapshot(track('a')),'mix')
    f.vote('q',s,s['session_id'],'a',-1)
    f.sessions['q']['updated']-=7200
    assert len(f.votes('q','mix'))==1
    assert f.votes('q','new-mix')==[]
    assert f.votes('q')==[]
    # Queue has advanced while the phone was absent, but mix identity survives.
    new=f.state('q',snapshot(track('b',title='Other'),'unseen',known=('unseen',)),'mix')
    assert new['session_id']==s['session_id']
    assert len(f.votes('q','mix'))==1


def test_stale_song_session_and_invalid_votes_are_rejected():
    f=SessionFeedback(lambda:{},lambda _:None)
    old=f.state('q',snapshot(track('a')))
    new=f.state('q',snapshot(track('b',title='Other'),'b'))
    for session,item,vote in [(old['session_id'],'a',1),('wrong','b',1),(new['session_id'],'b',True),(new['session_id'],'b',2)]:
        with pytest.raises(ValueError):f.vote('q',new,session,item,vote)


def test_recent_intent_uses_listened_room_scoped_tracks_not_skips():
    now=time.time();a=track('a');b=track('b',title='Other')
    rows=[dict(a,ts=now-10,room='bedroom',reward=-1,end_reason='skip'),
          dict(b,ts=now-20,room='kitchen',reward=1),dict(b,ts=now-50,room='bedroom',reward=.05),
          dict(a,ts=now-6000,room='bedroom',reward=1)]
    assert session_intent.accepted(rows,[a,b],'bedroom',now)==[b]
    near=track('near');far=track('far',vector=[-1.]*8)
    assert session_intent.bonus(near,[b],[],{})>session_intent.bonus(far,[b],[],{})
    assert session_intent.bonus({'uri':'missing'},[b],[],{})==0


def test_audio_intent_and_cross_provider_vote_affect_rank_without_global_dislike(tmp_path):
    m=library.LocalModel(tmp_path/'db')
    near=track('near',artist='Near');far=track('far',artist='Far',vector=[-1.]*8)
    heard=track('heard',artist='Heard')
    m.record_listen(dict(heard,selection_origin='manual'),195,{'room':'bedroom'})
    picks=m.rank([near,far,heard],{'room':'bedroom'},3)
    assert picks.index(near)<picks.index(far)
    vote={'item':dict(near,uri='other-provider'), 'vote':-1}
    assert near not in m.rank([near,far,heard],{'room':'bedroom','session_votes':[vote]},3)
    assert near in m.rank([near,far,heard],{'room':'kitchen'},3)
    assert m.get_kv('music_feedback',{})=={}


def test_completed_tracks_remain_intent_after_removed_from_candidate_queue(tmp_path):
    m=library.LocalModel(tmp_path/'db')
    near=track('near',artist='Near');far=track('far',artist='Far',vector=[-1.]*8)
    heard=track('heard',artist='Heard')
    m.record_listen(dict(heard,selection_origin='manual'),195,{'room':'bedroom'})
    picks=m.rank([far,near],{'room':'bedroom','intent_catalogue':[far,near,heard]},2)
    assert picks[0]==near


def test_completion_and_deliberate_replay_are_richer_than_autoplay():
    assert session_intent.reward(195,200,'manual','ended')>session_intent.reward(100,200,'manual','ended')
    assert session_intent.reward(195,200,'manual','ended',True)>session_intent.reward(195,200,'manual','ended')
    assert session_intent.reward(195,200,'automatic','ended')==.05
    for reason in ('pause','transfer','stop','buffering'):
        assert session_intent.reward(10,200,'manual',reason)==0


def test_cached_similarity_preserves_validated_audio_rules():
    sound=music_features.Similarity()
    root=track('root')
    variants=[track('same'),track('different',vector=[-1.]*8),{'audio_features':{'bpm':55}},
              {'audio_features':{'embedding':[1.]*8,'embedding_model':'other'}},
              {'audio_features':{'embedding':[float('nan')]*8,'bpm':110}}, {}]
    for item in variants:
        assert sound(item,root)==pytest.approx(music_features.similarity(item,root))
    size=len(sound.cache)
    for item in variants:sound(item,root)
    assert len(sound.cache)==size
    pool=[(1,track('a','A')),(1,track('b','B')),(1,track('c','C'))]
    assert queue_spacing.spaced(pool,['A','B'])==queue_spacing.spaced(pool,['A','B'],{'a':'a','b':'b','c':'c'})


def test_liked_audio_never_admits_mood_incompatible_track(tmp_path):
    m=library.LocalModel(tmp_path/'db')
    mood=music_mood.selection(90,.1)
    # Far-away coordinates are rejected before any audio/vote/familiarity bonus.
    wrong=track('wrong');wrong['audio_features'].update(model='test',energy=0.,valence=0.)
    assert wrong not in m.rank([wrong],{'mood':mood,'session_votes':[{'item':wrong,'vote':1}]},1)


@pytest.mark.asyncio
async def test_feedback_api_preserves_current_item_and_marks_upcoming_refresh(tmp_path, monkeypatch, mock_config):
    import json
    mock_config({})
    monkeypatch.setattr(library, 'DB_PATH', tmp_path/'service.db')
    service=library.LibraryService()
    snap=snapshot(track('a'))
    calls=[]
    async def command(name, **kwargs):
        calls.append(name)
        assert name=='mood_snapshot'  # Feedback does not interrupt playback.
        return {'state':'ok', 'snapshot':snap}
    service.mixes.command=command
    service.mixes.sessions['q']=dict(session_id='mix',room='bedroom',mode='radio',queue_id='q',
        awaiting_choice=False,mood=None,title='Song',updated=time.time(),generation=0)
    class Request:
        method='GET';query={'queue_id':'q','feedback':'1'}
        async def json(self):return self.data
    req=Request()
    state=json.loads((await service.handle_mix(req)).body)['feedback']
    req.method='POST';req.data=dict(action='feedback',queue_id='q',session_id=state['session_id'],current_item_id='a',vote=-1)
    response=json.loads((await service.handle_mix(req)).body)
    assert response['feedback']['vote']==-1 and response['pending_refresh']
    assert service.mixes.sessions['q']['generation']==1
    assert snap['current_item']['queue_item_id']=='a'
    assert service.model.get_kv('music_feedback',{})=={}
    service.model.db.close()

def test_song_radio_rejects_unrelated_favourites_and_missing_data():
    root=track('seed',vector=[1.,0.,0.,0.,0.,0.,0.,0.])
    close=track('close',artist='Other',vector=[.98,.1,0.,0.,0.,0.,0.,0.])
    unrelated=track('unrelated',artist='Other',vector=[0.,1.,0.,0.,0.,0.,0.,0.])
    unknown={'uri':'unknown','artist':'Other','favorite':True}
    assert music_features.radio_distance(close,root) is not None
    assert music_features.radio_distance(unrelated,root) is None
    assert music_features.radio_distance(unknown,root) is None

def test_song_radio_requires_compatible_energy_and_valence():
    root={'audio_features':{'model':'test','energy':.2,'valence':.3}}
    assert music_features.radio_distance({'audio_features':{'model':'test','energy':.25,'valence':.35}},root) is not None
    assert music_features.radio_distance({'audio_features':{'model':'test','energy':.9,'valence':.8}},root) is None

def test_radio_ranking_prefers_seed_sound_over_unrelated_favourites(tmp_path):
    model=library.LocalModel(tmp_path/'radio.db')
    root=track('seed',vector=[1.,0.,0.,0.,0.,0.,0.,0.])
    near=track('near',artist='New artist',vector=[.99,.1,0.,0.,0.,0.,0.,0.])
    far=track('far',artist='Familiar artist',vector=[0.,1.,0.,0.,0.,0.,0.,0.])
    far['favorite']=True
    picks=model.rank([far,near],{'radio':True,'seed_features':root},5)
    assert near in picks and far not in picks

def test_classical_work_radio_cannot_admit_pop_with_similar_embedding():
    root=track('seed',title='Grieg: Peer Gynt, Op. 23: Solveig Song')
    pop=track('pop',artist='Other',title='Borderline')
    classical=track('other',artist='Other',title='Bach: Concerto in D Minor BWV 974')
    assert music_features.radio_distance(pop,root) is None
    assert music_features.radio_distance(classical,root) is not None

def test_radio_falls_back_from_audio_to_specific_then_broad_genre():
    root={**track('seed',vector=[1.,0.,0.,0.,0.,0.,0.,0.]),'genres':['Deep House']}
    far={**track('same',artist='Other',vector=[0.,1.,0.,0.,0.,0.,0.,0.]),'genres':['deep-house']}
    broader={**far,'uri':'broad','genres':['Tech House']}
    unrelated={**far,'uri':'rock','genres':['Indie Rock']}
    assert music_features.radio_distance(far,root)==1.1
    assert music_features.radio_distance(broader,root)==1.32
    assert music_features.radio_distance(unrelated,root) is None

def test_solveig_radio_can_use_other_classical_repertoire_without_analysis():
    root={'name':"Grieg: Peer Gynt, Op. 23: Solveig's Song (Arr. for Cello and Piano)",'artist':'Maisky'}
    specific={'name':'Sonata for Cello and Piano','artist':'Another'}
    broad={'name':'Symphony No. 5','artist':'Orchestra'}
    pop={'name':'Borderline','artist':'Other','genres':['Indie Pop']}
    assert music_features.radio_distance(specific,root)==1.1
    assert music_features.radio_distance(broad,root)==1.4
    assert music_features.radio_distance(pop,root) is None

def test_classical_fallback_does_not_admit_ambiguous_or_mixed_album_tags():
    root={'name':'Grieg: Peer Gynt Op. 23','artist':'Maisky'}
    assert music_features.radio_distance({'name':'All Night','artist':'Beyonce','genres':['Romantic','Soul']},root) is None
    assert music_features.radio_distance({'name':'Hybrid pop','artist':'Other','genres':['Classical','Pop','Electronic']},root) is None


@pytest.mark.parametrize('vote', [-1, 1])
@pytest.mark.parametrize('reason', ['track_match', 'genre_match', 'mood_match', 'tempo_match'])
def test_matching_dimensions_accept_both_polarities_and_persist(vote, reason):
    f=SessionFeedback(lambda:{}, lambda _:None)
    s=f.state('q',snapshot(track('a')),'mix')
    result=f.vote('q',s,s['session_id'],'a',vote,reason)
    assert result['reason']==reason and result['vote']==vote
    assert f.state('q',snapshot(track('a')),'mix')['reason']==reason
    with pytest.raises(ValueError):f.vote('q',s,s['session_id'],'a',0,reason)


def test_reason_scoring_is_dimension_specific_and_signed():
    a=track('a', title='First'); a.update(genres=['Deep House'])
    same_genre=track('b', title='Second');same_genre.update(genres=['Deep House'])
    other_genre=track('c', title='Third');other_genre.update(genres=['Folk'])
    for vote in [-1,1]:
        votes=[{'item':a,'vote':vote,'reason':'genre_match'}]
        score=lambda item: session_intent.bonus(item,[],votes,{})
        assert vote*score(same_genre)>vote*score(other_genre)
    assert session_intent.feedback_similarity(same_genre,a,'track_match')==1
    assert session_intent.feedback_similarity(dict(same_genre,audio_features={'embedding_model':'test','embedding':[-1.]*8}),a,'track_match')==0
    assert session_intent.feedback_similarity({'uri':'missing'},a,'track_match')==0
    assert session_intent.feedback_similarity(dict(same_genre,audio_features={'embedding_model':'other','embedding':[1.]*8}),a,'track_match')==0
    assert session_intent.feedback_similarity(dict(a,uri='other-provider'),a,'track_match')==1
    # The same sound is not genre evidence, and unknown metadata is neutral.
    assert session_intent.feedback_similarity(track('unknown'),a,'genre_match')==0
    assert session_intent.feedback_similarity({'genres':['Ambient']},a,'genre_match')==0


def test_mood_and_tempo_reasons_do_not_borrow_genre_or_unsupported_profiles():
    a={'genres':['Deep House'],'audio_features':{'model':'test','energy':.2,'valence':.3,'bpm':120,'bpm_confidence':.8}}
    b={'genres':['Folk'],'audio_features':{'model':'test','energy':.2,'valence':.3,'bpm':60,'bpm_confidence':.8}}
    distant={'genres':['Deep House'],'audio_features':{'model':'test','energy':.9,'valence':.9,'bpm':165,'bpm_confidence':.8}}
    assert session_intent.feedback_similarity(b,a,'mood_match')==1
    assert session_intent.feedback_similarity(distant,a,'mood_match')==0
    assert session_intent.feedback_similarity(b,a,'tempo_match')==1
    assert session_intent.feedback_similarity(distant,a,'tempo_match')==0
    for reason in ['mood_match','tempo_match']:
        assert session_intent.feedback_similarity({},a,reason)==0
    assert session_intent.feedback_similarity({'audio_features':dict(b['audio_features'], profile_confidence=.1)},a,'mood_match')==0
    assert session_intent.feedback_similarity({'audio_features':dict(b['audio_features'], bpm_confidence=.1)},a,'tempo_match')==0


def test_dimension_positive_feedback_cannot_override_hard_mood_or_length_gates(tmp_path):
    model=library.LocalModel(tmp_path/'gate.db')
    root=track('root');root['audio_features'].update(model='test',energy=.5,valence=.5)
    wrong=track('wrong');wrong['audio_features'].update(model='test',energy=0.,valence=0.)
    long=track('long');long['duration']=1000
    for reason in ['track_match','genre_match','mood_match','tempo_match']:
        context={'mood':music_mood.selection(90,.1),'seed_features':root,
                 'session_votes':[{'item':wrong,'vote':1,'reason':reason},{'item':long,'vote':1,'reason':reason}]}
        assert wrong not in model.rank([root,wrong,long],context,3)
        assert long not in model.rank([root,wrong,long],context,3)
    model.db.close()
