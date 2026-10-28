"""Read-only stock YouTube detection, with PIR-gated BS5c warning prompts.

No SDB, TV launch/delete, HA service call or playback command is used here.
State is persisted only on incident/acknowledgement/display transitions.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from urllib.parse import quote

import aiohttp

log = logging.getLogger(__name__)
STOCK_APP_IDS = ('9Ur5IzDKqV.TizenYouTube', '111299001912')
DEFAULT_PIRS = ('binary_sensor.desk_pir', 'binary_sensor.dining_pir', 'binary_sensor.lounge_pir')
KIND = 'youtube_watchdog'


class YouTubeWatchdog:
    def __init__(self, session, config, *, ha_base, headers, load, save, clock=time.time):
        self.session, self.config = session, config
        self.ha_base, self.headers = ha_base.rstrip('/'), headers
        self.load, self.save, self.clock = load, save, clock
        raw = load() or {}
        self.state = {k: raw[k] for k in ('present', 'incident', 'acknowledged', 'last_shown') if k in raw}
        self.state.setdefault('present', False)
        self.state.setdefault('last_shown', 0)
        self.pirs = set(config.get('pir_entities') or DEFAULT_PIRS)
        self.host = str(config.get('host') or '').strip()
        self.enabled = config.get('enabled') is True and bool(self.host)
        self.interval = max(60, int(config.get('poll_seconds', 300)))
        self.repeat = max(120, int(config.get('reminder_seconds', 1800)))
        self.absent_checks = 0
        self.last_check = 0
        self.last_result = 'not_checked'
        self.last_probes = {}
        self.ha_connected = False
        self.last_sensor = ''
        self.last_motion = 0
        self.started = self.clock()
        self.motion = asyncio.Event()
        self.check_lock = asyncio.Lock()
        self.display_lock = asyncio.Lock()

    def persist(self):
        self.save(dict(self.state))

    def observe(self, result):
        """Unknown is never interpreted as removal; require two full 404 passes."""
        self.last_check = self.clock()
        self.last_result = 'present' if result is True else 'absent' if result is False else 'unreachable_or_unknown'
        if result is None:
            self.absent_checks = 0
            return
        if result:
            self.absent_checks = 0
            if not self.state.get('present'):
                self.state.update(present=True, incident='youtube-return-' + uuid.uuid4().hex,
                                  acknowledged=False, last_shown=0)
                self.persist()
                log.warning('Stock YouTube detected; awaiting configured PIR activity')
        else:
            self.absent_checks += 1
            if self.absent_checks >= 2 and self.state.get('present'):
                self.state.update(present=False, acknowledged=False, last_shown=0)
                self.persist()
                log.info('Stock YouTube absent on two consecutive checks')

    async def check(self):
        async with self.check_lock:
            unknown = False
            self.last_probes = {}
            for app_id in STOCK_APP_IDS:
                try:
                    url = f'http://{self.host}:8001/api/v2/applications/{quote(app_id, safe="")}'
                    async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=4),
                                                allow_redirects=False) as response:
                        self.last_probes[app_id] = response.status
                        if response.status == 404:
                            continue
                        if response.status != 200:
                            unknown = True
                            continue
                        data = await response.json(content_type=None)
                        # Exact stock IDs only. The replacement has the same display
                        # name but a different ID; embedded firmware stubs are ignored.
                        if (isinstance(data, dict) and data.get('id') in STOCK_APP_IDS
                                and str(data.get('name', '')).casefold() == 'youtube'):
                            self.observe(True)
                            return True
                        unknown = True
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                    self.last_probes[app_id] = type(exc).__name__
                    unknown = True
            result = None if unknown else False
            self.observe(result)
            return result

    def acknowledge(self, incident):
        if incident != self.state.get('incident') or not self.state.get('present'):
            return False
        if not self.state.get('acknowledged'):
            self.state['acknowledged'] = True
            self.persist()
        return True

    def note_state(self, entity, old, new, changed=None):
        if entity not in self.pirs or new != 'on' or old == 'on':
            return False
        now = self.clock()
        # Ignore stale snapshots/replayed old events, but permit short PIR pulses.
        stamp = float(changed) if changed is not None else now
        if stamp < self.started or now - stamp > 60 or stamp > now + 5:
            return False
        self.last_sensor, self.last_motion = entity, now
        self.motion.set()
        return True

    def prompt(self):
        return {'id': self.state.get('incident'), 'kind': KIND, 'priority': 'warning',
                'question': 'Samsung’s original YouTube app has returned on The Frame. '
                            'Remove it and check the TV’s router blocks.',
                'options': [{'id': 'dismiss', 'label': 'Got it'}], 'timeout_seconds': 90}

    async def _post(self, url, payload):
        async with self.session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=4)) as response:
            if response.status != 200:
                raise RuntimeError(f'Warning delivery HTTP {response.status}')
            data = await response.json(content_type=None)
            if data.get('status') == 'error':
                raise RuntimeError('Warning delivery rejected')

    async def notify(self):
        async with self.display_lock:
            now = self.clock()
            if (not self.state.get('present') or self.state.get('acknowledged')
                    or not self.last_motion or now - self.last_motion > 60
                    or (self.state.get('last_shown') and now - self.state['last_shown'] < self.repeat)):
                return False
            # If a recent read explicitly found absence, wait for confirmation;
            # TV-off/unknown does not erase an already verified pending incident.
            if self.last_result == 'absent':
                return False
            await self._post('http://127.0.0.1:8770/router/broadcast',
                             {'type': 'context_suggestion', 'data': self.prompt()})
            # Panel-only wake: no route change, TV power, amplifier or player action.
            await self._post('http://127.0.0.1:8767/webhook', {'command': 'display_on'})
            self.state['last_shown'] = now
            self.persist()
            log.warning('Stock YouTube warning displayed after %s', self.last_sensor)
            return True

    async def poll_loop(self):
        retry = 30
        while True:
            try:
                was_present = self.state.get('present')
                await self.check()
                if was_present and not self.state.get('present'):
                    await self._post('http://127.0.0.1:8770/router/broadcast',
                        {'type': 'context_suggestion', 'data': {'kind': KIND, 'clear': True,
                                                             'id': self.state.get('incident')}})
                # Motion shortly before the positive poll may still be relevant.
                await self.notify()
            except Exception:
                log.exception('YouTube watchdog check/delivery failed; retrying next interval')
            if self.last_result == 'unreachable_or_unknown':
                await asyncio.sleep(min(retry, self.interval))
                retry = min(self.interval, retry * 2)
            else:
                retry = 30
                await asyncio.sleep(self.interval)

    async def motion_loop(self):
        while True:
            await self.motion.wait()
            self.motion.clear()
            try:
                # Refresh after a PIR if the regular check is old; one pending
                # motion event is enough, so a busy sensor cannot grow a queue.
                if self.clock() - self.last_check > 60:
                    await self.check()
                await self.notify()
            except Exception:
                log.exception('YouTube watchdog motion warning failed')

    async def ha_loop(self):
        """Subscribe to edges so a short PIR pulse is not missed by polling."""
        backoff = 5
        while True:
            try:
                url = self.ha_base.removesuffix('/api') + '/api/websocket'
                url = url.replace('https://', 'wss://', 1).replace('http://', 'ws://', 1)
                async with self.session.ws_connect(url, heartbeat=30, timeout=10) as ws:
                    await ws.receive_json(timeout=10)
                    token = self.headers.get('Authorization', '').removeprefix('Bearer ')
                    await ws.send_json({'type': 'auth', 'access_token': token})
                    if (await ws.receive_json(timeout=10)).get('type') != 'auth_ok':
                        raise RuntimeError('HA authentication rejected')
                    await ws.send_json({'id': 1, 'type': 'subscribe_events', 'event_type': 'state_changed'})
                    await ws.send_json({'id': 2, 'type': 'get_states'})
                    self.ha_connected = True
                    log.info('YouTube watchdog connected to HA; watching %d PIR sensors', len(self.pirs))
                    backoff = 5
                    async for message in ws:
                        if message.type != aiohttp.WSMsgType.TEXT:
                            continue
                        data = message.json()
                        if data.get('type') == 'result':
                            if data.get('success') is False:
                                raise RuntimeError('HA state subscription rejected')
                            if data.get('id') == 2:
                                for state in data.get('result') or []:
                                    self._ha_state(state, None)
                        elif data.get('type') == 'event':
                            event = data.get('event', {}).get('data', {})
                            self._ha_state(event.get('new_state'), event.get('old_state'))
            except Exception as exc:
                log.warning('YouTube watchdog HA subscription unavailable (%s); retrying in %ss',
                            type(exc).__name__, backoff)
            self.ha_connected = False
            await asyncio.sleep(backoff)
            backoff = min(300, backoff * 2)

    def _ha_state(self, state, old):
        if not isinstance(state, dict) or state.get('entity_id') not in self.pirs:
            return
        try:
            from datetime import datetime
            stamp = datetime.fromisoformat(state['last_changed'].replace('Z', '+00:00')).timestamp()
        except (KeyError, TypeError, ValueError):
            return
        self.note_state(state['entity_id'], (old or {}).get('state'), state.get('state'), stamp)

    def status(self):
        return {**self.state, 'enabled': self.enabled, 'last_check': self.last_check,
                'last_result': self.last_result, 'last_probes': dict(self.last_probes), 'ha_connected': self.ha_connected, 'pir_entities': sorted(self.pirs),
                'last_sensor': self.last_sensor, 'poll_seconds': self.interval}
