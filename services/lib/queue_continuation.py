"""Default song-radio continuation for ordinary MA music queues, never transport start."""
import asyncio
import time
from .mood_mix import current, item_index
from . import mix_policy

class QueueContinuation:
    def __init__(self, api, mixes, music_item, room, enabled=True, load=None, save=None):
        self.api, self.mixes, self.music_item, self.room = api, mixes, music_item, room
        self.enabled, self.save = enabled, save
        self.last_scan, self.last_error = 0, None
        stored = load() if load else {}
        self.blocked = dict(stored) if isinstance(stored, dict) else {}

    async def head(self, queue):
        result = await self.api('player_queues/items', {'queue_id':queue, 'offset':0, 'limit':1})
        items = result if isinstance(result,list) else result.get('items',[])
        return str(items[0].get('queue_item_id') or '') if items else ''

    async def block(self, queue):
        self.blocked[queue] = await self.head(queue)
        if self.save:self.save(self.blocked)

    def unblock(self, queue):
        if self.blocked.pop(queue,None) is not None and self.save:self.save(self.blocked)

    @staticmethod
    def list_items(payload):
        return payload if isinstance(payload,list) else payload.get('items',[])

    def eligible(self, snapshot):
        if snapshot.get('state') != 'playing' or snapshot.get('repeat_mode','off') not in ('off',None):return False
        index = item_index(snapshot)
        items = snapshot.get('items') or []
        if not isinstance(index,int) or index < 0:return False
        tail = items[index:]
        if not tail or len(tail)>8:return False
        playing = current(snapshot)
        if not playing['id'] or not playing['uri'] or playing['duration']<=0:return False
        # News/podcasts/video/spoken audio queues never become song radio.
        return all(self.music_item(i.get('media_item') or i) and
                   mix_policy.duration(i.get('media_item') or i)>0 for i in tail)

    async def check(self):
        if not self.enabled:return
        queues = self.list_items(await self.api('player_queues/all',{}))
        self.last_scan, self.last_error = time.time(), None
        # Canonical queue owners only. Group members with empty queues are skipped.
        for queue in queues:
            q = str(queue.get('queue_id') or '')
            if not q or q in self.mixes.sessions or queue.get('state')!='playing':continue
            if isinstance(queue.get('items'),int):
                if queue['items']==0:continue
                index=queue.get('current_index')
                if isinstance(index,int) and queue['items']-index>8:continue
            try:
                snapshot = await self.mixes.snapshot(q)
                owner = str(snapshot.get('resolved_queue_id') or q)
                if owner != q or owner in self.mixes.sessions or not self.eligible(snapshot):continue
                if q in self.blocked:
                    head = await self.head(q)
                    if not head or head==self.blocked[q]:continue
                    self.unblock(q)
                # Timer state must be verified before adopting any queue. MA stops
                # playback itself; this service never resumes an idle/paused player.
                player = str(queue.get('player_id') or q)
                timer = await self.api('players/sleep_timer/get',{'player_id':player})
                if timer not in (None,0,False) and float(timer)>time.time():continue
                await self.mixes.begin(q, self.room(q), mode='radio', preserve_upcoming=True)
            except asyncio.CancelledError:raise
            except Exception:
                # A single unavailable queue must not prevent discovery elsewhere.
                import logging
                logging.getLogger(__name__).exception('Automatic queue continuation failed queue=%s',q)

    async def run(self):
        while True:
            try:await self.check()
            except asyncio.CancelledError:raise
            except Exception as exc:
                self.last_error = type(exc).__name__
                import logging
                logging.getLogger(__name__).exception('Automatic queue discovery unavailable')
            await asyncio.sleep(5)
