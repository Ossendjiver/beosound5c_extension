"""
Generic audio output management via PipeWire/PulseAudio.

Lists, classifies, and switches audio sinks. Detects AirPlay, Bluetooth A2DP,
HDMI, analog (3.5mm), and optical/SPDIF outputs automatically based on
PipeWire sink naming conventions.

Usage:
    audio = AudioOutputs()
    outputs = await audio.get_outputs()        # list all available sinks
    await audio.set_output(sink_name)          # switch default + move streams
    sink = await audio.find_sink(ip="192.168.1.135")  # find by IP
    sink = await audio.find_sink(type="bluetooth")     # find by type

Every method that shells out runs the subprocess via
``asyncio.create_subprocess_exec`` so the event loop stays responsive
— previously pactl calls could block the loop for multiple seconds.
"""

import asyncio
import logging
import os
import re

log = logging.getLogger(__name__)


# Sink type classification rules, checked in order.
# Each rule: (type_name, match_function)
_SINK_RULES = [
    ("bluetooth", lambda name, desc: name.startswith("bluez_output.") or
                                     "bluetooth" in desc.lower() and "a2dp" in name.lower()),
    ("hdmi",      lambda name, desc: "hdmi" in name.lower() or "hdmi" in desc.lower()),
    ("optical",   lambda name, desc: any(k in name.lower() or k in desc.lower()
                                         for k in ("spdif", "iec958", "optical", "digital-stereo",
                                                    "hifiberry-digi"))),
    ("usb",       lambda name, desc: name.startswith("alsa_output.usb-")),
    ("analog",    lambda name, desc: any(k in name.lower() or k in desc.lower()
                                         for k in ("headphones", "analog", "bcm2835",
                                                    "alsa_output.platform"))),
    # AirPlay last — sub-classified by _classify_airplay
    ("airplay",   lambda name, desc: name.startswith("raop_sink.")),
]

# AirPlay sub-classification: extract the hostname from the RAOP sink name
# and match against known device patterns. Checked in order, first match wins.
_AIRPLAY_RULES = [
    ("sonos",     lambda host: host.startswith("Sonos-")),
    ("homepod",   lambda host: "homepod" in host.lower()),
    ("appletv",   lambda host: "appletv" in host.lower() or "apple-tv" in host.lower()),
    ("mac",       lambda host: any(k in host.lower() for k in
                                   ("macbook", "macmini", "mac-mini", "imac", "macpro",
                                    "mac-pro", "mac-studio", "macstudio"))),
    ("iphone",    lambda host: "iphone" in host.lower()),
    ("ipad",      lambda host: "ipad" in host.lower()),
]


class AudioOutputs:
    """Lists and switches audio outputs via PipeWire/PulseAudio.

    PipeWire discovers sinks automatically:
      - AirPlay speakers via the RAOP discovery module
      - Bluetooth A2DP via libspa-0.2-bluetooth
      - Local outputs (HDMI, analog, optical) via ALSA
    """

    def __init__(self):
        self.current_sink = None
        self._env = os.environ.copy()
        self._env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")

    async def _run(self, *args, timeout: float = 3.0,
                   capture: bool = True) -> tuple[str, int]:
        """Run a subprocess without blocking the event loop.

        Returns (stdout_text, returncode).  Kills the process on timeout.
        """
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE if capture else asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env=self._env,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        return (stdout.decode("utf-8", "replace") if stdout else ""), (proc.returncode or 0)

    async def get_outputs(self):
        """List all available audio sinks with type classification.

        Returns list of dicts:
            {name, label, type, active}

        Where type is one of:
            sonos, homepod, appletv, mac, iphone, ipad, airplay (generic),
            bluetooth, hdmi, optical, usb, analog, other
        """
        try:
            short_out, _ = await self._run("pactl", "list", "sinks", "short")
            full_out, _ = await self._run("pactl", "list", "sinks")
            default_out, _ = await self._run("pactl", "get-default-sink")
            default = default_out.strip()

            # Parse descriptions from full output
            descriptions = {}
            current_name = None
            for line in full_out.split("\n"):
                line = line.strip()
                if line.startswith("Name:"):
                    current_name = line.split(":", 1)[1].strip()
                elif line.startswith("Description:") and current_name:
                    descriptions[current_name] = line.split(":", 1)[1].strip()

            outputs = []
            for line in short_out.strip().split("\n"):
                if not line.strip():
                    continue
                parts = line.split("\t")
                if len(parts) < 2:
                    continue
                sink_name = parts[1]

                if "null" in sink_name.lower():
                    continue

                description = descriptions.get(sink_name, sink_name)
                sink_type = _classify_sink(sink_name, description)

                outputs.append({
                    "name": sink_name,
                    "label": description,
                    "type": sink_type,
                    "active": sink_name == default,
                })

            self.current_sink = default
            return outputs

        except Exception as e:
            log.error("Failed to list audio outputs: %s", e)
            return []

    async def find_sink(self, *, ip=None, type=None, name_contains=None):
        """Find a sink matching the given criteria. Returns dict or None.

        Args:
            ip: Match sinks whose name contains this IP address
            type: Match sinks of this type (airplay, bluetooth, hdmi, etc.)
            name_contains: Match sinks whose name contains this substring
        """
        for output in await self.get_outputs():
            if ip and ip not in output["name"]:
                continue
            if type and output["type"] != type:
                continue
            if name_contains and name_contains not in output["name"]:
                continue
            return output
        return None

    async def check_pipewire_health(self):
        """Quick check if PipeWire/PulseAudio can handle audio streams.

        Tests by running ``pactl info`` and checking for a valid server
        protocol version.  A value of 4294967295 (0xFFFFFFFF) indicates
        a broken PulseAudio handshake — PipeWire needs a restart.

        Returns True if healthy, False if broken.
        """
        try:
            stdout, rc = await self._run("pactl", "info")
            for line in stdout.split("\n"):
                if "Server Protocol Version" in line:
                    version = line.split(":", 1)[1].strip()
                    if version == "4294967295":
                        log.warning("PipeWire broken: invalid protocol version")
                        return False
            return rc == 0
        except Exception as e:
            log.warning("PipeWire health check failed: %s", e)
            return False

    async def restart_pipewire(self):
        """Restart the PipeWire stack (pipewire + pipewire-pulse + wireplumber).

        Waits for sinks to re-appear after restart.  Returns True on
        success, False if restart failed or sinks didn't come back.
        """
        log.warning("Restarting PipeWire stack...")
        try:
            await self._run(
                "systemctl", "--user", "restart",
                "pipewire", "pipewire-pulse", "wireplumber",
                timeout=10, capture=False,
            )
            # Wait for sinks to re-appear (mDNS RAOP discovery takes a few seconds)
            for attempt in range(15):
                await asyncio.sleep(2)
                outputs = await self.get_outputs()
                if outputs:
                    log.info("PipeWire restarted — %d sinks available after %ds",
                             len(outputs), (attempt + 1) * 2)
                    return True
            log.error("PipeWire restarted but no sinks appeared after 30s")
            return False
        except Exception as e:
            log.error("PipeWire restart failed: %s", e)
            return False

    async def ensure_healthy(self):
        """Check PipeWire health and auto-restart if broken.

        Call this before starting playback to avoid silent failures.
        Returns True if audio subsystem is ready.
        """
        if await self.check_pipewire_health():
            return True
        return await self.restart_pipewire()

    async def ensure_output(self, ip):
        """Ensure the AirPlay sink for the given IP exists and is the default.

        Checks PipeWire health first.  Waits up to 5s for PipeWire to
        rediscover the speaker if it disappeared.
        Returns True if the sink is ready, False if it couldn't be found/set.
        """
        if not ip:
            return False

        # Auto-heal PipeWire if broken
        if not await self.check_pipewire_health():
            if not await self.restart_pipewire():
                return False

        sink = await self.find_sink(ip=ip)
        if sink and sink.get("active"):
            return True
        if sink:
            return await self.set_output(sink["name"])

        # Sink gone — wait for PipeWire to rediscover it
        for attempt in range(5):
            await asyncio.sleep(1)
            sink = await self.find_sink(ip=ip)
            if sink:
                log.info("AirPlay sink reappeared after %ds", attempt + 1)
                return await self.set_output(sink["name"])

        log.warning("AirPlay sink for %s not found after 5s", ip)
        return False

    async def set_output(self, sink_name):
        """Switch default audio output and move active streams.

        Returns True on success, False on failure.
        """
        try:
            _, rc = await self._run("pactl", "set-default-sink", sink_name)
            if rc != 0:
                log.error("set-default-sink %s returned rc=%d", sink_name, rc)
                return False
            # Move any active playback streams to the new sink
            inputs_out, _ = await self._run("pactl", "list", "sink-inputs", "short")
            for line in inputs_out.strip().split("\n"):
                if line.strip():
                    stream_id = line.split("\t")[0]
                    await self._run(
                        "pactl", "move-sink-input", stream_id, sink_name)

            self.current_sink = sink_name
            log.info("Audio output -> %s", sink_name)
            return True
        except Exception as e:
            log.error("Failed to set output %s: %s", sink_name, e)
            return False


def _classify_sink(name, description):
    """Classify a sink by checking rules in priority order.

    AirPlay sinks are further sub-classified by hostname pattern
    (e.g. Sonos-*, MacBook*, etc.) into more specific types.
    """
    for type_name, match_fn in _SINK_RULES:
        if match_fn(name, description):
            if type_name == "airplay":
                return _classify_airplay(name)
            return type_name
    return "other"


def _classify_airplay(sink_name):
    """Sub-classify an AirPlay sink by extracting and matching the hostname.

    RAOP sink names follow: raop_sink.<hostname>.<ip>.<port>
    e.g. raop_sink.Sonos-XXXXXXXXXXXX.local.192.168.1.100.7000
    """
    # Strip "raop_sink." prefix, then take everything before the IP
    rest = sink_name.removeprefix("raop_sink.")
    # hostname is everything up to the first digit-dot-digit IP pattern
    m = re.match(r"^(.+?)\.?\d+\.\d+\.\d+\.\d+", rest)
    hostname = m.group(1).rstrip(".") if m else rest

    for type_name, match_fn in _AIRPLAY_RULES:
        if match_fn(hostname):
            return type_name
    return "airplay"
