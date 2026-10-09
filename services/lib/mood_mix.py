"""Server-owned live mood sessions; never restart the current queue item."""
import asyncio
import time
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
    def __init__(self, command, recommend, record=None):
        self.command, self.recommend, self.record = command, recommend, record
        self.sessions = {}
        self.locks = {}

    def lock(self, queue):
        return self.locks.setdefault(queue, asyncio.Lock())

    async def snapshot(self, queue):
        result = await self.command('mood_snapshot', queue_id=queue)
        if result.get('state') == 'error':
            raise ValueError(result.get('reason', 'Queue unavailable'))
        return result['snapshot']

    async def begin(self, queue, room, seed=None, mode='mood'):
        if not queue or mode not in ('mood', 'radio'):
            raise ValueError('A queue and valid mix mode are required')
        async with self.lock(queue):
            snap = await self.snapshot(queue)
            playing = current(snap)
            if not playing['id'] or not playing['uri']:
                raise ValueError('Start the seed item before opening its mood mix')
            seed = dict(seed or {})
            media = (snap.get('current_item') or {}).get('media_item') or snap.get('current_item') or {}
            seed.update(uri=playing['uri'], name=playing['title'], duration=playing['duration'])
            if media.get('artists'): seed['artists'] = media['artists']; seed.pop('artist', None)
            session = {'queue_id': queue, 'room': room, 'mode': mode, 'seed': seed or {},
                       'awaiting_choice': mode == 'mood', 'mood': None,
                       'current_id': playing['id'], 'current_uri': playing['uri'], 'seen': [], 'owned': [playing['uri']],
                       'recordings': [seed], 'versions': [seed], 'title': playing['title'], 'updated': time.time(), 'generation': 0}
            self.sessions[queue] = session
            if mode == 'radio':
                await self.fill(queue, snap)
            else:
                # Seed collections must wait for a deliberate mood; retain only the playing item.
                result = await self.command('mood_replace_upcoming', queue_id=queue,
                    expected_item_id=playing['id'], expected_index=item_index(snap), media=[])
                if result.get('state') == 'error':
                    self.sessions.pop(queue, None)
                    raise ValueError(result.get('reason', 'Seed queue could not be prepared'))
                session['fallback'] = await self.recommend(room, 50, None, session['seed'])
            return self.public(queue)

    def public(self, queue):
        s = self.sessions.get(queue)
        if not s:
            return {'active': False, 'queue_id': queue}
        return {key: s[key] for key in ('queue_id', 'room', 'mode', 'awaiting_choice', 'mood', 'title', 'updated')} | {'active': True}

    async def update(self, queue, angle, radius):
        mood = music_mood.selection(angle, radius)
        async with self.lock(queue):
            s = self.sessions.get(queue)
            if not s:
                raise ValueError('This queue has no active mood mix')
            snap = await self.snapshot(queue)
            if current(snap)['uri'] not in s['owned']:
                self.sessions.pop(queue, None)
                raise ValueError('Playback changed outside this mix')
            s['mood'], s['awaiting_choice'], s['mode'] = mood, False, 'mood'
            s['generation'] += 1
            await self.fill(queue, snap)
            return self.public(queue)

    async def fill(self, queue, snap, append=False):
        s = self.sessions.get(queue)
        if not s:
            return
        playing = current(snap)
        generation = s['generation']
        fallback = s.pop('fallback', None) if not s['mood'] else None
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
        policy = {'exclude': list(exclude), 'previous': previous, 'versions': versions}
        picks = fallback if fallback is not None else await self.recommend(s['room'], 50, s['mood'], s['seed'], policy)
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
            repeat_policy = {'exclude': list(blocked), 'previous': [current_media] + existing, 'versions': versions}
            candidates = await self.recommend(s['room'], 50, s['mood'], s['seed'], repeat_policy)
            last_played = {p.get('uri'): i for i,p in enumerate(s.get('recordings', []))}
            candidates = sorted(candidates, key=lambda p: last_played.get(p.get('uri'), -1))
            picks = eligible(candidates, blocked, [current_media] + existing)
        if not picks:
            # Do not wipe a useful queue when the library temporarily fails or has no candidates.
            return
        latest = await self.snapshot(queue)
        now = current(latest)
        if s is not self.sessions.get(queue) or generation != s['generation']:
            return
        if now['id'] != playing['id']:
            return  # The monitor will retry against the new song, never restart it.
        result = await self.command('mood_replace_upcoming', queue_id=queue,
            expected_item_id=now['id'], expected_index=latest.get('current_index'),
            media=upcoming+[p['uri'] for p in picks])
        if result.get('state') == 'error':
            raise ValueError(result.get('reason', 'Queue refresh rejected'))
        s['owned'] = [playing['uri']] + upcoming + [p['uri'] for p in picks]
        s['updated'] = time.time()
        if self.record and s['mood']:
            self.record(s['mood'], picks)

    async def tick(self, queue):
        async with self.lock(queue):
            s = self.sessions.get(queue)
            if not s:
                return
            snap = await self.snapshot(queue)
            playing = current(snap)
            if snap.get('state') not in ('playing', 'paused', 'buffering'):
                s['idle_ticks'] = s.get('idle_ticks', 0) + 1
                if s['idle_ticks'] >= 5:
                    self.sessions.pop(queue, None)
                return
            s['idle_ticks'] = 0
            if playing['uri'] not in s['owned']:
                self.sessions.pop(queue, None)
                return
            if playing['id'] != s['current_id']:
                old_uri = s.get('current_uri') or s['seed'].get('uri')
                if old_uri:
                    s['seen'] = (s['seen'] + [old_uri])[-200:]
                s['current_id'] = playing['id']
                media = (snap.get('current_item') or {}).get('media_item') or snap.get('current_item') or {}
                s['recordings'] = (s['recordings'] + [media])[-200:]
                if not any(p.get('uri') == media.get('uri') for p in s.setdefault('versions', [])):
                    s['versions'].append(media)
            s['current_uri'], s['title'] = playing['uri'], playing['title']
            index = item_index(snap)
            upcoming = len(snap.get('items') or []) - index - 1 if isinstance(index, int) else 0
            remaining = playing['duration'] - playing['elapsed']
            if s['awaiting_choice']:
                if snap.get('state') == 'playing' and playing['duration'] > 0 and remaining <= 5:
                    s['awaiting_choice'] = False
                    await self.fill(queue, snap)  # None mood means normal Pattern Play.
            elif upcoming < 5:
                await self.fill(queue, snap, append=True)
