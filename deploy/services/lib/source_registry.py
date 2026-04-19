"""Source registry for BeoSound 5c router.

Manages the lifecycle of audio sources (Spotify, CD, Radio, etc.):
registration, activation, deactivation, and menu visibility.

Source states: gone → available → playing/paused → available → gone
Only one source can be active (playing/paused) at a time.
"""

import asyncio
import json
import logging

logger = logging.getLogger("beo-router")

STATE_FILE = "/tmp/beo-router-state.json"

# Source handles defaults (used when a source registers without specifying handles)
_DIGITS = {"0", "1", "2", "3", "4", "5", "6", "7", "8", "9"}
DEFAULT_SOURCE_HANDLES = {
    "cd": {"play", "pause", "next", "prev", "stop", "go", "left", "right",
           "up", "down", "info", "track"} | _DIGITS,
    "spotify": {"play", "pause", "next", "prev", "stop", "go", "left", "right",
                "up", "down"} | _DIGITS,
    "usb": {"play", "pause", "next", "prev", "stop", "go", "left", "right",
            "up", "down"},
    "news": {"go", "left", "right", "up", "down"},
    "radio": {"play", "pause", "next", "prev", "stop", "go", "left", "right",
              "up", "down", "red", "blue"} | _DIGITS,
}

# Known source ports — used on startup to probe running sources
DEFAULT_SOURCE_PORTS = {
    "cd": 8769,
    "spotify": 8771,
    "usb": 8773,
    "apple_music": 8774,
    "news": 8776,
    "tidal": 8777,
    "plex": 8778,
    "radio": 8779,
    "mass": 8783,
    "kodi": 8782,
    "join": 8766,
}


# Valid state transitions — any transition not listed here is rejected.
# Self-transitions (playing→playing, available→available) are valid for
# re-registration and resync scenarios.
# gone→playing/paused is allowed for the router-restart resync case: a
# source process that's mid-playback when the router restarts will
# re-register directly into its current state, and we must not reject
# it — otherwise the source stays unregistered until it pauses back to
# available, which never happens on its own.
VALID_TRANSITIONS = {
    "gone":      {"available", "playing", "paused"},
    "available": {"available", "playing", "paused", "gone"},
    "playing":   {"playing", "paused", "available", "gone"},
    "paused":    {"playing", "paused", "available", "gone"},
}


class Source:
    """A registered source that can receive routed events.

    ``state`` is a read-only property.  All mutations must go through
    :class:`SourceRegistry` so transitions are validated against
    ``VALID_TRANSITIONS`` and UI broadcasts happen consistently.
    Historically several bugs (84f9bb3, aac5b60, df5605e, 9ef9492) were
    caused by direct ``source.state = ...`` writes that bypassed the
    state machine; the read-only property is the regression guard.
    """

    __slots__ = (
        "id", "name", "command_url", "handles", "menu_preset", "player",
        "_state", "from_config", "visible", "manages_queue",
    )

    def __init__(self, id: str, handles: set):
        self.id = id
        self.name = id.upper()
        self.command_url = ""
        self.handles = handles
        self.menu_preset = id
        self.player = "local"
        self._state = "gone"
        self.from_config = False
        self.visible = "auto"
        self.manages_queue = False

    @property
    def state(self) -> str:
        return self._state

    # Deliberately no setter: ``source.state = 'x'`` raises AttributeError
    # at runtime.  Use ``SourceRegistry.update()`` (or the specific
    # helpers it exposes) so transitions go through ``VALID_TRANSITIONS``.

    def to_menu_item(self) -> dict:
        return {
            "id": self.id,
            "title": self.name,
            "preset": self.menu_preset,
            "dynamic": True,
        }


class SourceRegistry:
    """Manages dynamic sources and their lifecycle.

    The registry tracks all known sources and ensures only one is active at
    any time.  It delegates UI broadcasting and media clearing to callback
    functions provided by the router, keeping the registry testable in
    isolation.
    """

    def __init__(self):
        self._sources: dict[str, Source] = {}
        self._active_id: str | None = None
        self._persisted_active_id: str | None = self._load_persisted_active()
        self._resync_in_progress: bool = False

    # ── Persistence ──

    @staticmethod
    def _load_persisted_active() -> str | None:
        try:
            with open(STATE_FILE) as f:
                data = json.load(f)
                active = data.get("active_source_id")
                if active:
                    logger.info("Loaded persisted active source: %s", active)
                return active
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None

    def _persist_active(self):
        try:
            with open(STATE_FILE, "w") as f:
                json.dump({"active_source_id": self._active_id}, f)
        except OSError as e:
            logger.warning("Failed to persist active source: %s", e)

    # ── Public interface ──

    def consume_persisted_active(self) -> str | None:
        val = self._persisted_active_id
        self._persisted_active_id = None
        return val

    @property
    def active_source(self) -> Source | None:
        if self._active_id:
            return self._sources.get(self._active_id)
        return None

    @property
    def active_id(self) -> str | None:
        return self._active_id

    def get(self, id: str) -> Source | None:
        return self._sources.get(id)

    def create_from_config(self, id: str, handles: set) -> Source:
        source = Source(id, handles)
        self._sources[id] = source
        return source

    def all_available(self) -> list[Source]:
        return [s for s in self._sources.values() if s.state != "gone"]

    # ── State transitions ──

    @staticmethod
    def _validate_transition(old_state: str, new_state: str) -> bool:
        """Check if a state transition is allowed."""
        valid = VALID_TRANSITIONS.get(old_state)
        return valid is not None and new_state in valid

    async def update(self, id: str, state: str, router, **fields) -> dict:
        """Handle a source state transition.

        ``router`` must provide: _broadcast(), _push_media(), _media_state,
        _latest_action_ts, _forward_to_source(), _volume, _wake_screen(),
        _get_config_title(), _get_after().
        """
        source = self._sources.get(id)
        was_new = source is None or source.state == "gone"
        was_active = self._active_id == id

        if source is None:
            handles = set(fields.get("handles", []))
            source = Source(id, handles)
            self._sources[id] = source

        for key in ("name", "command_url", "menu_preset", "player"):
            if key in fields:
                setattr(source, key, fields[key])
        if "manages_queue" in fields:
            source.manages_queue = fields["manages_queue"]
        if "handles" in fields and not source.handles:
            source.handles = set(fields["handles"])

        old_state = source.state

        # ── Validate transition ──
        if not self._validate_transition(old_state, state):
            logger.warning("Rejected invalid transition for %s: %s -> %s",
                           id, old_state, state)
            return {"actions": [], "old_state": old_state, "new_state": state,
                    "rejected": "invalid_transition"}

        source._state = state
        actions = []

        # First-time registration: add menu item regardless of the state
        # the source is registering into. A source that was mid-playback
        # during a router restart will register directly into playing or
        # paused (see VALID_TRANSITIONS); it still needs to appear in
        # the menu, and the "source registered" log line is a useful
        # breadcrumb for either path.
        if was_new:
            if source.visible not in ("never", "always"):
                broadcast_data = {"action": "add", "preset": source.menu_preset}
                config_title = router._get_config_title(id)
                if config_title:
                    broadcast_data["title"] = config_title
                after_id = router._get_after(id)
                if after_id:
                    broadcast_data["after"] = f"menu/{after_id}"
                await router.media.broadcast("menu_item", broadcast_data)
                actions.append("add_menu_item")
            logger.info("Source registered: %s (state=%s, handles: %s)",
                        id, state, source.handles)

        if state == "playing":
            if self._active_id != id:
                action_ts = fields.get("action_ts", 0)
                if action_ts and action_ts < router._latest_action_ts:
                    logger.info("Rejected stale register from %s (ts=%.3f < latest=%.3f)",
                                id, action_ts, router._latest_action_ts)
                    return {"actions": actions, "old_state": old_state, "new_state": state}

                if self._resync_in_progress and self._active_id:
                    logger.info("Resync: %s wants active but %s is current — skipping",
                                id, self._active_id)
                    return {"actions": actions, "old_state": old_state, "new_state": state}

                # Atomic source switch: await old source stop before activating new
                old_source = self._sources.get(self._active_id) if self._active_id else None
                if old_source and old_source.command_url:
                    logger.info("Stopping old source: %s", old_source.id)
                    try:
                        await asyncio.wait_for(
                            router._forward_to_source(old_source, {"action": "stop"}),
                            timeout=3.0)
                    except asyncio.TimeoutError:
                        logger.warning("Timeout stopping old source %s — proceeding",
                                       old_source.id)
                self._active_id = id
                self._persist_active()
                await router.media.broadcast("source_change", {
                    "active_source": id, "source_name": source.name,
                    "player": source.player,
                })
                # Player-backed sources own their metadata — the player service
                # will push the correct track within milliseconds, so a
                # push_idle here would only flash empty metadata unnecessarily.
                # Non-player sources (e.g. web views) still need the idle push
                # to clear stale metadata from a previous source.
                if not source.player:
                    await router.media.push_idle("source_change")
                actions.append("source_change")
                logger.info("Source activated: %s (player=%s)", id, source.player)

            if fields.get("auto_power"):
                if router._volume and not await router._volume.is_on():
                    await router._volume.power_on()
                await router._wake_screen()

        elif state == "paused":
            if self._active_id != id:
                current = self._sources.get(self._active_id) if self._active_id else None
                if not current or current.state not in ("playing", "paused"):
                    self._active_id = id
                    self._persist_active()
                    await router.media.broadcast("source_change", {
                        "active_source": id, "source_name": source.name,
                        "player": source.player,
                    })
                    actions.append("source_change")

        elif state == "available" and was_active:
            self._active_id = None
            self._persist_active()
            await router.media.broadcast("source_change", {
                "active_source": None, "player": None,
            })
            await router.media.push_idle("source_deactivated")
            actions.append("source_change_clear")
            logger.info("Source deactivated: %s", id)

        elif state == "gone":
            if was_active:
                self._active_id = None
                self._persist_active()
                await router.media.broadcast("source_change", {
                    "active_source": None, "player": None,
                })
                await router.media.push_idle("source_gone")
                actions.append("source_change_clear")
            if source.visible not in ("never", "always"):
                await router.media.broadcast("menu_item", {
                    "action": "remove", "preset": source.menu_preset
                })
                actions.append("remove_menu_item")
            # state is already set to "gone" above via _state; this
            # branch only runs the unregister side-effects.
            logger.info("Source unregistered: %s", id)

        if fields.get("navigate") and state in ("playing", "available"):
            page = "menu/playing" if state == "playing" else f"menu/{id}"
            await router.media.broadcast("navigate", {"page": page})
            actions.append(f"navigate:{page}")

        return {"actions": actions, "old_state": old_state, "new_state": state}

    async def restore_persisted_active(
        self, persisted_id: str, resynced: list[str], router,
    ) -> bool:
        """Promote a persisted source back to active after startup resync.

        Startup resync rediscovers several sources as ``playing`` or
        ``paused`` from their on-disk state.  Only one may be active,
        so this method demotes every resynced source *except*
        ``persisted_id`` back to ``available`` and then restores
        ``persisted_id`` as the active source.

        This replaces the previous inline code in
        ``router._probe_running_sources`` which mutated
        ``source.state = "available"`` directly — a path that now
        raises ``AttributeError`` because ``Source.state`` is
        read-only.  All mutation is routed through
        ``VALID_TRANSITIONS`` here.

        Returns True if the persisted source was successfully restored.
        """
        if persisted_id not in resynced:
            return False

        # Demote every other resynced source.
        for source_id in resynced:
            if source_id == persisted_id:
                continue
            source = self._sources.get(source_id)
            if source is None or source.state not in ("playing", "paused"):
                continue
            if not self._validate_transition(source.state, "available"):
                logger.warning(
                    "restore_persisted_active: cannot demote %s from %s",
                    source_id, source.state,
                )
                continue
            source._state = "available"
            logger.info(
                "Startup resync: demoted %s to available (persisted active: %s)",
                source_id, persisted_id,
            )

        # Promote persisted_id back to active.
        persisted = self._sources.get(persisted_id)
        if persisted is None or persisted.state not in ("playing", "paused"):
            return False
        if self._active_id == persisted_id:
            return True
        self._active_id = persisted_id
        self._persist_active()
        await router.media.broadcast("source_change", {
            "active_source": persisted_id,
            "source_name": persisted.name,
            "player": persisted.player,
        })
        logger.info("Startup resync: restored active source: %s", persisted_id)
        return True

    async def clear_active_source(self, router, push_idle: bool = True) -> bool:
        """Clear the active source and (optionally) push idle media.

        Called when external playback overrides the BS5c source (e.g.
        someone uses the Sonos app directly).

        ``push_idle=False`` is used by the eager-broadcast path on external
        Sonos playback start: the player has already pushed (or is about
        to push) real media for the new track, and the idle broadcast
        would wipe ``MediaState._state`` right after the real update
        lands. Suppressing it here keeps the UI's mediaInfo intact.
        """
        if self._active_id is None:
            return False
        old_id = self._active_id
        self._active_id = None
        self._persist_active()
        await router.media.broadcast("source_change", {
            "active_source": None, "player": None,
        })
        if push_idle:
            # Clear stale media so UI doesn't show old source's metadata
            await router.media.push_idle("external_override")
        logger.info("Active source cleared (was: %s, push_idle=%s)",
                    old_id, push_idle)
        return True
