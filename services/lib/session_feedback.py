"""Persisted reversible queue-session votes with current-item race protection."""
import time
import uuid
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
            s = {'id': uuid.uuid4().hex, 'mix_id': mix_id, 'votes': [], 'known': [], 'updated': now}
            self.sessions[queue] = s
        known = [str(i.get('queue_item_id') or '') for i in items]
        if playing['id']:
            known.append(playing['id'])
        changed = reset or playing['id'] != s.get('current_id') or known != s.get('known')
        s.update(current_id=playing['id'], item=media, known=known, updated=now)
        if active:
            s['last_active'] = now
        if changed:
            # Bound storage and writes; polling alone never writes to disk.
            self.sessions = {q:v for q,v in self.sessions.items() if now-v.get('updated', 0) <= 86400}
            self.save(self.sessions)
        vote = next((v['vote'] for v in s['votes'] if mix_policy.same_recording(media, v['item'])), 0)
        return {'queue_id': queue, 'session_id': s['id'], 'current_item_id': playing['id'],
                'vote': vote, 'available': bool(playing['id'] and playing['uri'] and snapshot.get('state') in ('playing', 'paused', 'buffering'))}

    def vote(self, queue, state, session_id, item_id, vote):
        if type(vote) is not int or vote not in (-1, 0, 1):
            raise ValueError('Vote must be -1, 0 or 1')
        if not state['available'] or session_id != state['session_id'] or item_id != state['current_item_id']:
            raise ValueError('The playing song or session changed; refresh the queue')
        s = self.sessions[queue]
        s['votes'] = [v for v in s['votes'] if not mix_policy.same_recording(s['item'], v['item'])]
        if vote:
            s['votes'].append({'item': s['item'], 'vote': vote})
        s['votes'] = s['votes'][-200:]
        self.save(self.sessions)
        return dict(state, vote=vote)

    def votes(self, queue, mix_id=None):
        s = self.sessions.get(queue) or {}
        if s.get('mix_id') != mix_id:
            return []
        return s.get('votes', []) if mix_id or time.time()-s.get('updated', 0) <= 1800 else []
