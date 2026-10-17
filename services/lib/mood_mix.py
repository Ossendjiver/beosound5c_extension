"""Server-owned live mood sessions; never restart the current queue item."""
import asyncio
import aiohttp
import time
import uuid
import logging

log = logging.getLogger(__name__)
from . import music_mood, mix_policy


def current(snapshot):
    item = snapshot.get('current_item') or {}
    if not item:
        items = snapshot.get('items') or []
        index = item_index(snapshot)
        if isinstance(index, int) and 0 <= index < len(items):
            item = items[index]
    media = item.get('media_item') or item
    elapsed = float(snapshot.get('elapsed_time') or 0)
    stamp = snapshot.get('elapsed_time_last_updated')
    if snapshot.get('state') == 'playing' and isinstance(stamp, (float, int)) and stamp > 0:
        elapsed += max(0, time.time()-stamp)
    return {'id': str(item.get('queue_item_id') or ''),
            'uri': str(media.get('uri') or item.get('uri') or ''),
            'title': media.get('name') or item.get('name') or '',
            'duration': float(item.get('duration') or media.get('duration') or 0),
            'elapsed': elapsed}


def item_index(snapshot):
    index = snapshot.get('current_index')
    return index - int(snapshot.get('items_offset') or 0) if isinstance(index, int) else None


class MoodMixes:
    def __init__(self, command, recommend, record=None, load=None, save=None):
        self.command, self.recommend, self.record = command, recommend, record
        self.save = save
        stored = load() if load else {}
        if not isinstance(stored,dict) or not isinstance(stored.get('sessions'),dict):
            stored = {}
        self.sessions = {q:s for q,s in stored.get('sessions',{}).items()
                         if isinstance(q,str) and isinstance(s,dict) and
                         all(k in s for k in ('seed','mode','owned','seen','current_id','current_uri','room','awaiting_choice','mood','generation','updated','title')) and
                         s['mode'] in ('mood','radio') and isinstance(s['owned'],list)} if isinstance(stored,dict) and stored.get('version')==1 else {}
        self.locks = {}
        for session in self.sessions.values():
            session.setdefault('session_id', uuid.uuid4().hex)
            session.setdefault('discovery_ledger', {'total': 0, 'discovery': 0})
            session.setdefault('owned_categories', {})

    def persist(self):
        if self.save:
            # Writes occur at session changes/queue transitions, never each polling tick.
            self.save({'version':1,'sessions':self.sessions})

    def stop(self,queue,reason='explicit_stop'):
        if self.sessions.pop(queue,None) is not None:
            log.info('Mix session stopped queue=%s reason=%s', queue, reason)
            self.persist()

    def lock(self, queue):
        return self.locks.setdefault(queue, asyncio.Lock())

    async def snapshot(self, queue):
        result = await self.command('mood_snapshot', queue_id=queue)
        if result.get('state') == 'error':
            raise ValueError(result.get('reason', 'Queue unavailable'))
        return result['snapshot']

    async def begin(self, queue, room, seed=None, mode='mood', preserve_upcoming=False):
        if not queue or mode not in ('mood', 'radio'):
            raise ValueError('A queue and valid mix mode are required')
        async with self.lock(queue):
            snap = await self.snapshot(queue)
            playing = current(snap)
            if not playing['id'] or not playing['uri']:
                raise ValueError('Start the seed item before opening its mood mix')
            seed = dict(seed or {})
            media = (snap.get('current_item') or {}).get('media_item') or snap.get('current_item') or {}
            seed.update(uri=playing['uri'], name=playing['title'], duration=playing['duration'] or mix_policy.duration(seed))
            if not mix_policy.duration(seed):
                raise ValueError('The root track needs a known duration before starting a mix')
            if media.get('artists'): seed['artists'] = media['artists']; seed.pop('artist', None)
            session = {'session_id': uuid.uuid4().hex, 'queue_id': queue, 'room': room, 'mode': mode, 'seed': seed or {},
                       'awaiting_choice': mode == 'mood', 'mood': None,
                       'current_id': playing['id'], 'current_uri': playing['uri'], 'seen': [], 'owned': [playing['uri']],
                       'recordings': [seed], 'versions': [seed], 'owned_item_ids': [playing['id']], 'owned_recordings': [seed], 'title': playing['title'], 'updated': time.time(), 'generation': 0}
            session.update(discovery_ledger={'total': 0, 'discovery': 0}, owned_categories={})
            if preserve_upcoming:
                index = item_index(snap)
                tail = (snap.get('items') or [])[index+1:] if isinstance(index,int) else []
                session['owned'] += [str((i.get('media_item') or i).get('uri') or '') for i in tail]
                session['owned_item_ids'] += [str(i.get('queue_item_id') or '') for i in tail]
                session['owned_recordings'] += [i.get('media_item') or i for i in tail]
            self.sessions[queue] = session
            self.persist()  # Persist intent before a potentially interrupted first fill.
            if mode == 'radio':
                await self.fill(queue, snap, append=preserve_upcoming)
            else:
                # Seed collections must wait for a deliberate mood; retain only the playing item.
                result = await self.command('mood_replace_upcoming', queue_id=queue,
                    expected_item_id=playing['id'], expected_index=snap.get('current_index'), media=[])
                if result.get('state') == 'error':
                    self.stop(queue)
                    raise ValueError(result.get('reason', 'Seed queue could not be prepared'))
            if mode != 'radio':
                self.persist()
            return self.public(queue)

    def public(self, queue):
        s = self.sessions.get(queue)
        if not s:
            return {'active': False, 'queue_id': queue}
        return {key: s[key] for key in ('queue_id', 'room', 'mode', 'awaiting_choice', 'mood', 'title', 'updated')} | {
            'active': True, 'pending_refresh': bool(s.get('pending_refresh')),
            'refresh_status': s.get('refresh_status', 'ready'), 'applied_mood': s.get('applied_mood'),
            'discovery_ledger': dict(s.get('discovery_ledger') or {}),
            'last_error': s.get('last_error'), 'next_refill': s.get('next_refill')}

    async def update(self, queue, angle, radius):
        mood = music_mood.selection(angle, radius)
        async with self.lock(queue):
            s = self.sessions.get(queue)
            if not s:
                raise ValueError('This queue has no active mood mix')
            snap = await self.snapshot(queue)
            if not current(snap)['id'] or not current(snap)['uri']:
                raise ValueError('Queue metadata is temporarily unavailable; retry the mood change')
            if not self.owns(s, snap):
                self.stop(queue, 'manual_replacement')
                raise ValueError('Playback changed outside this mix')
            s['mood'], s['awaiting_choice'], s['mode'] = mood, False, 'mood'
            s['generation'] += 1
            s['pending_refresh'] = True
            s['refresh_status'] = 'pending'
            s.pop('retry_after', None)
            s.pop('next_refill', None)
            try:
                await self.fill(queue, snap)
            except (asyncio.TimeoutError, OSError, aiohttp.ClientError) as exc:
                # Steering was accepted/persisted. Existing music keeps playing;
                # the session worker will reconcile and refill from a fresh snapshot.
                s['pending_refresh'] = True
                s['refresh_status'] = 'pending'
                s['last_error'] = 'Provider or queue temporarily unavailable; mood refresh pending'
                log.warning('Mood steering deferred (%s)', type(exc).__name__)
            finally:
                self.persist()  # Failed/racing steering must remain pending after restart.
            return self.public(queue)

    async def fill(self, queue, snap, append=False):
        s = self.sessions.get(queue)
        if not s:
            return
        playing = current(snap)
        s['next_refill'] = time.time()+15  # Bound failures/shortages; manual steering resets this.
        generation = s['generation']
        s.pop('fallback', None)  # Never reuse rankings captured before more recent skips.
        upcoming=[]
        index=item_index(snap)
        if append and isinstance(index, int):
            for item in (snap.get('items') or [])[index+1:]:
                media=item.get('media_item') or item
                if media.get('uri'): upcoming.append(media['uri'])
        exclude = set(s['seen']) | {playing['uri']} | set(upcoming)
        existing = []
        if append and isinstance(index, int):
            existing = [item.get('media_item') or item for item in (snap.get('items') or [])[index+1:]]
        previous = s.get('recordings', []) + existing
        versions = s.get('versions', s.get('recordings', []))
        current_media=(snap.get('current_item') or {}).get('media_item') or snap.get('current_item') or {}
        relation_seed=existing[-1] if append and existing else current_media
        policy = {'mode': s.get('mode', 'mood'), 'queue_id': queue, 'exclude': list(exclude), 'previous': previous, 'versions': versions, 'relation_seed':relation_seed}
        balance = dict(s.get('discovery_ledger') or {'total': 0, 'discovery': 0})
        balance['total'] += len(upcoming)
        balance['discovery'] += sum(s.get('owned_categories', {}).get(u)=='discovery' for u in upcoming)
        policy.update(discovery_balance=balance, steering=not append and bool(s.get('mood')),
                      previous_mood=s.get('applied_mood'))
        picks = await self.recommend(s['room'], 50, s['mood'], s['seed'], policy)
        def eligible(items, blocked, recordings):
            unique = {p['uri']: p for p in items if p.get('uri') and p['uri'] not in blocked
                      and mix_policy.similar_length(p, s['seed'])
                      and not any(p['uri'] != old.get('uri') and mix_policy.same_recording(p, old) for old in versions)}
            return mix_policy.distinct(list(unique.values()), recordings)[:20]
        picks = eligible(picks, exclude, previous)
        if not picks:
            # An endless session can reuse older exact recordings after exhausting
            # fresh candidates, but never alternate versions of the same song.
            blocked = {playing['uri']} | set(upcoming)
            current_media = (snap.get('current_item') or {}).get('media_item') or snap.get('current_item') or {}
            repeat_policy = {'mode': s.get('mode', 'mood'), 'queue_id': queue, 'exclude': list(blocked), 'previous': [current_media] + existing, 'versions': versions, 'relation_seed':relation_seed}
            repeat_policy.update(discovery_balance=balance, steering=policy.get('steering'), previous_mood=policy.get('previous_mood'))
            candidates = await self.recommend(s['room'], 50, s['mood'], s['seed'], repeat_policy)
            last_played = {p.get('uri'): i for i,p in enumerate(s.get('recordings', []))}
            candidates = sorted(candidates, key=lambda p: last_played.get(p.get('uri'), -1))
            picks = eligible(candidates, blocked, [current_media] + existing)
        if not picks:
            if append:
                s['refresh_status'] = 'waiting_for_candidates'
                s['last_error'] = 'No eligible tracks available for continuation'
                return  # Preserve the existing album/tail and retry after 15 seconds.
            if s.get('mood') or s.get('pending_refresh'):
                # A mood change must not silently keep an incompatible old tail.
                latest = await self.snapshot(queue)
                if current(latest)['id'] != playing['id']:
                    s['pending_refresh'] = True
                    s['refresh_status'] = 'pending'
                    return
                result = await self.command('mood_replace_upcoming', queue_id=queue,
                    expected_item_id=playing['id'], expected_index=latest.get('current_index'), media=[])
                if result.get('state') == 'error':
                    s['pending_refresh'] = True
                    raise ValueError(result.get('reason', 'Queue refresh rejected'))
                s['owned'] = [playing['uri']]
                s['pending_refresh'] = True
                s['refresh_status'] = 'insufficient_mood_data' if s.get('mood') else 'insufficient_session_matches'
                s['retry_after'] = time.time()+900
                self.persist()
                reason = 'No mood-compatible tracks' if s.get('mood') else 'No session-compatible tracks'
                raise ValueError(reason+' with sufficient metadata; current song kept, upcoming queue cleared')
            return
        latest = await self.snapshot(queue)
        now = current(latest)
        if s is not self.sessions.get(queue) or generation != s['generation']:
            return
        if now['id'] != playing['id']:
            if not append:
                s['pending_refresh'] = True
            s['refresh_status'] = 'retrying' if append else 'pending'
            return  # The monitor will retry against the new song, never restart it.
        result = await self.command('mood_append_upcoming' if append else 'mood_replace_upcoming', queue_id=queue,
            expected_item_id=now['id'], expected_index=latest.get('current_index'),
            **({'expected_upcoming_ids': [str(i.get('queue_item_id') or '') for i in (snap.get('items') or [])[index+1:]]} if append and isinstance(index,int) else {}),
            media=[p['uri'] for p in picks])
        if result.get('state') == 'error':
            if not append:
                s['pending_refresh'] = True
            raise ValueError(result.get('reason', 'Queue refresh rejected'))
        s['owned'] = [playing['uri']] + upcoming + [p['uri'] for p in picks]
        s['owned_recordings'] = [current_media] + existing + picks
        s['owned_item_ids'] = [playing['id']]
        s.pop('last_error', None)
        categories = s.setdefault('owned_categories', {})
        for pick in picks:
            categories[pick['uri']] = pick.get('_selection_category', 'familiar')
        s['owned_categories'] = {u: categories.get(u, 'familiar') for u in s['owned']}
        s['updated'] = time.time()
        s['pending_refresh'] = False
        s['refresh_status'] = 'ready'
        s['applied_mood'] = s['mood']
        s.pop('retry_after', None)
        self.persist()
        if self.record and s['mood']:
            self.record(s['mood'], picks)

    @staticmethod
    def owns(session, snapshot):
        playing = current(snapshot)
        if playing['uri'] in session['owned'] or playing['id'] in session.get('owned_item_ids', []):
            return True
        # MA can resolve a requested provider URI into a library URI. Compare
        # the recording we actually selected, rather than treating that as a
        # manual replacement. Stable queue IDs survive temporary metadata loss.
        raw = snapshot.get('current_item') or {}
        if not raw:
            index = item_index(snapshot)
            items = snapshot.get('items') or []
            raw = items[index] if isinstance(index, int) and 0 <= index < len(items) else {}
        media = raw.get('media_item') or raw
        return any(mix_policy.same_recording(media, selected) for selected in session.get('owned_recordings', []))

    async def tick(self, queue):
        async with self.lock(queue):
            s = self.sessions.get(queue)
            if not s:
                return
            snap = await self.snapshot(queue)
            playing = current(snap)
            if snap.get('state') not in ('playing', 'paused', 'buffering'):
                # A disconnected phone may be reported idle for hours. Retain
                # its durable mix; only a user resume can restart transport.
                s.setdefault('idle_since', time.time())
                return
            s.pop('idle_since',None)
            if not playing['id'] or not playing['uri']:
                s['refresh_status'] = 'waiting_for_queue_metadata'
                return
            if not self.owns(s, snap):
                self.stop(queue, 'manual_replacement')
                return
            if s.get('refresh_status') == 'waiting_for_queue_metadata':
                s['refresh_status'] = 'pending' if s.get('pending_refresh') else 'ready'
            if playing['uri'] not in s['owned']:
                s['owned'].append(playing['uri'])
            known_ids = set(s.get('owned_item_ids', []))
            for item in snap.get('items') or []:
                media = item.get('media_item') or item
                if media.get('uri') in s['owned'] and item.get('queue_item_id'):
                    known_ids.add(str(item['queue_item_id']))
            known_ids.add(playing['id'])
            s['owned_item_ids'] = sorted(known_ids)[-500:]
            if playing['id'] != s['current_id']:
                s.pop('next_refill', None)  # A real track transition warrants an immediate check.
                old_uri = s.get('current_uri') or s['seed'].get('uri')
                if old_uri:
                    ledger = s.setdefault('discovery_ledger', {'total': 0, 'discovery': 0})
                    ledger['total'] += 1
                    ledger['discovery'] += int(s.get('owned_categories', {}).get(old_uri)=='discovery')
                    s['seen'] = (s['seen'] + [old_uri])[-200:]
                s['current_id'] = playing['id']
                media = (snap.get('current_item') or {}).get('media_item') or snap.get('current_item') or {}
                s['recordings'] = (s['recordings'] + [media])[-200:]
                if not any(p.get('uri') == media.get('uri') for p in s.setdefault('versions', [])):
                    s['versions'].append(media)
                s['current_uri'], s['title'] = playing['uri'], playing['title']
                self.persist()
            s['current_uri'], s['title'] = playing['uri'], playing['title']
            index = item_index(snap)
            upcoming = len(snap.get('items') or []) - index - 1 if isinstance(index, int) else 0
            remaining = playing['duration'] - playing['elapsed']
            if s['awaiting_choice']:
                if snap.get('state') == 'playing' and playing['duration'] > 0 and remaining <= 5:
                    s['awaiting_choice'] = False
                    self.persist()
                    await self.fill(queue, snap)  # None mood means normal Pattern Play.
            elif s.get('pending_refresh') and time.time() >= max(s.get('retry_after', 0), s.get('next_refill', 0)):
                await self.fill(queue, snap, append=False)
            elif not s.get('pending_refresh') and upcoming < 8 and time.time() >= s.get('next_refill', 0):
                await self.fill(queue, snap, append=True)
