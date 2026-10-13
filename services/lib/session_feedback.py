"""Persisted reversible queue-session votes with current-item race protection."""
import time
import uuid
import math
import re
from . import mix_policy
from .mood_mix import current, item_index


class SessionFeedback:
    def __init__(self, load, save):
        value = load() or {}
        self.sessions = value if isinstance(value, dict) else {}
        self.save = save

    def state(self, queue, snapshot, mix_id=None):
        playing = current(snapshot)
        items = snapshot.get('items') or []
        index = item_index(snapshot)
        raw = snapshot.get('current_item') or (items[index] if isinstance(index, int) and 0 <= index < len(items) else {})
        media = dict(raw.get('media_item') or raw)
        media.setdefault('uri', playing['uri'])
        s = self.sessions.get(queue)
        now = time.time()
        # An explicit new mix or unrelated replacement starts a new session.
        active = snapshot.get('state') in ('playing', 'paused', 'buffering')
        reset = not s or (not active and now-s.get('last_active', s.get('updated', 0)) > 1800) or mix_id != s.get('mix_id')
        if s and not mix_id and playing['id'] and playing['id'] not in s.get('known', []):
            reset = True
        if reset:
            archive = ((s or {}).get('archive', []) + ([dict(s, archive=[])] if s else []))[-20:]
            s = {'id': uuid.uuid4().hex, 'mix_id': mix_id, 'votes': [], 'known': [], 'records': {}, 'archive': archive, 'updated': now}
            self.sessions[queue] = s
        known = [str(i.get('queue_item_id') or '') for i in items]
        if playing['id']:
            known.append(playing['id'])
        changed = reset or playing['id'] != s.get('current_id') or known != s.get('known')
        s.setdefault('records', {})[playing['id']] = media
        s['records'] = dict(list(s['records'].items())[-300:])
        s.update(current_id=playing['id'], item=media, known=known, updated=now)
        if active:
            s['last_active'] = now
        if changed:
            # Bound storage and writes; polling alone never writes to disk.
            self.sessions = {q:v for q,v in self.sessions.items() if now-v.get('updated', 0) <= 30*86400}
            self.save(self.sessions)
        voted = next((v for v in s['votes'] if mix_policy.same_recording(media, v['item'])), {})
        vote = voted.get('vote', 0)
        return {'queue_id': queue, 'session_id': s['id'], 'current_item_id': playing['id'],
                'vote': vote, 'reason': voted.get('reason'), 'available': bool(playing['id'] and playing['uri'] and snapshot.get('state') in ('playing', 'paused', 'buffering'))}

    def vote(self, queue, state, session_id, item_id, vote, reason=None):
        if type(vote) is not int or vote not in (-1, 0, 1):
            raise ValueError('Vote must be -1, 0 or 1')
        if reason not in (None, '', 'track_match', 'genre_match', 'mood_match', 'tempo_match', 'wrong_mood', 'overplayed', 'dislike_recording', 'more_like_this'):
            raise ValueError('Unknown feedback reason')
        if reason and (reason in ('wrong_mood', 'overplayed', 'dislike_recording') and vote != -1 or reason == 'more_like_this' and vote != 1 or vote == 0):
            raise ValueError('Feedback reason does not match vote')
        if not state['available'] or session_id != state['session_id'] or item_id != state['current_item_id']:
            raise ValueError('The playing song or session changed; refresh the queue')
        s = self.sessions[queue]
        s['votes'] = [v for v in s['votes'] if not mix_policy.same_recording(s['item'], v['item'])]
        if vote:
            s['votes'].append({'item': s['item'], 'vote': vote, 'reason': reason or None, 'ts': time.time()})
        s['votes'] = s['votes'][-200:]
        s.setdefault('feedback_versions', {})[str(item_id)] = time.time()
        self.save(self.sessions)
        return dict(state, vote=vote, reason=reason or None)

    def votes(self, queue, mix_id=None):
        s = self.sessions.get(queue) or {}
        if s.get('mix_id') != mix_id:
            return []
        return s.get('votes', []) if mix_id or time.time()-s.get('updated', 0) <= 1800 else []

    def deferred_vote(self, payload):
        queue = payload.get('queue_id')
        root = self.sessions.get(queue) or {}
        s = next((v for v in [root, *root.get('archive', [])] if v.get('id') == payload.get('session_id')), None)
        stamp = float(payload.get('timestamp_ms', 0))/1000
        event = str(payload.get('event_id') or '')
        if not math.isfinite(stamp) or not time.time()-30*86400 <= stamp <= time.time()+300 or not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', event):
            raise ValueError('Invalid saved feedback')
        if not s:
            return {'status': 'ok', 'expired': True}
        item_id = payload.get('current_item_id')
        item = s.get('records', {}).get(item_id)
        if not item and item_id == s.get('current_id'):
            item = s.get('item')
        if not item:
            raise ValueError('Original song was not observed in this session')
        receipts = s.setdefault('receipts', [])
        if event in receipts:
            return {'status': 'ok', 'duplicate': True}
        # Validate reasons and polarity through the same current-vote validator,
        # using an isolated instance; never change the live current item.
        trial = SessionFeedback(lambda: {queue: dict(s, item=item, votes=[])}, lambda _: None)
        trial.vote(queue, {'available': True, 'session_id': s['id'], 'current_item_id': item_id},
                   s['id'], item_id, payload.get('vote'), payload.get('reason'))
        versions = s.setdefault('feedback_versions', {})
        key = str(item_id)
        newest_recording = max((v.get('ts', 0) for v in s.get('votes', []) if mix_policy.same_recording(item, v['item'])), default=0)
        newer = stamp >= max(versions.get(key, 0), newest_recording)
        if newer:
            s['votes'] = [v for v in s.get('votes', []) if not mix_policy.same_recording(item, v['item'])]
            if payload['vote']:
                s['votes'].append({'item': item, 'vote': payload['vote'], 'reason': payload.get('reason'), 'ts': stamp})
            s['votes'] = s['votes'][-200:]
            versions[key] = stamp
        receipts.append(event); s['receipts'] = receipts[-500:]
        self.save(self.sessions)
        return {'status': 'ok', 'applied': newer, 'active': s is root}
