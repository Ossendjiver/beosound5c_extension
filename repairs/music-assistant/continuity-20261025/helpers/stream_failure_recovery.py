"""Bounded recovery of a confirmed Tidal failure at an unchanged queue's EOF.

This is deliberately not an idle-player watchdog. Unknown idle states, early
stops, pauses, source changes and timers cannot start transport here.
"""
from __future__ import annotations
import asyncio
import time

class StreamFailureRecovery:
    def __init__(self, host, *, sleep=asyncio.sleep, clock=time.monotonic):
        self.host, self.sleep, self.clock = host, sleep, clock
        self.pending = {}
        self.budget = {}

    def cancel(self, queue_id):
        task = self.pending.pop(queue_id, None)
        if task and task is not asyncio.current_task():
            task.cancel()
        self.budget.pop(queue_id, None)

    def arm(self, queue_id, item_id, session_id):
        if queue_id in self.pending or not session_id:
            return
        data = self.host._queue_data.get(queue_id)
        item = self.host.get_item(queue_id, item_id)
        if not data or data.session_id != session_id or not item or str(item.media_type) != 'track':
            return
        details = item.streamdetails
        if not details or details.stream_error is not True or not str(details.provider).startswith('tidal'):
            return
        index = self.host.index_by_id(queue_id, item_id)
        if index is None:
            return
        successor = self.host.get_item(queue_id, index + 1)
        if successor is None:
            return
        now = self.clock()
        start, used = self.budget.get(queue_id, (now, set()))
        if now - start > 900:
            start, used = now, set()
        if item_id in used or len(used) >= 3:
            return
        self.budget[queue_id] = start, used
        task = self.host.mass.create_task(self._watch(queue_id, item_id, successor.queue_item_id, session_id))
        self.pending[queue_id] = task
        def finished(done):
            if self.pending.get(queue_id) is done:
                self.pending.pop(queue_id, None)
            if not done.cancelled():
                error = done.exception()
                if error:
                    self.host.logger.warning('Stream failure recovery aborted queue=%s: %s', queue_id, type(error).__name__)
        task.add_done_callback(finished)

    def inspect(self, queue_id, item_id, successor_id, session_id):
        data = self.host._queue_data.get(queue_id)
        if not data or data.session_id != session_id:
            return None
        queue = data.queue
        player = self.host.mass.players.get_player(queue_id)
        if (not queue.active or queue.flow_mode or str(queue.repeat_mode) != 'off' or not player
            or not player.state.available or player.state.powered is not True or player.stop_called
            or player.state.active_source != queue_id
            or str(player.state.playback_state) not in ('playing', 'buffering', 'idle')
            or str(queue.state) not in ('playing', 'buffering', 'idle')
            or self.host.mass.players.get_sleep_timer(queue_id) is not None):
            return None
        failed_index = self.host.index_by_id(queue_id, item_id)
        successor = self.host.get_item(queue_id, failed_index + 1) if failed_index is not None else None
        if (failed_index is None or queue.current_index is None or queue.current_index > failed_index
            or not successor or successor.queue_item_id != successor_id):
            return None
        failed = self.host.get_item(queue_id, item_id)
        if not failed or not failed.streamdetails or failed.streamdetails.stream_error is not True:
            return None  # fresh successful stream details supersede an old failure
        if str(queue.state) != 'idle' or str(player.state.playback_state) != 'idle' or not queue.current_item or queue.current_item.queue_item_id != item_id:
            return False  # still playing/buffering, or the failed prefetch is not current yet
        duration = queue.current_item.duration
        if not duration or queue.elapsed_time < duration - 2:
            return None  # an early/ambiguous stop is never automatically resumed
        if not successor.available:
            return None
        return successor

    async def _watch(self, queue_id, item_id, successor_id, session_id):
        for _ in range(900):
            await self.sleep(1)
            result = self.inspect(queue_id, item_id, successor_id, session_id)
            if result is None:
                return
            if result is False:
                continue
            async with self.host.mass.players.get_group_and_player_lock(queue_id):
                target = self.inspect(queue_id, item_id, successor_id, session_id)
                if target is None or target is False:
                    return
                # Never reuse a failed or expired prewarmed provider URL.
                if target.streamdetails and (buffer := target.streamdetails.buffer):
                    await asyncio.wait_for(buffer.clear(), timeout=5)
                    target = self.inspect(queue_id, item_id, successor_id, session_id)
                    if target is None or target is False:
                        return
                target.streamdetails = None
                self.budget[queue_id][1].add(item_id)  # consume before dispatch; never replay a lost acknowledgement
                self.host.logger.warning('Recovering confirmed failed stream queue=%s item=%s next=%s', queue_id, item_id, successor_id)
                await self.host.play_index(queue_id, successor_id)
                return
