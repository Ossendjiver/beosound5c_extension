"""Bounded network adapters for playback_jobs, sharing beo-library's HTTP session."""
from __future__ import annotations
import asyncio
import hmac
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
import aiohttp
from aiohttp import web
from .config import cfg
from .playback_jobs import Coordinator, Store, InvalidIntent, Conflict, Uncertain

class HALease:
    def __init__(self, session, base, headers, background):
        self.session, self.base, self.headers = session, base, headers; self.lost = set(); self.background=background
    async def call(self, action, owner):
        async with self.session.post(self.base+'/home_media_bridge/routing-lease',headers=self.headers,
            json={"action":action,"owner":owner},timeout=aiohttp.ClientTimeout(total=5)) as response:
            if response.status != 200: raise Uncertain("HA routing lease is busy/unavailable")
            data = await response.json()
            if action != 'release' and (not data.get('active') or data.get('owner') != owner): raise Uncertain("HA lease ownership changed")
            return data
    async def check(self, owner):
        if owner in self.lost: raise Uncertain("HA routing lease renewal failed")
        await self.call('renew',owner)
    @asynccontextmanager
    async def hold(self, owner):
        self.lost.discard(owner); await self.call('acquire',owner)
        async def renew():
            try:
                while True: await asyncio.sleep(10); await self.call('renew',owner)
            except asyncio.CancelledError: raise
            except Exception: self.lost.add(owner)
        task = self.background.spawn(renew(),name="playback_ha_lease")
        try: yield
        finally:
            task.cancel(); await asyncio.gather(task,return_exceptions=True)
            try: await asyncio.shield(self.call('release',owner))
            except Exception: pass  # HA-local TTL releases only this owner's lease.

class Runtime:
    def __init__(self, service, session, ha_base, ha_headers):
        self.service, self.session = service, session
        self.ha_base,self.ha_headers = ha_base,ha_headers
        self.key = os.getenv('BS5C_COORDINATOR_KEY','')
        if len(self.key)<32:raise ValueError('A dedicated coordinator key is required')
        configured=(os.getenv('MASS_WS_URL') or os.getenv('BS5C_MASS_WS_URL') or '').strip()
        host=(os.getenv('PLAYER_IP') or cfg('player','ip',default='localhost')).strip()
        self.ma_base=configured.replace('wss://','https://').replace('ws://','http://').rstrip('/').removesuffix('/ws') if configured else f'http://{host}:8095'
        token=os.getenv('MASS_TOKEN','').strip()
        self.ma_headers={'Authorization':'Bearer '+token} if token else {}
        path = Path(os.getenv('BS5C_PLAYBACK_ROUTES','/etc/beosound5c/playback-routing.json'))
        routes = json.loads(path.read_text())['rooms']
        self.entities = {route.get('entity') for entries in routes.values() for route in entries.values() if route.get('entity')}
        self.entities.update({'input_boolean.homemedia_routing_active','binary_sensor.home_media_bridge_routing_active','input_boolean.kodi_mass_interlock'})
        self.state_slots = asyncio.Semaphore(4)
        self.lease = HALease(session,ha_base,ha_headers,service._background)
        self.coordinator = Coordinator(Store(service.model.db),routes,self.command,self.ha_states,self.actuator,self.lease,audio_sync=self.sync_audio)
    async def command(self, name, args):
        # A new deadline on this adapter, not the library's 12-second session default.
        async with self.session.post(self.ma_base+'/api',headers=self.ma_headers,
            json={'command':name,'args':args},timeout=aiohttp.ClientTimeout(total=90 if name=='player_queues/transfer' else 15)) as response:
            response.raise_for_status(); value=await response.json()
            if isinstance(value,dict) and ('error_code' in value or value.get('error')): raise Uncertain('MA rejected the operation')
            return value
    async def ha_states(self):
        # Do not fetch HA's 1,000+ entities for each queue confirmation tick.
        async def read(entity):
            async with self.state_slots:
                async with self.session.get(self.ha_base+'/states/'+entity,headers=self.ha_headers,timeout=aiohttp.ClientTimeout(total=8)) as response:
                    if response.status == 404: return None
                    response.raise_for_status(); return await response.json()
        return [state for state in await asyncio.gather(*(read(entity) for entity in sorted(self.entities))) if state]
    async def sync_audio(self, owner, room):
        # Reuse HA's existing output/EQ policy under this job's lease, after the
        # final room power state is confirmed. Never ask this sync to start music.
        zone={"bedroom":"bed","bathroom":"bl3500"}.get(room,room)
        async with self.session.post(self.ha_base+'/services/script/beosystem_sync_link_audio',headers=self.ha_headers,
            json={"routing_owner":owner,"preferred_zone":zone,"ensure_playback":False,"update_history":True,"playback_reason":"handoff"},
            timeout=aiohttp.ClientTimeout(total=20)) as response:
            response.raise_for_status();await response.read()

    async def actuator(self, action, entity, data):
        if action not in {'select_source','turn_off'}: raise InvalidIntent('Unsupported hardware action')
        async with self.session.post(self.ha_base+'/services/media_player/'+action,headers=self.ha_headers,
            json={'entity_id':entity,**data},timeout=aiohttp.ClientTimeout(total=10)) as response:
            response.raise_for_status(); await response.read()
    def authorize(self, request):
        supplied=request.headers.get('X-HomeMedia-Key','')
        if len(self.key)<32 or not hmac.compare_digest(supplied,self.key): raise web.HTTPUnauthorized()
    async def jobs(self, request):
        self.authorize(request)
        try:
            if request.method=='POST':
                result=self.coordinator.submit(await request.json())
                return web.json_response(result,status=202,headers={'Cache-Control':'no-store'})
            job_id=request.query.get('job_id','')
            if set(request.query)-{'job_id'} or not job_id:raise InvalidIntent('job_id is required')
            result=self.coordinator.store.get(job_id)
            if not result: raise web.HTTPNotFound()
            return web.json_response(result,headers={'Cache-Control':'no-store'})
        except Conflict as error:return web.json_response({'error':str(error)},status=409)
        except (InvalidIntent,ValueError,TypeError) as error:return web.json_response({'error':str(error)},status=400)
    async def state(self, request):
        self.authorize(request)
        if request.query:raise web.HTTPBadRequest()
        try:
            snapshot=await self.coordinator.snapshot.get()
            # Raw HA state is deliberately kept local; expose the configured rooms only.
            from .playback_jobs import Route, queue_for
            value={'timestamp':snapshot['timestamp'],'job_id':self.coordinator.current,'rooms':[],'active_queues':[]}
            for queue in snapshot['queues']:
                if queue.get('state') in {'playing','paused','buffering'}:
                    item=queue.get('current_item') or {}
                    value['active_queues'].append({'queue_id':queue['queue_id'],'state':queue.get('state'),'item_id':item.get('queue_item_id'),'title':item.get('name'),'items':queue.get('items')})
            for room, entries in self.coordinator.routes.items():
                players=[]
                for key,route in entries.items():
                    hardware=snapshot['ha'].get(route.get('entity'),{})
                    try:
                        queue=queue_for(Route(**route,room=room),snapshot)
                        item=queue.get('current_item') or {}
                        powered=hardware.get('state') not in {None,'off','unknown','unavailable'} if route.get('entity') else queue.get('state') in {'playing','paused','buffering'}
                        listening=powered and (not route.get('entity') or hardware.get('attributes',{}).get('source') in (route.get('source'),*route.get('alternate_sources',[])))
                        players.append({'key':key,'queue_id':queue['queue_id'],'state':queue.get('state') if listening else hardware.get('state','off'),
                            'hardware_state':hardware.get('state'),'source':hardware.get('attributes',{}).get('source'),
                            'title':item.get('name') if listening else '', 'queue_items':queue.get('items'),
                            'can_join':not powered and bool(value['active_queues'])})
                    except Exception:players.append({'key':key,'available':False,'can_join':False})
                value['rooms'].append({'room':room,'players':players})
            return web.json_response(value,headers={'Cache-Control':'no-store'})
        except Exception:return web.json_response({'available':False},status=503)
