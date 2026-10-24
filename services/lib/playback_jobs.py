"""Durable, one-shot home playback intents, hosted by the existing library service.

No network I/O happens on submission. Queue mutations are never replayed after a
transport error or service restart. HA owns the cross-process routing lease.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import re
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

TERMINAL = {"succeeded", "failed", "needs_review", "interrupted"}
OPERATIONS = {"transfer", "join", "leave"}
UUID = re.compile(r"[A-Za-z0-9_-]{16,96}\Z")
PHONE = re.compile(r"homemedia-[a-fA-F0-9-]{36}\Z")

class InvalidIntent(ValueError): pass
class Conflict(ValueError): pass
class Uncertain(RuntimeError): pass
class DispatchUncertain(Uncertain): pass

@dataclass(frozen=True)
class Route:
    player_id: str
    entity: str = ""
    source: str = ""
    shared: bool = False
    alternate_sources: tuple = ()
    room: str = ""

class Store:
    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self.db.execute("CREATE TABLE IF NOT EXISTS playback_jobs (id TEXT PRIMARY KEY, digest TEXT NOT NULL, request TEXT NOT NULL, status TEXT NOT NULL, phase TEXT NOT NULL, result TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL)")
        self.db.commit()
    def get(self, job_id):
        row = self.db.execute("SELECT id,request,status,phase,result,created,updated FROM playback_jobs WHERE id=?", (job_id,)).fetchone()
        if not row: return None
        return dict(zip(("job_id", "request", "status", "phase", "result", "created", "updated"),
            (row[0], json.loads(row[1]), row[2], row[3], json.loads(row[4]), row[5], row[6])))
    def submit(self, intent):
        body = json.dumps(intent, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(body.encode()).hexdigest()
        row = self.db.execute("SELECT digest FROM playback_jobs WHERE id=?", (intent["request_id"],)).fetchone()
        if row:
            if row[0] != digest: raise Conflict("Request ID was already used with a different intent")
            return self.get(intent["request_id"])
        if self.db.execute("SELECT count(*) FROM playback_jobs WHERE status NOT IN ('succeeded','failed','needs_review','interrupted')").fetchone()[0] >= 32:
            raise Conflict("Playback job queue is full")
        now = time.time()
        self.db.execute("INSERT INTO playback_jobs VALUES (?,?,?,?,?,?,?,?)", (intent["request_id"], digest, body, "queued", "accepted", "{}", now, now))
        self.db.commit()
        return self.get(intent["request_id"])
    def update(self, job_id, status, phase, **result):
        self.db.execute("UPDATE playback_jobs SET status=?,phase=?,result=?,updated=? WHERE id=?", (status, phase, json.dumps(result), time.time(), job_id))
        self.db.commit()
    def pending(self):
        return [row[0] for row in self.db.execute("SELECT id FROM playback_jobs WHERE status='queued' ORDER BY created")]
    def recover(self):
        # A running job may have crossed a network mutation boundary. Never replay it.
        self.db.execute("UPDATE playback_jobs SET status='interrupted',phase='reconcile_required',result=?,updated=? WHERE status='running'", (json.dumps({"message": "Service restarted; inspect source/destination before another command"}), time.time()))
        self.db.commit()
    def prune(self):
        # Retain compact idempotency receipts permanently; trim old detail only.
        self.db.execute("UPDATE playback_jobs SET request='{}',result='{}' WHERE updated<? AND status IN ('succeeded','failed','needs_review','interrupted') AND request!='{}'", (time.time()-7*86400,))
        self.db.commit()

class Snapshot:
    """One coalesced MA read for every client; use fresh=True at mutation boundaries."""
    def __init__(self, command, ha_states, ttl=2.0):
        self.command, self.ha_states, self.ttl = command, ha_states, ttl
        self.lock = asyncio.Lock(); self.value = None; self.timestamp = 0.0
    async def get(self, fresh=False):
        async with self.lock:
            if not fresh and self.value is not None and time.monotonic()-self.timestamp < self.ttl:
                return self.value
            players, queues, states = await asyncio.gather(
                self.command("players/all", {"include_protocol_players": True}),
                self.command("player_queues/all", {}), self.ha_states())
            if not isinstance(players, list) or not isinstance(queues, list) or not isinstance(states, list) or not states:
                raise Uncertain("Fresh MA and HA state is required")
            self.value = {"players": players, "queues": queues, "ha": {s["entity_id"]: s for s in states}, "timestamp": time.time()}
            self.timestamp = time.monotonic()
            return self.value

def canonical(player_id, players):
    indexed = {p["player_id"]: p for p in players}; seen = set()
    while player_id in indexed:
        if player_id in seen: raise Uncertain("Cyclic MA player topology")
        seen.add(player_id); p = indexed[player_id]
        parent = p.get("protocol_parent_id")
        if not parent:
            for other in players:
                protocols = other.get("linked_output_protocols") or other.get("output_protocols") or []
                ids = [x if isinstance(x, str) else x.get("output_protocol_id", x.get("player_id", "")) for x in protocols]
                if player_id in ids: parent = other["player_id"]; break
        if not parent: return p
        player_id = parent
    raise Uncertain("Configured MA player is missing")

def leader(player_id, snapshot):
    p = canonical(player_id, snapshot["players"]); seen = set()
    while p["player_id"] not in seen:
        seen.add(p["player_id"])
        parent = p.get("active_group") or p.get("synced_to")
        if not parent or parent == p["player_id"]:
            # Some MA providers publish group_members without a child synced_to pointer.
            parents = {canonical(other["player_id"], snapshot["players"])["player_id"]
                       for other in snapshot["players"]
                       if other["player_id"] != p["player_id"] and p["player_id"] in (other.get("group_members") or [])}
            parents.discard(p["player_id"])
            if len(parents) > 1: raise Uncertain("Ambiguous MA group ownership")
            parent = next(iter(parents), None)
            if not parent: return p
        p = canonical(parent, snapshot["players"])
    raise Uncertain("Cyclic MA group topology")

def require_music(player_id, snapshot):
    """Reject Kodi behind renamed/grouped/protocol players before any mutation."""
    players = {p["player_id"]: p for p in snapshot["players"]}
    visited = set()
    def visit(pid):
        if pid in visited: return
        visited.add(pid)
        player = players.get(pid)
        if player is None: raise Uncertain("Music player topology is incomplete")
        identity = " ".join(str(player.get(k, "")) for k in ("player_id", "provider", "name", "display_name", "device_info"))
        if "kodi" in identity.casefold(): raise InvalidIntent("Kodi cannot be a music target")
        for key in ("group_members", "static_group_members"):
            for member in player.get(key) or []: visit(member)
        if player.get("protocol_parent_id"): visit(player["protocol_parent_id"])
        protocol = player.get("active_output_protocol")
        if protocol:
            # MA protocols can be owner-local descriptors (including "native"),
            # rather than entries returned by players/all.
            descriptors = player.get("output_protocols") or []
            if not isinstance(descriptors, list): raise Uncertain("Invalid output protocol descriptors")
            matches = [p for p in descriptors if isinstance(p, dict) and p.get("output_protocol_id") == protocol]
            if len(matches) > 1: raise Uncertain("Ambiguous output protocol")
            if matches:
                if "kodi" in " ".join(str(v) for v in matches[0].values()).casefold():
                    raise InvalidIntent("Kodi cannot be a music target")
            if protocol in players: visit(protocol)
            elif not matches: raise Uncertain("Music output protocol is not advertised by its owner")
    visit(player_id)
    visit(canonical(player_id, snapshot["players"])["player_id"])

def queue_for(route, snapshot, *, own=False):
    p = canonical(route.player_id, snapshot["players"]) if own else leader(route.player_id, snapshot)
    if p.get("available") is not True or p.get("needs_setup"): raise Uncertain("Player unavailable or requires setup")
    queues = {q["queue_id"]: q for q in snapshot["queues"]}
    for candidate in [p.get("active_source"), p["player_id"]]:
        if candidate in queues and queues[candidate].get("available", True): return queues[candidate]
    raise Uncertain("Queue ownership is not available")

def current_id(queue):
    return (queue.get("current_item") or {}).get("queue_item_id") or queue.get("current_item_id") or ""

def identity(rows):
    return [(str(x.get("queue_item_id") or ""), str(x.get("uri") or (x.get("media_item") or {}).get("uri") or "")) for x in rows]

class Coordinator:
    def __init__(self, store, routes, command, ha_states, actuator, lease, *, wait=asyncio.sleep, audio_sync=None):
        self.store, self.routes, self.command = store, routes, command
        self.actuator, self.lease, self.wait = actuator, lease, wait
        self.audio_sync=audio_sync
        self.snapshot = Snapshot(command, ha_states)
        self.signal = asyncio.Event(); self.current = None
    def route(self, endpoint):
        if not isinstance(endpoint, dict): raise InvalidIntent("Endpoint must be an object")
        if endpoint.get("kind") == "phone" and set(endpoint) == {"kind", "player_id"} and isinstance(endpoint["player_id"], str) and PHONE.fullmatch(endpoint["player_id"]):
            return Route(endpoint["player_id"])
        if set(endpoint) - {"room", "player"}: raise InvalidIntent("Unsupported endpoint fields")
        room, player = endpoint.get("room"), endpoint.get("player", "primary")
        value = self.routes.get(room, {}).get(player)
        if not value: raise InvalidIntent("Unknown room/player; configure it on BS5c")
        if not isinstance(value,dict) or set(value)-{"player_id","entity","source","shared","alternate_sources"} or not isinstance(value.get("player_id"),str) or not value["player_id"]:
            raise InvalidIntent("Invalid configured room/player")
        if not isinstance(value.get("alternate_sources",[]),(list,tuple)) or any(not isinstance(x,str) or not x for x in value.get("alternate_sources",[])):
            raise InvalidIntent("Invalid configured alternate source")
        return Route(**value, room=room)
    def submit(self, intent):
        if not isinstance(intent, dict) or set(intent) - {"request_id", "operation", "source", "target"}:
            raise InvalidIntent("Invalid intent")
        if not isinstance(intent.get("request_id"), str) or not UUID.fullmatch(intent["request_id"]): raise InvalidIntent("A stable request ID is required")
        if intent.get("operation") not in OPERATIONS: raise InvalidIntent("Unsupported playback operation")
        self.route(intent.get("target"))
        if intent["operation"] in {"transfer", "join"}: self.route(intent.get("source"))
        elif "source" in intent: raise InvalidIntent("This operation does not accept a source")
        result = self.store.submit(intent); self.signal.set(); return result
    async def run(self):
        self.store.recover()
        while True:
            for job_id in self.store.pending():
                await self.process(job_id)
            self.store.prune(); self.signal.clear()
            # Recheck after clear so submission cannot be lost between queue scan and wait.
            if self.store.pending(): continue
            await self.signal.wait()
    async def _items(self, queue_id):
        rows = await self.command("player_queues/items", {"queue_id": queue_id, "limit": 10000, "offset": 0})
        if not isinstance(rows, list): raise Uncertain("Invalid queue contents")
        if len(rows) >= 10000: raise Uncertain("Queue exceeds safe transfer snapshot limit")
        return rows
    async def _guarded(self, job_id, command, args):
        await self.lease.check(job_id)
        # Save the mutation boundary before dispatch; interrupted jobs are never replayed.
        self.store.update(job_id, "running", "dispatching", command=command)
        try: return await self.command(command, args)
        except asyncio.CancelledError: raise
        except Exception as error: raise DispatchUncertain("MA command outcome requires reconciliation") from error
    async def _confirm_queue(self, destination, before, current_id, playing):
        stable = 0; original_index = next((i for i,x in enumerate(before) if x.get("queue_item_id") == current_id), -1)
        for attempt in range(50):
            s = await self.snapshot.get(fresh=True)
            q = next((q for q in s["queues"] if q["queue_id"] == destination), None)
            rows = await self._items(destination)
            active = (q or {}).get("current_item") or {}
            current = active.get("queue_item_id") or (q or {}).get("current_item_id")
            idx = next((i for i,x in enumerate(before) if x.get("queue_item_id") == current), -1)
            state_ok = (q or {}).get("state") == "playing" if playing else (q or {}).get("state") in {"paused", "idle", "stopped"}
            position_ok = idx >= 0 and (original_index < 0 or idx >= original_index) and (playing or idx == original_index or original_index < 0)
            stable = stable+1 if identity(rows) == identity(before) and position_ok and state_ok else 0
            if stable >= 3: return
            await self.wait(.5)
        raise Uncertain("Destination did not confirm the exact transferred queue/playback; no replay was sent")
    async def _route_on(self, job_id, route):
        if not route.entity: return
        await self.lease.check(job_id)
        s = await self.snapshot.get(fresh=True); state = s["ha"].get(route.entity)
        if not state or state.get("state") in {"unknown", "unavailable"}: raise Uncertain("Fresh destination hardware state required")
        desired=route.source
        actual=state.get("attributes",{}).get("source")
        if route.shared and state.get("state") != "off" and actual in (route.source,*route.alternate_sources): desired=actual
        if desired not in state.get("attributes", {}).get("source_list", []): raise Uncertain("Configured HA source is not available")
        self.store.update(job_id, "running", "routing_target")
        await self.lease.check(job_id)
        await self.actuator("select_source", route.entity, {"source": desired})
        for _ in range(30):
            s = await self.snapshot.get(fresh=True); state = s["ha"].get(route.entity, {})
            if state.get("state") not in {None, "off", "unknown", "unavailable"} and state.get("attributes", {}).get("source") == desired: return
            await self.wait(.3)
        raise Uncertain("HA did not confirm the selected room/source")
    async def _route_off(self, job_id, route):
        if route.entity:
            await self.lease.check(job_id)
            self.store.update(job_id, "running", "stopping_source_room")
            await self.actuator("turn_off", route.entity, {})
            for _ in range(30):
                s = await self.snapshot.get(fresh=True)
                if s["ha"].get(route.entity,{}).get("state") == "off": return
                await self.wait(.3)
            raise Uncertain("HA did not confirm the source room off")
    async def _execute(self, job_id, intent):
        target = self.route(intent["target"]); operation = intent["operation"]
        s = await self.snapshot.get(fresh=True)
        require_music(target.player_id, s)
        interlock=s["ha"].get("input_boolean.kodi_mass_interlock")
        if interlock is None or interlock.get("state") != "off":
            raise InvalidIntent("Kodi owns or may own the audio route; stop it before routing music")
        if operation in {"transfer", "join"}:
            source = self.route(intent["source"])
            require_music(source.player_id, s)
            source_queue = queue_for(source, s); target_queue = queue_for(target, s)
            if operation == "transfer" and source == target: return
            if source.shared:
                hardware=s["ha"].get(source.entity,{})
                if hardware.get("state") in {None,"off","unknown","unavailable"} or hardware.get("attributes",{}).get("source") not in (source.source,*source.alternate_sources):
                    raise InvalidIntent("Source room is not listening to the configured Link source")
            if source_queue.get("state") != "playing" and operation == "join":
                raise InvalidIntent("Only playing music can be joined")
            before = await self._items(source_queue["queue_id"])
            if not before: raise InvalidIntent("Source queue is empty; destination preserved")
            if source_queue.get("state") not in {"playing", "paused", "idle", "stopped"}: raise Uncertain("Source playback is still changing")
            current = current_id(source_queue)
            if current and current not in {x.get("queue_item_id") for x in before}: raise Uncertain("Stale source track; destination preserved")
            if source_queue["queue_id"] == target_queue["queue_id"]:
                if not source.shared or not target.shared:
                    if operation == "join":
                        await self._route_on(job_id,target)
                        return
                    raise InvalidIntent("Already sharing a queue; detach explicitly first")
                if source.source != target.source and source.entity == target.entity:
                    raise InvalidIntent("Cannot join conflicting sources on the same hardware")
                await self._route_on(job_id, target)
                if operation == "transfer": await self._route_off(job_id, source)
                return
            if operation == "join":
                # Use MA grouping for a different source, never a disguised queue transfer.
                # Refuse to replace Link's source while other shared rooms are listening.
                if target.shared:
                    for room, entries in self.routes.items():
                        other=entries.get("primary",{})
                        if room==target.room or not other.get("shared"):continue
                        state=s["ha"].get(other.get("entity"),{})
                        if state.get("state") in {None,"unknown","unavailable"}:raise Uncertain("Another shared room has unknown state")
                        if state.get("state")!="off" and state.get("attributes",{}).get("source") in {other.get("source"),*other.get("alternate_sources",[])}:raise InvalidIntent("Other rooms are listening to a different Link source")
                if leader(target.player_id, s)["player_id"] != canonical(target.player_id, s["players"])["player_id"]: raise InvalidIntent("Target already belongs to a group; leave it first")
                member=canonical(target.player_id,s["players"]); source_player=leader(source.player_id,s)
                if len(set(member.get("group_members") or [])-{member["player_id"]}) > 0:
                    raise InvalidIntent("Target leads a group; preserve its other listeners")
                if member["player_id"] not in (source_player.get("can_group_with") or []):
                    raise InvalidIntent("These MA output protocols cannot group; configure a compatible output first")
                if member["player_id"] in (source_player.get("static_group_members") or []):
                    raise InvalidIntent("This is a fixed MA group; edit its membership in MA")
                try:
                    await self._guarded(job_id, "players/cmd/group", {"player_id": canonical(target.player_id,s["players"])["player_id"], "target_player": leader(source.player_id,s)["player_id"]})
                except DispatchUncertain: pass  # confirm membership with reads; never resend
                for _ in range(30):
                    state = await self.snapshot.get(fresh=True)
                    if leader(target.player_id,state)["player_id"] == leader(source.player_id,state)["player_id"] and queue_for(target,state)["queue_id"] == source_queue["queue_id"] and canonical(target.player_id,state["players"]).get("state",canonical(target.player_id,state["players"]).get("playback_state")) in {"playing","buffering"}:
                        await self._route_on(job_id,target)
                        return
                    await self.wait(.5)
                raise Uncertain("Native MA join could not be confirmed")
            if operation == "transfer":
                source_player=canonical(source.player_id,s["players"])
                if leader(source.player_id,s)["player_id"] != source_player["player_id"] or len(set(source_player.get("group_members") or [])-{source_player["player_id"]}) > 0:
                    raise InvalidIntent("Source belongs to a group; preserve its other listeners")
            if source.shared:
                for room, entries in self.routes.items():
                    other = entries.get("primary", {})
                    if room == source.room or not other.get("shared"): continue
                    state = s["ha"].get(other.get("entity"), {})
                    if state.get("state") in {None,"unknown","unavailable"}: raise Uncertain("Another shared room has unknown state")
                    if state.get("state") != "off" and state.get("attributes",{}).get("source") in {other.get("source"),*other.get("alternate_sources",[])}: raise InvalidIntent("Other rooms are listening to Link; join instead of moving their queue")
            if leader(target.player_id,s)["player_id"] != canonical(target.player_id,s["players"])["player_id"]: raise InvalidIntent("Target is grouped; leave it first")
            if (canonical(target.player_id,s["players"]).get("group_members") or []) not in ([],[canonical(target.player_id,s["players"])["player_id"]]): raise InvalidIntent("Target leads a group; preserve its other listeners")
            await self.lease.check(job_id)
            # Re-read source immediately before mutation to catch a concurrent next/queue edit.
            latest = await self.snapshot.get(fresh=True); q = queue_for(source, latest)
            if q["queue_id"] != source_queue["queue_id"] or identity(await self._items(q["queue_id"])) != identity(before) or current_id(q) != current:
                raise Uncertain("Source changed while preparing transfer; destination preserved")
            playing = source_queue.get("state") == "playing"
            try:
                await self._guarded(job_id, "player_queues/transfer", {"source_queue_id":source_queue["queue_id"],"target_queue_id":target_queue["queue_id"],"auto_play":playing})
            except DispatchUncertain: pass  # only reads follow an uncertain mutation
            await self._confirm_queue(target_queue["queue_id"], before, current, playing)
            await self._route_on(job_id, target)
            await self._route_off(job_id, source)
            return
        p = canonical(target.player_id, s["players"])
        if operation == "leave" and target.shared:
            await self._route_off(job_id, target); return
        group = leader(target.player_id,s)
        if group["player_id"] != p["player_id"]:
            if operation != "leave": raise InvalidIntent("Leave this group before controlling one room independently")
            if p["player_id"] in (group.get("static_group_members") or []):raise InvalidIntent("This is a fixed MA group; edit it in MA")
            try:
                await self._guarded(job_id,"players/cmd/ungroup",{"player_id":p["player_id"]})
            except DispatchUncertain: pass
            for _ in range(30):
                latest=await self.snapshot.get(fresh=True)
                if leader(target.player_id,latest)["player_id"]==p["player_id"]:return
                await self.wait(.5)
            raise Uncertain("MA did not confirm the room left its group")
        if len(set(p.get("group_members") or [])-{p["player_id"]}) > 0: raise InvalidIntent("Group leader handover needs explicit confirmation; other rooms preserved")
        if operation == "leave": return
    async def process(self, job_id):
        job = self.store.get(job_id)
        if not job or job["status"] != "queued": return
        if time.time()-job["created"] > 180:
            self.store.update(job_id,"failed","expired",message="Intent expired before execution; choose again")
            return
        self.current = job_id; self.store.update(job_id,"running","acquiring_ha_lease")
        try:
            async with self.lease.hold(job_id):
                async def execute_and_sync():
                    await self._execute(job_id,job["request"])
                    routes=[self.route(job["request"][key]) for key in ("target","source") if key in job["request"]]
                    shared=next((route for route in routes if route.shared),None)
                    if shared is not None and self.audio_sync is not None:
                        await self.lease.check(job_id)
                        self.store.update(job_id,"running","reconciling_room_audio")
                        await self.audio_sync(job_id,shared.room)
                        await self.lease.check(job_id)
                await asyncio.wait_for(execute_and_sync(),timeout=150)
            self.store.update(job_id,"succeeded","confirmed")
        except asyncio.CancelledError:
            self.store.update(job_id,"interrupted","reconcile_required"); raise
        except InvalidIntent as error: self.store.update(job_id,"failed","rejected",message=str(error))
        except Exception as error: self.store.update(job_id,"needs_review","reconcile_required",message=str(error)[:512])
        finally: self.current = None
