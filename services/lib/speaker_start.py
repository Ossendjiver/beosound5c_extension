"""Route an explicit kiosk playback start through the Home Media HA lease."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid

import aiohttp

from .background_tasks import BackgroundTaskSet
from .config import cfg
from .endpoints import source_url

logger = logging.getLogger(__name__)
_ACTIVE = {"playing", "buffering"}
_ON = _ACTIVE | {"on", "idle", "paused", "standby_on"}
_OFF = {"off", "standby"}
_LEASE = "/home_media_bridge/routing-lease"
_COMMANDS = {"play_item", "play_now", "play_radio", "play_news"}
_PLAY_KEYS = {"command", "id", "url", "play_url"}


class SpeakerStartError(Exception):
    pass


class SpeakerStart:
    def __init__(self, session):
        self.session = session
        self._tasks = BackgroundTaskSet(logger, label="speaker-start")
        self.settings = cfg("playback", "speaker_start", default={}) or {}
        self.targets = self.settings.get("targets") or {}
        self.token = os.getenv("HA_TOKEN", "").strip()
        base = (os.getenv("HA_URL") or cfg("home_assistant", "url", default="")).rstrip("/")
        self.api = base if base.endswith("/api") else f"{base}/api"

    async def _ha(self, path, data=None, *, timeout=5):
        method = "POST" if data is not None else "GET"
        async with self.session.request(
            method, self.api + path, json=data,
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as response:
            if response.status >= 400:
                if path == _LEASE and response.status == 409:
                    raise SpeakerStartError("Another playback route is in progress. Try again shortly.")
                raise SpeakerStartError(f"Home Assistant returned HTTP {response.status} for {path}.")
            return await response.json()

    async def _all_off(self):
        states = {entry["entity_id"]: entry for entry in await self._ha("/states")}
        showing = cfg("showing", "entity_id", default="")
        active_entities = set(self.settings.get("playback_entities", [])) | ({showing} - {""})
        if any(states.get(entity, {}).get("state") in _ACTIVE for entity in active_entities):
            return False
        unknown = []
        for entity in self.settings["power_entities"]:
            state = states.get(entity, {})
            if state.get("attributes", {}).get("art_mode_status") == "on":
                continue
            if state.get("state") in _ON:
                return False
            if state.get("state") not in _OFF:
                unknown.append(entity)
        if unknown:
            raise SpeakerStartError(f"The power state of {unknown[0]} is unavailable.")
        return True

    async def options(self):
        if not self.targets:
            return []
        if not self.token or not self.settings.get("power_entities"):
            raise SpeakerStartError("Home Assistant speaker routing is unavailable.")
        if not await self._all_off():
            return []
        return [{"id": key, "name": target["name"]} for key, target in self.targets.items()]

    async def _service(self, service, entity, **data):
        await self._ha(f"/services/{service}", {"entity_id": entity, **data})

    async def _ready(self, target):
        switch = target.get("switch")
        hardware = target.get("receiver")
        startup_input = target.get("startup_input")
        for _ in range(12):
            receiver = await self._ha(f"/states/{hardware}") if hardware else {}
            receiver_ready = not hardware or (
                receiver.get("state") in _ON and receiver.get("attributes", {}).get("source") == target["source"]
            )
            switch_ready = not switch or (await self._ha(f"/states/{switch}")).get("state") == "on"
            input_ready = not startup_input or (
                await self._ha(f"/states/{startup_input['entity']}")
            ).get("attributes", {}).get("source") == startup_input["source"]
            if receiver_ready and switch_ready and input_ready:
                return
            await asyncio.sleep(.4)
        raise SpeakerStartError(f"{target['name']} did not confirm its input or power.")

    async def _wake(self, target):
        if target.get("receiver"):
            await self._service("media_player/select_source", target["receiver"], source=target["source"])
        if target.get("switch"):
            await self._service("switch/turn_on", target["switch"])
        await self._ready(target)
        if target.get("cinema"):
            await self._ha("/services/script/homemedia_speaker_control", {
                "room": "lounge", "operation": "orientation", "cinema": target["cinema"],
            }, timeout=12)
            await self._ha("/services/script/homemedia_speaker_control", {
                "room": "lounge", "operation": "audio_start",
            }, timeout=12)

    async def _play(self, target, command):
        if not command:
            if target.get("resume_script"):
                await self._ha(f"/services/script/{target['resume_script']}", {"playback_reason": "bs5c_manual"}, timeout=15)
            else:
                await self._service("media_player/media_play", target["player_entity"])
            result = {"state": "starting", "queue_id": target["queue_id"]}
        elif command["command"] == "play_news":
            result = await self._post_source(8776, "/action", {
                "action": "play_news", "target_player_id": target["queue_id"],
            })
        else:
            payload = {key: command[key] for key in _PLAY_KEYS if key in command}
            if payload["command"] == "play_item":
                # This is a confirmed cold start. An old paused queue must not turn it into an enqueue.
                payload["command"] = "play_now"
            payload.update(target_player_id=target["queue_id"], queue_id=target["queue_id"])
            result = await self._post_source(8783, "/command", payload)
        for _ in range(12):
            states = await asyncio.gather(*(self._ha(f"/states/{entity}") for entity in target["player_entities"]))
            if any(state.get("state") in _ACTIVE for state in states):
                return {**result, "queue_id": target["queue_id"], "speaker_name": target["name"]}
            await asyncio.sleep(.4)
        raise SpeakerStartError(f"Playback did not start on {target['name']}.")

    async def _post_source(self, port, path, payload):
        async with self.session.post(
            source_url(port, path), json=payload, timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            result = await response.json()
            if response.status >= 400 or result.get("state") == "error" or result.get("status") == "error":
                raise SpeakerStartError("The playback service could not start the selected item.")
            return result

    async def _renew(self, owner):
        while True:
            await asyncio.sleep(5)
            await self._ha(_LEASE, {"action": "renew", "owner": owner})

    async def _route(self, target, command):
        if not await self._all_off():
            raise SpeakerStartError("A playback device turned on. Cancel and start playback again.")
        interlock = await self._ha("/states/input_boolean.kodi_mass_interlock")
        if interlock.get("state") != "off":
            raise SpeakerStartError("The Kodi playback interlock is active.")
        if target.get("cinema"):
            speaker = await self._ha("/states/script.homemedia_speaker_control")
            if speaker.get("state") not in {"off", "on"}:
                raise SpeakerStartError("Home Assistant speaker control is unavailable.")
        await self._wake(target)
        return await self._play(target, command)

    async def start(self, target_id, command):
        target = self.targets.get(target_id)
        if not target or not self.token or not self.settings.get("power_entities"):
            raise SpeakerStartError("The selected speaker route is unavailable.")
        if command is not None and (
            not isinstance(command, dict) or not isinstance(command.get("command"), str)
            or command["command"] not in _COMMANDS
            or any(not isinstance(command[key], str) for key in _PLAY_KEYS & command.keys())
            or (command["command"] != "play_news" and not (command.get("url") or command.get("play_url")))
        ):
            raise SpeakerStartError("This playback command cannot be sent to the selected speakers.")

        owner = "bs5c-" + uuid.uuid4().hex
        await self._ha(_LEASE, {"action": "acquire", "owner": owner})
        tasks = [self._tasks.spawn(self._route(target, command), name="speaker_route"),
                 self._tasks.spawn(self._renew(owner), name="speaker_lease_renew")]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if tasks[1] in done:
                await tasks[1]
            return await tasks[0]
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                await self._ha(_LEASE, {"action": "release", "owner": owner})
            except (SpeakerStartError, aiohttp.ClientError, TimeoutError):
                logger.warning("Could not release the BS5C routing lease", exc_info=True)
