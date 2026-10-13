import time
import pytest
from lib.session_feedback import SessionFeedback

def snap(item='a'):
    media={'uri':'library://'+item,'name':'Song '+item,'artist':'Artist '+item}
    return {'state':'playing','current_item':{'queue_item_id':item,'media_item':media},'items':[]}

def setup():
    writes=[]; f=SessionFeedback(lambda:{},lambda v:writes.append(v))
    state=f.state('q',snap(),'mix1')
    return f,state,writes

def event(state, **kw):
    return dict(type='session_feedback',queue_id='q',session_id=state['session_id'],current_item_id='a',vote=-1,
                reason='mood_match',event_id='event-original-0001',timestamp_ms=int(time.time()*1000),**kw)

def test_delayed_vote_rates_original_song_not_current():
    f,s,w=setup(); f.state('q',snap('b'),'mix1')
    assert f.deferred_vote(event(s))['applied']
    assert f.sessions['q']['current_id']=='b'
    assert f.sessions['q']['votes'][0]['item']['uri']=='library://a'
    assert len(w)>1

def test_closed_session_does_not_modify_new_mix():
    f,s,_=setup();f.state('q',snap('b'),'mix2')
    assert not f.deferred_vote(event(s))['active']
    assert f.votes('q','mix2')==[]
    assert f.sessions['q']['archive'][0]['votes'][0]['vote']==-1

def test_duplicate_and_old_delivery_are_safe():
    f,s,_=setup();e=event(s);assert f.deferred_vote(e)['applied']
    assert f.deferred_vote(e)['duplicate']
    old=dict(e,event_id='event-original-0002',timestamp_ms=e['timestamp_ms']-1000,vote=1)
    assert not f.deferred_vote(old)['applied'];assert f.votes('q','mix1')[0]['vote']==-1

def test_unknown_original_item_and_invalid_vote_rejected():
    f,s,_=setup();e=event(s);e['current_item_id']='missing'
    with pytest.raises(ValueError):f.deferred_vote(e)
    e=event(s);e['vote']=False
    with pytest.raises(ValueError):f.deferred_vote(e)

def test_persisted_feedback_survives_service_restart():
    f,s,_=setup();f.state('q',snap('b'),'mix2')
    restored=SessionFeedback(lambda:f.sessions,lambda _:None)
    assert restored.deferred_vote(event(s))['applied']
