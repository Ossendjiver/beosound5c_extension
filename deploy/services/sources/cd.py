#!/usr/bin/env python3
"""
BeoSound 5c CD Service (beo-cd)

Monitors USB CD/DVD drive, reads disc metadata from MusicBrainz,
routes playback through Music Assistant, and manages FLAC ripping.
"""

import asyncio
import fcntl
import json
import os
import re
import socket
import subprocess
import sys
import logging
import urllib.parse
from pathlib import Path
from aiohttp import web, ClientSession

# Optional imports with graceful fallback
try:
    import discid
    HAS_DISCID = True
except ImportError:
    HAS_DISCID = False

try:
    import musicbrainzngs
    HAS_MB = True
    musicbrainzngs.set_useragent("BeoSound5c", "1.0", "https://github.com/beosound5c")
except ImportError:
    HAS_MB = False

try:
    from zeroconf import ServiceBrowser, Zeroconf, ServiceStateChange
    HAS_ZEROCONF = True
except ImportError:
    HAS_ZEROCONF = False

try:
    import pyudev
    HAS_PYUDEV = True
except ImportError:
    pyudev = None
    HAS_PYUDEV = False

# Shared library
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.audio_outputs import AudioOutputs
from lib.cd_ripper import CDRipManager, parse_duration, safe_component
from lib.config import cfg
from lib.source_base import SourceBase


from lib.tts import tts_announce, tts_precache

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
log = logging.getLogger('beo-cd')

# Configuration
CDROM_DEVICE = cfg("cd", "device", default="/dev/sr0")
BS5C_BASE_PATH = os.getenv('BS5C_BASE_PATH', os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
CD_CACHE_DIR = os.path.join(BS5C_BASE_PATH, 'web/assets/cd-cache')
MASS_SERVICE_URL = str(cfg("cd", "mass_service_url", default="http://127.0.0.1:8783")).rstrip('/')
ROUTER_PLAYBACK_URL = "http://127.0.0.1:8770/router/playback"
CD_RIP_DEFAULT_DESTINATION = cfg("cd", "rip_destination", default="/media/local/music")
CD_QUIET_RIP_SPEED = max(1, int(cfg("cd", "quiet_rip_speed", default=4) or 4))
CD_RIP_STATE_FILE = os.getenv(
    "CD_RIP_STATE_FILE",
    "/media/local/cache/cd_rip_settings.json",
)
# Linux CDROM ioctl constants (from <linux/cdrom.h>)
CDROM_DRIVE_STATUS = 0x5326
CDS_DISC_OK = 4


class CDDrive:
    """Monitors CD/DVD drive presence and disc insertion/ejection via udev events."""

    def __init__(self, device_path=CDROM_DEVICE):
        self.device_path = device_path
        self.drive_connected = False
        self.disc_inserted = False
        self._monitor_task = None
        self._udev_observer = None

    async def start_polling(self, on_drive_change, on_disc_change):
        self._on_drive_change = on_drive_change
        self._on_disc_change = on_disc_change
        monitor = self._udev_monitor if HAS_PYUDEV else self._poll_monitor
        self._monitor_task = asyncio.create_task(monitor())

    async def stop(self):
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

    def _check_disc_ioctl(self):
        """Check disc presence via ioctl — no physical read, no clicking."""
        try:
            fd = os.open(self.device_path, os.O_RDONLY | os.O_NONBLOCK)
            try:
                status = fcntl.ioctl(fd, CDROM_DRIVE_STATUS, 0)
                return status == CDS_DISC_OK
            finally:
                os.close(fd)
        except (OSError, IOError):
            return False

    async def _update_state(self):
        """Check drive/disc state and fire callbacks on changes."""
        drive_present = Path(self.device_path).exists()
        disc_present = drive_present and await asyncio.get_running_loop().run_in_executor(
            None, self._check_disc_ioctl)

        if drive_present != self.drive_connected:
            self.drive_connected = drive_present
            log.info(f"Drive {'connected' if drive_present else 'disconnected'}")
            await self._on_drive_change(drive_present)

        if disc_present != self.disc_inserted:
            self.disc_inserted = disc_present
            log.info(f"Disc {'inserted' if disc_present else 'ejected'}")
            await self._on_disc_change(disc_present)

    async def _udev_monitor(self):
        """Watch for udev events on the CD device — event-driven, no polling."""
        loop = asyncio.get_running_loop()
        context = pyudev.Context()
        monitor = pyudev.Monitor.from_netlink(context)
        monitor.filter_by(subsystem='block', device_type='disk')

        await self._update_state()

        # Use a threading event pipe to bridge udev callbacks to asyncio
        event = asyncio.Event()

        def _on_udev_event(action, device):
            if device.device_node == self.device_path or action in ('add', 'remove'):
                loop.call_soon_threadsafe(event.set)

        self._udev_observer = pyudev.MonitorObserver(monitor, _on_udev_event)
        self._udev_observer.start()
        log.info(f"Monitoring {self.device_path} via udev events")

        try:
            while True:
                await event.wait()
                event.clear()
                await asyncio.sleep(0.5)  # debounce — udev fires multiple events per disc change
                await self._update_state()
        finally:
            self._udev_observer.stop()

    async def _poll_monitor(self):
        """Fallback when pyudev is unavailable: poll drive/disc state periodically."""
        log.warning("pyudev not installed - CD source falling back to polling mode")
        await self._update_state()
        while True:
            await asyncio.sleep(2)
            await self._update_state()

    async def eject(self):
        """Eject the disc."""
        try:
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: subprocess.run(['eject', self.device_path], timeout=5)
            )
            log.info("Disc ejected")
        except Exception as e:
            log.error(f"Eject failed: {e}")


class CDMetadata:
    """Fetches CD metadata from MusicBrainz + Cover Art Archive."""

    def __init__(self, device_path=CDROM_DEVICE, cache_dir=CD_CACHE_DIR):
        self.device_path = device_path
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.last_disc = None  # last discid.Disc object (for TOC offsets)

    async def lookup(self):
        """Read disc TOC and query MusicBrainz. Returns metadata dict or None."""
        if not HAS_DISCID:
            log.warning("python-discid not installed — skipping metadata lookup")
            return None

        try:
            disc = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(
                    None, lambda: discid.read(self.device_path)),
                timeout=15)

            self.last_disc = disc
            disc_id = disc.id
            log.info(f"Disc ID: {disc_id}, tracks: {len(disc.tracks)}")

            if not HAS_MB:
                log.warning("musicbrainzngs not installed — using fallback metadata")
                return self._fallback_metadata(disc)

            try:
                result = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: musicbrainzngs.get_releases_by_discid(
                        disc_id, includes=['artists', 'recordings']
                    )
                )
            except Exception as e:
                log.warning(f"MusicBrainz lookup failed: {e}")
                return self._fallback_metadata(disc)

            if 'disc' not in result:
                log.warning(f"No MusicBrainz match for disc {disc_id}")
                return self._fallback_metadata(disc)

            release_list = result['disc']['release-list']
            release = release_list[0]
            artist = release.get('artist-credit-phrase', 'Unknown Artist')
            title = release.get('title', 'Unknown Album')
            date = release.get('date', '')[:4]
            release_id = release.get('id', '')

            tracks = []
            for medium in release.get('medium-list', []):
                for track in medium.get('track-list', []):
                    rec = track.get('recording', {})
                    length_ms = int(rec.get('length', 0) or 0)
                    mins = length_ms // 60000
                    secs = (length_ms % 60000) // 1000
                    tracks.append({
                        'num': int(track.get('position', 0)),
                        'title': rec.get('title', f'Track {track.get("position", "?")}'),
                        'duration': f'{mins}:{secs:02d}'
                    })

            artwork_path = await self._fetch_artwork(release_id, disc_id)
            back_artwork_path = await self._fetch_artwork(release_id, disc_id, 'back')

            # Build alternatives list (all releases except the chosen one)
            alternatives = []
            for rel in release_list[1:]:
                alt_artist = rel.get('artist-credit-phrase', 'Unknown Artist')
                alt_title = rel.get('title', 'Unknown Album')
                alt_date = rel.get('date', '')[:4]
                alternatives.append({
                    'release_id': rel.get('id', ''),
                    'artist': alt_artist,
                    'title': alt_title,
                    'year': alt_date
                })

            metadata = {
                'disc_id': disc_id,
                'release_id': release_id,
                'title': title,
                'artist': artist,
                'year': date,
                'album': f'{title} ({date})' if date else title,
                'tracks': tracks,
                'track_count': len(tracks) or len(disc.tracks),
                'artwork': artwork_path,
                'back_artwork': back_artwork_path,
                'alternatives': alternatives
            }
            log.info(f"Metadata: {artist} — {title} ({date}), {len(tracks)} tracks, "
                     f"{len(alternatives)} alternatives, back={'yes' if back_artwork_path else 'no'}")
            return metadata

        except Exception as e:
            log.error(f"Metadata lookup failed: {e}")
            return None

    def _fallback_metadata(self, disc):
        """Basic metadata from TOC when MusicBrainz has no match."""
        tracks = [{'num': i, 'title': f'Track {i}', 'duration': ''}
                  for i in range(1, len(disc.tracks) + 1)]
        return {
            'disc_id': disc.id,
            'release_id': '',
            'title': 'Unknown Album',
            'artist': 'Unknown Artist',
            'year': '',
            'album': 'Unknown Album',
            'tracks': tracks,
            'track_count': len(disc.tracks),
            'artwork': None,
            'back_artwork': None,
            'alternatives': []
        }

    async def _fetch_artwork(self, release_id, disc_id, side='front'):
        """Download cover art from Cover Art Archive. Returns web-relative path."""
        suffix = '' if side == 'front' else f'-{side}'
        cached = self.cache_dir / f'{disc_id}{suffix}.jpg'
        if cached.exists():
            return f'assets/cd-cache/{disc_id}{suffix}.jpg'

        try:
            async with ClientSession() as session:
                url = f'https://coverartarchive.org/release/{release_id}/{side}-1200'
                async with session.get(url, timeout=15) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        cached.write_bytes(data)
                        log.info(f"Artwork ({side}) cached: {cached}")
                        return f'assets/cd-cache/{disc_id}{suffix}.jpg'
                    else:
                        log.debug(f"No {side} artwork (HTTP {resp.status})")
                        return None
        except Exception as e:
            log.warning(f"Artwork ({side}) fetch failed: {e}")
            return None



class CDPlayer:
    """Controls CD playback via mpv with gapless chapter-based navigation.

    Launches mpv with cdda:// (whole disc) plus a generated chapters file
    from the disc TOC. Track changes use chapter seeking for gapless audio.
    """

    PAUSE_TIMEOUT = 300  # 5 minutes
    CHAPTERS_FILE = '/tmp/beo-cd-chapters.txt'

    def __init__(self, device_path=CDROM_DEVICE):
        self.device_path = device_path
        self.process = None
        self.current_track = 0
        self.total_tracks = 0
        self.track_offsets = []  # start time in seconds for each track (index 0 = track 1)
        self.state = 'stopped'  # stopped | playing | paused
        self.shuffle = False
        self.repeat = False
        self._ipc_socket = '/tmp/beo-cd-mpv.sock'
        self._play_order = []  # shuffled track order
        self._ipc_task = None
        self._ipc_reader = None
        self._ipc_writer = None
        self._pause_timer = None
        self._pending_track = None  # track we're seeking to (suppress stale events)
        self._volume = 100.0  # track mpv volume internally (avoids IPC read races)
        # Detect mpv CD device flag (--cdrom-device pre-0.39, --cdda-device 0.39+)
        try:
            opts = subprocess.check_output(['mpv', '--list-options'], stderr=subprocess.DEVNULL, text=True)
            self._cd_device_flag = '--cdda-device' if '--cdda-device' in opts else '--cdrom-device'
        except Exception:
            self._cd_device_flag = '--cdrom-device'
        # Callbacks — set by CDService
        self._on_track_change = None  # track changed during playback (UI update)
        self._on_disc_end = None      # disc finished or shuffle order exhausted
        self._on_pause_timeout = None
        self._on_before_play = None

    # ── mpv lifecycle ──

    def _mpv_running(self):
        return self.process is not None and self.process.poll() is None

    def _write_chapters_file(self):
        """Generate OGM-style chapters file from disc TOC offsets."""
        if not self.track_offsets:
            return None
        with open(self.CHAPTERS_FILE, 'w') as f:
            for i, offset in enumerate(self.track_offsets):
                h = int(offset // 3600)
                m = int((offset % 3600) // 60)
                s = offset % 60
                f.write(f"CHAPTER{i+1:02d}={h:02d}:{m:02d}:{s:06.3f}\n")
                f.write(f"CHAPTER{i+1:02d}NAME=Track {i+1}\n")
        log.info(f"Chapters file written: {len(self.track_offsets)} tracks")
        return self.CHAPTERS_FILE

    async def _launch_mpv(self, start_track=1):
        """Launch mpv with cdda:// and a chapters file for track seeking."""
        if self._on_before_play:
            await self._on_before_play()

        try:
            os.unlink(self._ipc_socket)
        except FileNotFoundError:
            pass

        env = os.environ.copy()
        env.setdefault('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')
        cmd = [
            'mpv', '--ao=pulse',
            f'{self._cd_device_flag}={self.device_path}',
            'cdda://',
            '--no-video', '--no-terminal',
            '--gapless-audio=yes',
            f'--input-ipc-server={self._ipc_socket}',
            f'--start=#{start_track}',
        ]
        chapters_file = self._write_chapters_file()
        if chapters_file:
            cmd.append(f'--chapters-file={chapters_file}')

        self.process = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)

        # Wait for IPC socket and connect
        connected = False
        for _ in range(50):  # up to 5 s
            await asyncio.sleep(0.1)
            if self.process.poll() is not None:
                raise RuntimeError("mpv exited immediately")
            if os.path.exists(self._ipc_socket):
                try:
                    self._ipc_reader, self._ipc_writer = \
                        await asyncio.open_unix_connection(self._ipc_socket)
                    connected = True
                    break
                except (ConnectionRefusedError, FileNotFoundError):
                    continue

        if not connected:
            raise RuntimeError("Could not connect to mpv IPC")

        await self._send_ipc({'command': ['observe_property', 1, 'chapter']})
        self._ipc_task = asyncio.create_task(self._read_ipc_events())
        self.current_track = start_track
        self.state = 'playing'
        self._volume = 100.0
        self._pending_track = None
        log.info(f"mpv launched — cdda:// with {len(self.track_offsets)} chapters, start track {start_track}")

    # ── IPC communication ──

    async def _send_ipc(self, cmd_obj):
        if not self._ipc_writer:
            return
        try:
            self._ipc_writer.write(json.dumps(cmd_obj).encode() + b'\n')
            await self._ipc_writer.drain()
        except Exception as e:
            log.error(f"mpv IPC send error: {e}")

    async def _close_ipc(self):
        if self._ipc_writer:
            try:
                self._ipc_writer.close()
                await self._ipc_writer.wait_closed()
            except Exception:
                pass
        self._ipc_reader = None
        self._ipc_writer = None

    async def _read_ipc_events(self):
        """Background task — reads mpv IPC events for chapter changes."""
        try:
            while self._ipc_reader:
                line = await self._ipc_reader.readline()
                if not line:
                    break  # EOF — mpv closed
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (msg.get('event') == 'property-change'
                        and msg.get('name') == 'chapter'):
                    chapter = msg.get('data')
                    if isinstance(chapter, int) and chapter >= 0:
                        await self._handle_track_change(chapter)
        except asyncio.CancelledError:
            return
        except Exception as e:
            # Not just EOF: a handler exception lands here too, possibly
            # with mpv still alive and audibly playing.  Without killing it
            # we'd drop the handle (orphan) and — with repeat on — launch a
            # second mpv on top of it below.
            log.warning(f"IPC reader ended: {e}")

        # mpv exited naturally (not via stop() which cancels this task first)
        if self.state not in ('playing', 'paused'):
            return
        proc = self.process
        self.process = None
        self.state = 'stopped'
        await self._close_ipc()
        if proc is not None:
            loop = asyncio.get_running_loop()
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        await loop.run_in_executor(None, proc.wait, 2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        await loop.run_in_executor(None, proc.wait)
                else:
                    proc.wait()  # reap the zombie from a natural EOF exit
            except Exception:
                pass

        # Repeat → restart the disc
        if self.repeat:
            try:
                start = self._play_order[0] if (self.shuffle and self._play_order) else 1
                await self._launch_mpv(start_track=start)
                log.info(f"Repeat → restarted from track {start}")
                if self._on_track_change:
                    await self._on_track_change()
                return
            except Exception as e:
                log.error(f"Repeat restart failed: {e}")

        log.info(f"Disc ended after track {self.current_track}")
        if self._on_disc_end:
            await self._on_disc_end()

    # ── Track navigation ──

    async def _handle_track_change(self, pos):
        """React to mpv's playlist-pos property change."""
        new_track = pos + 1  # 0-based → 1-based

        if new_track == self.current_track:
            # Seek landed on the pre-set track — clear pending so gapless
            # advances aren't suppressed afterward.
            if self._pending_track is not None:
                self._pending_track = None
                log.info(f"Seek confirmed → track {new_track}")
                if self._on_track_change:
                    await self._on_track_change()
            return

        # If a seek is pending, only accept the matching track
        if self._pending_track is not None:
            if new_track == self._pending_track:
                self._pending_track = None
                self.current_track = new_track
                log.info(f"Seek confirmed → track {new_track}")
                if self._on_track_change:
                    await self._on_track_change()
            return  # ignore non-matching events while seeking

        # Auto-advance — shuffle redirect
        if self.shuffle and self._play_order:
            nxt = self._next_shuffle_track()
            if nxt is not None:
                await self._seek_track(nxt)
            elif self.repeat:
                self._rebuild_play_order()
                await self._seek_track(self._play_order[0])
            else:
                log.info("Shuffle order complete")
                await self.stop()
                if self._on_disc_end:
                    await self._on_disc_end()
            return

        # Normal sequential advance
        self.current_track = new_track
        log.info(f"Gapless → track {new_track}")
        if self._on_track_change:
            await self._on_track_change()

    async def _seek_track(self, track_num):
        """Seek to a track (1-based) in the running mpv."""
        self._pending_track = track_num
        self.current_track = track_num
        # Use absolute-time seek with the raw TOC offset — more direct path to
        # the CDDA demuxer than set_property chapter, ensuring the read head
        # actually repositions even with --gapless-audio buffering.
        if self.track_offsets and track_num <= len(self.track_offsets):
            await self._send_ipc({'command': ['seek', self.track_offsets[track_num - 1], 'absolute']})
        else:
            await self._send_ipc({'command': ['set_property', 'chapter', track_num - 1]})

    def _next_shuffle_track(self):
        if not self._play_order:
            return None
        try:
            idx = self._play_order.index(self.current_track)
            if idx < len(self._play_order) - 1:
                return self._play_order[idx + 1]
        except ValueError:
            pass
        return None

    # ── Public controls ──

    async def play_track(self, track_num):
        """Play a specific track. Reuses running mpv when possible."""
        self._cancel_pause_timer()
        if self._mpv_running() and self._ipc_writer:
            # mpv already running — just seek to the chapter
            self.state = 'playing'
            await self._send_ipc({'command': ['set_property', 'pause', False]})
            await self._seek_track(track_num)
        else:
            # Need to (re)launch mpv
            await self.stop()
            try:
                await self._launch_mpv(start_track=track_num)
            except Exception as e:
                log.error(f"Playback failed: {e}")
                self.state = 'stopped'

    async def play(self):
        if self.state == 'paused':
            self._cancel_pause_timer()
            if self._on_before_play:
                await self._on_before_play()
            await self._send_ipc({'command': ['set_property', 'pause', False]})
            self.state = 'playing'
        elif self.state == 'stopped':
            await self.play_track(self.current_track if self.current_track > 0 else 1)

    async def pause(self):
        if self.state == 'playing':
            await self._send_ipc({'command': ['set_property', 'pause', True]})
            self.state = 'paused'
            self._start_pause_timer()

    async def toggle_playback(self):
        if self.state == 'playing':
            await self.pause()
        else:
            await self.play()

    async def next_track(self):
        if not self._mpv_running():
            return
        if self.shuffle and self._play_order:
            nxt = self._next_shuffle_track()
            if nxt is not None:
                await self._seek_track(nxt)
            elif self.repeat:
                self._rebuild_play_order()
                await self._seek_track(self._play_order[0])
        elif self.current_track < self.total_tracks:
            await self._seek_track(self.current_track + 1)
        elif self.repeat:
            await self._seek_track(1)

    async def prev_track(self):
        if not self._mpv_running():
            return
        if self.shuffle and self._play_order:
            try:
                idx = self._play_order.index(self.current_track)
                if idx > 0:
                    await self._seek_track(self._play_order[idx - 1])
            except ValueError:
                pass
        elif self.current_track > 1:
            await self._seek_track(self.current_track - 1)

    # ── Shuffle / Repeat ──

    def toggle_shuffle(self):
        import random
        self.shuffle = not self.shuffle
        if self.shuffle:
            self._rebuild_play_order()
        log.info(f"Shuffle: {'on' if self.shuffle else 'off'}")

    def toggle_repeat(self):
        self.repeat = not self.repeat
        log.info(f"Repeat: {'on' if self.repeat else 'off'}")

    def _rebuild_play_order(self):
        import random
        self._play_order = list(range(1, self.total_tracks + 1))
        random.shuffle(self._play_order)
        if self.current_track in self._play_order:
            self._play_order.remove(self.current_track)
            self._play_order.insert(0, self.current_track)

    # ── Pause timer ──

    def _start_pause_timer(self):
        self._cancel_pause_timer()
        loop = asyncio.get_running_loop()
        self._pause_timer = loop.call_later(
            self.PAUSE_TIMEOUT, lambda: asyncio.ensure_future(self._pause_timeout()))

    def _cancel_pause_timer(self):
        if self._pause_timer:
            self._pause_timer.cancel()
            self._pause_timer = None

    async def _pause_timeout(self):
        log.info("Pause timeout — stopping playback")
        await self.stop()
        if self._on_pause_timeout:
            await self._on_pause_timeout()

    # ── Stop ──

    async def stop(self):
        self._cancel_pause_timer()
        if self._ipc_task:
            self._ipc_task.cancel()
            try:
                await self._ipc_task
            except asyncio.CancelledError:
                pass
            self._ipc_task = None
        await self._close_ipc()
        if self.process:
            self.process.terminate()
            try:
                await asyncio.get_running_loop().run_in_executor(
                    None, self.process.wait, 2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                # Reap after kill or the dead mpv lingers as a zombie
                await asyncio.get_running_loop().run_in_executor(
                    None, self.process.wait)
            self.process = None
        self.state = 'stopped'
        self._pending_track = None

    async def fade_volume(self, target, duration=0.5, steps=10):
        """Smoothly fade mpv volume to target (0-100) over duration seconds."""
        if not self._ipc_writer:
            return
        current = self._volume
        step_delay = duration / steps
        for i in range(1, steps + 1):
            vol = current + (target - current) * (i / steps)
            await self._send_ipc({'command': ['set_property', 'volume', vol]})
            await asyncio.sleep(step_delay)
        self._volume = target

    def get_status(self):
        return {
            'state': self.state,
            'current_track': self.current_track,
            'total_tracks': self.total_tracks,
            'shuffle': self.shuffle,
            'repeat': self.repeat
        }


class CDService(SourceBase):
    """Tie CD detection and metadata to MASS playback and managed ripping."""

    id = "cd"
    name = "CD"
    port = 8769
    player = "remote"
    manages_queue = True
    action_map = {
        "play": "toggle",
        "pause": "toggle",
        "go": "toggle",
        "next": "next",
        "prev": "prev",
        "right": "next",
        "left": "prev",
        "up": "next",
        "down": "prev",
        "stop": "stop",
        # Beo4 RANDOM key (0xC1) toggles shuffle while CD is active
        "random": "toggle_shuffle",
        "info": "announce",
        "track": "announce",
        "menu": "announce",
        "0": "play_track", "1": "play_track", "2": "play_track",
        "3": "play_track", "4": "play_track", "5": "play_track",
        "6": "play_track", "7": "play_track", "8": "play_track",
        "9": "play_track",
    }

    def __init__(self):
        super().__init__()
        self.drive = CDDrive()
        self.metadata_lookup = CDMetadata()
        self.audio = AudioOutputs()
        self.cdplayer = CDPlayer()
        self.metadata = None
        self._all_releases = []  # full release list from MusicBrainz
        self._metadata_task = None  # cancel on eject to prevent phantom playback
        self._is_first_detection = True  # suppress navigate (not autoplay) on startup
        self._external_drive_cache = None
        self._external_drive_cache_time = 0
        self._mass_target_id = ""
        self._remote_playback = False
        self._remote_transitioning = False
        self._remote_pause_requested = False
        self._remote_monitor_task = None
        self._stream_processes = set()
        self._stream_lock = asyncio.Lock()
        self._rip_resume_track = 0
        self._rip_resume_target = ""
        self._rip_resume_playback = False
        self._rip_queue_lock = asyncio.Lock()
        self._mass_rip_queue_target = ""
        self._mass_rip_queued_paths = set()
        self._mass_rip_uri_tracks = {}
        self._rip_speed_limited = False
        self._rip_speed_mode = "maximum"
        self._rip_speed_message = "Maximum drive speed"
        self._refresh_after_rip_started = False
        self._auto_eject_after_rip_started = False
        self._auto_ejecting = False
        self._mass_library_path = str(CD_RIP_DEFAULT_DESTINATION)
        self.ripper = CDRipManager(
            state_file=CD_RIP_STATE_FILE,
            default_destination=self._mass_library_path,
            default_automatic=bool(cfg("cd", "automatic_rip", default=False)),
            default_autoplay=bool(cfg("cd", "autoplay", default=True)),
            default_autoeject=bool(cfg("cd", "auto_eject", default=True)),
            on_update=self._on_rip_update,
            on_track_ready=self._on_rip_track_ready,
        )

    # ── SourceBase hooks ──

    async def on_start(self):
        # Wire the local fallback player callbacks. Normal network playback is
        # routed through Music Assistant so every configured target works.
        self.cdplayer._on_track_change = self._on_track_change
        self.cdplayer._on_disc_end = self._on_disc_end
        self.cdplayer._on_pause_timeout = self._on_pause_timeout
        self.cdplayer._on_before_play = None

        await self.drive.start_polling(
            on_drive_change=self._on_drive_change,
            on_disc_change=self._on_disc_change
        )

        # After grace period, treat any disc detection as a real insertion
        asyncio.get_running_loop().call_later(6, self._clear_first_detection)

        self._spawn(self._initialize_rip_destination(), name="cd_rip_destination")

    def _clear_first_detection(self):
        if self._is_first_detection:
            self._is_first_detection = False
            log.info("Startup grace ended — next disc detection will navigate")

    async def on_stop(self):
        await self._stop_playback()
        if self.ripper.active:
            await self.ripper.cancel()
        await self.drive.stop()


    def add_routes(self, app):
        app.router.add_get('/speakers', self._handle_speakers)
        app.router.add_get('/audio/{disc_id}/{track}', self._handle_audio_track)
        app.router.add_get('/playlist/{disc_id}/{track}.m3u', self._handle_track_playlist)

    async def handle_status(self) -> dict:
        allow_local_fallback = bool(cfg("cd", "allow_local_fallback", default=False))
        return {
            'drive_connected': self.drive.drive_connected,
            'disc_inserted': self.drive.disc_inserted,
            'metadata': self.metadata,
            'playback': self.cdplayer.get_status(),
            'audio_outputs': await self.audio.get_outputs() if allow_local_fallback else [],
            'current_sink': self.audio.current_sink if allow_local_fallback else None,
            'has_external_drive': self._detect_external_drive(),
            'ripping': self.ripper.active,
            'rip': self._rip_payload(),
            'playback_target_id': self._mass_target_id,
            'playback_backend': 'mass' if self._mass_target_id else 'unavailable',
            'capabilities': {
                'discid': HAS_DISCID,
                'musicbrainz': HAS_MB,
                'zeroconf': HAS_ZEROCONF,
                'pyudev': HAS_PYUDEV
            }
        }

    async def _initialize_rip_destination(self):
        """Use the active MASS filesystem provider as the initial destination."""
        await self._selected_target_id()
        configured = str(cfg("cd", "rip_destination", default="") or "").strip()
        if configured:
            self._mass_library_path = configured
            if not self.ripper.settings.get("destination"):
                await self.ripper.set_settings(destination=configured)
            return
        for attempt in range(3):
            try:
                async with self._http_session.get(
                        f"{MASS_SERVICE_URL}/library/path", timeout=8) as response:
                    if response.status == 200:
                        payload = await response.json()
                        path = str(payload.get("path") or "").strip()
                        if path:
                            self._mass_library_path = path
                            # Replace only the built-in default. A user's saved
                            # choice remains authoritative.
                            if self.ripper.settings.get("destination") == str(CD_RIP_DEFAULT_DESTINATION):
                                await self.ripper.set_settings(destination=path)
                            return
            except Exception as exc:
                if attempt == 2:
                    log.warning("Unable to resolve MASS library path: %s", exc)
            await asyncio.sleep(2 * (attempt + 1))

    @staticmethod
    def _local_ip():
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("192.0.2.1", 9))
            return sock.getsockname()[0]
        except OSError:
            return "127.0.0.1"
        finally:
            sock.close()

    def _public_base_url(self):
        configured = str(
            os.getenv("CD_PUBLIC_BASE_URL")
            or cfg("cd", "stream_base_url", default="")
            or ""
        ).strip()
        return configured.rstrip('/') if configured else f"http://{self._local_ip()}:{self.port}"

    def _track_url(self, track_number):
        disc_id = urllib.parse.quote(str((self.metadata or {}).get("disc_id") or "disc"), safe="")
        tracks = (self.metadata or {}).get("tracks") or []
        title = ""
        if 0 < int(track_number) <= len(tracks):
            title = tracks[int(track_number) - 1].get("title") or ""
        query = urllib.parse.urlencode({"title": title}) if title else ""
        url = f"{self._public_base_url()}/audio/{disc_id}/{int(track_number)}"
        return f"{url}?{query}" if query else url

    def _track_title(self, track_number):
        tracks = (self.metadata or {}).get("tracks") or []
        if 0 < int(track_number) <= len(tracks):
            return str(tracks[int(track_number) - 1].get("title") or f"Track {track_number}")
        return f"Track {track_number}"

    def _playlist_url(self, track_number):
        disc_id = urllib.parse.quote(
            str((self.metadata or {}).get("disc_id") or "disc"), safe="")
        return f"{self._public_base_url()}/playlist/{disc_id}/{int(track_number)}.m3u"

    def _absolute_artwork_url(self):
        artwork = str((self.metadata or {}).get("artwork") or "").strip()
        if not artwork:
            return ""
        if urllib.parse.urlparse(artwork).scheme in {"http", "https"}:
            return artwork
        path = urllib.parse.quote(artwork.lstrip('/'), safe='/')
        return f"http://{self._local_ip()}/{path}"

    @staticmethod
    def _m3u_value(value):
        return re.sub(r"[\r\n]+", " ", str(value or "")).replace("||", " ").strip()

    def _track_order_from(self, start_track):
        total = int(getattr(self.cdplayer, "total_tracks", 0) or 0)
        if total <= 0:
            return []
        start_track = max(1, min(total, int(start_track or 1)))
        if self.cdplayer.shuffle:
            order = list(self.cdplayer._play_order or range(1, total + 1))
            if start_track in order:
                position = order.index(start_track)
                return order[position:] + order[:position]
        return list(range(start_track, total + 1))

    async def _selected_target_id(self, data=None):
        data = data if isinstance(data, dict) else {}
        target_id = str(
            data.get("target_player_id")
            or data.get("audio_target_id")
            or (data.get("playback") or {}).get("audio_target_id")
            or ""
        ).strip()
        if target_id:
            self._mass_target_id = target_id
            return target_id
        try:
            async with self._http_session.get(ROUTER_PLAYBACK_URL, timeout=4) as response:
                if response.status == 200:
                    payload = await response.json()
                    target_id = str(payload.get("audio_target_id") or "").strip()
        except Exception as exc:
            log.warning("Unable to read playback target: %s", exc)
        if target_id:
            self._mass_target_id = target_id
        return target_id or self._mass_target_id

    async def _mass_request(self, endpoint, *, method="POST", payload=None, timeout=15):
        try:
            request = self._http_session.post if method == "POST" else self._http_session.get
            kwargs = {"timeout": timeout}
            if method == "POST":
                kwargs["json"] = payload or {}
            async with request(f"{MASS_SERVICE_URL}{endpoint}", **kwargs) as response:
                body = await response.json()
                if response.status >= 400:
                    log.warning("MASS CD integration %s failed HTTP %d: %s", endpoint, response.status, body)
                    return None
                return body
        except Exception as exc:
            log.warning("MASS CD integration %s failed: %s", endpoint, exc)
            return None

    @staticmethod
    def _rip_path_key(path):
        return str(Path(str(path or "")).resolve(strict=False)) if path else ""

    @staticmethod
    def _track_number_for_rip_path(path):
        match = re.match(r"^(\d{1,3})\s*-", Path(str(path or "")).name)
        return int(match.group(1)) if match else 0

    def _completed_rip_paths_from(self, track_number):
        paths = []
        for number in self._track_order_from(track_number):
            path = self.ripper.completed_track_path(number)
            if path:
                paths.append(str(path))
        if not paths:
            path = self.ripper.completed_track_path(track_number)
            if path:
                paths.append(str(path))
        return paths

    async def _queue_ripped_paths(
            self, paths, target_id, *, option="add", start_playback=False):
        """Queue only atomically-finalised FLACs through MASS filesystem_local."""
        if not target_id:
            return False
        path_keys = [self._rip_path_key(path) for path in paths if path]
        path_keys = list(dict.fromkeys(filter(None, path_keys)))
        if not path_keys:
            return False

        lock = getattr(self, "_rip_queue_lock", None)
        if lock is None:
            lock = self._rip_queue_lock = asyncio.Lock()
        async with lock:
            queued_target = getattr(self, "_mass_rip_queue_target", "")
            queued_paths = getattr(self, "_mass_rip_queued_paths", set())
            if queued_target != target_id:
                queued_paths = set()
                self._mass_rip_uri_tracks = {}
            requested_option = "replace" if start_playback else option
            if requested_option == "add":
                path_keys = [path for path in path_keys if path not in queued_paths]
                if not path_keys:
                    return True

            response = await self._mass_request(
                "/filesystem/queue",
                payload={
                    "target_player_id": target_id,
                    "paths": path_keys,
                    "option": requested_option,
                    "start_playback": bool(start_playback),
                },
                timeout=30,
            )
            if not response or response.get("state") == "error":
                return False

            self._mass_rip_queue_target = target_id
            if requested_option == "replace":
                queued_paths = set(path_keys)
                self._mass_rip_uri_tracks = {}
            else:
                queued_paths = set(queued_paths)
                queued_paths.update(path_keys)
            self._mass_rip_queued_paths = queued_paths

            path_track_numbers = {
                self._rip_path_key(path): self._track_number_for_rip_path(path)
                for path in path_keys
            }
            for item in response.get("queued_items") or []:
                if not isinstance(item, dict):
                    continue
                item_path = self._rip_path_key(item.get("path"))
                uri = urllib.parse.unquote(str(item.get("uri") or "").strip())
                track = path_track_numbers.get(item_path) or self._track_number_for_rip_path(item_path)
                if uri and track:
                    self._mass_rip_uri_tracks[uri] = track
            return True

    def _track_number_from_remote_uri(self, uri):
        decoded = urllib.parse.unquote(str(uri or "").strip())
        if not decoded:
            return 0
        mapped = getattr(self, "_mass_rip_uri_tracks", {}).get(decoded)
        if mapped:
            return int(mapped)
        match = re.search(r"/(?:audio|playlist)/[^/]+/(\d+)(?:\.m3u)?(?:\?|$)", decoded)
        if not match:
            match = re.search(r"/(\d{1,3})\s*-\s*[^/]*\.flac(?:\?|$)", decoded, re.I)
        if not match:
            return 0
        track = int(match.group(1))
        total = int(getattr(self.cdplayer, "total_tracks", 0) or 0)
        return track if total <= 0 or 1 <= track <= total else 0

    async def _start_mass_playback(self, track_number, data=None):
        target_id = await self._selected_target_id(data)
        if not target_id:
            return False
        if self.ripper.active and not self.ripper.completed_track_path(track_number):
            self._rip_resume_track = int(track_number)
            self._rip_resume_target = target_id
            self._rip_resume_playback = True
            self.cdplayer.current_track = int(track_number)
            self.cdplayer.state = "paused"
            return False

        completed_path = self.ripper.completed_track_path(track_number)
        # Raw optical playback remains a one-track external stream so MASS
        # never probes several CDDA reads concurrently. Completed FLACs take
        # the native filesystem provider path below instead.
        self._remote_transitioning = True
        try:
            await self._terminate_stream_processes()
            await self.cdplayer.stop()
            if completed_path:
                paths = self._completed_rip_paths_from(track_number)
                if not await self._queue_ripped_paths(
                        paths, target_id, option="replace", start_playback=True):
                    return False
            else:
                tracks = (self.metadata or {}).get("tracks") or []
                track = tracks[int(track_number) - 1] if 0 < int(track_number) <= len(tracks) else {}
                response = await self._mass_request(
                    "/external/play",
                    payload={
                        "target_player_id": target_id,
                        # A full Track descriptor is assembled by the MASS bridge.
                        # This preserves BS5C metadata for genuine disc streams.
                        "url": self._track_url(track_number),
                        "title": self._track_title(track_number),
                        "artist": (self.metadata or {}).get("artist", ""),
                        "album": (self.metadata or {}).get("title", "Audio CD"),
                        "year": (self.metadata or {}).get("year", ""),
                        "release_id": (self.metadata or {}).get("release_id", ""),
                        "disc_id": (self.metadata or {}).get("disc_id", ""),
                        "track_number": int(track_number),
                        "duration": int(round(parse_duration(track.get("duration")))),
                        "content_type": "wav",
                        "image_url": self._absolute_artwork_url(),
                    },
                    timeout=30,
                )
                if not response or response.get("state") == "error":
                    return False
            self._remote_playback = True
            self._remote_pause_requested = False
            self._mass_target_id = target_id
            self.cdplayer.current_track = int(track_number)
            self.cdplayer.state = "playing"
            self._start_remote_monitor()
            return True
        finally:
            self._remote_transitioning = False

    async def _play_track(self, track_number, data=None):
        track_number = max(1, min(int(self.cdplayer.total_tracks or 1), int(track_number or 1)))
        if await self._start_mass_playback(track_number, data):
            return True
        if self.ripper.active:
            return False
        if bool(cfg("cd", "allow_local_fallback", default=False)):
            log.warning("MASS target playback failed; using explicitly enabled local fallback")
            self._remote_playback = False
            await self.cdplayer.play_track(track_number)
            return True
        raise RuntimeError("Selected playback target is unavailable; local fallback is disabled")

    async def _play(self, data=None):
        if self._remote_playback and self.cdplayer.state == "paused":
            response = await self._mass_control("play")
            if response:
                self._remote_pause_requested = False
                self.cdplayer.state = "playing"
                self.cdplayer._cancel_pause_timer()
                return True
        return await self._play_track(self.cdplayer.current_track or 1, data)

    async def _pause(self):
        if self._rip_resume_playback and not self._remote_playback:
            # Autoplay/manual play may be waiting for a FLAC to finish. A pause
            # during that window cancels the deferred start instead of being
            # mistaken for an optical-drive transport command.
            self._rip_resume_playback = False
            self.cdplayer.state = "paused"
            return True
        if self._remote_playback:
            # Some transports report IDLE after a pause (and Music Assistant
            # deliberately stops a long-paused queue). Remember the user's
            # intent so the monitor never mistakes that for end-of-track.
            self._remote_pause_requested = True
            if await self._mass_control("pause"):
                self.cdplayer.state = "paused"
                self.cdplayer._start_pause_timer()
                return True
            self._remote_pause_requested = False
            return False
        await self.cdplayer.pause()
        return True

    async def _toggle_playback(self, data=None):
        if self.cdplayer.state == "playing":
            return await self._pause()
        return await self._play(data)

    async def _next_track(self):
        if self._remote_playback or self.ripper.active:
            track = self._adjacent_remote_track(1)
            return bool(track and await self._start_mass_playback(
                track, {"target_player_id": self._mass_target_id}))
        await self.cdplayer.next_track()
        return True

    async def _prev_track(self):
        if self._remote_playback or self.ripper.active:
            track = self._adjacent_remote_track(-1)
            return bool(track and await self._start_mass_playback(
                track, {"target_player_id": self._mass_target_id}))
        await self.cdplayer.prev_track()
        return True

    def _adjacent_remote_track(self, direction):
        total = int(self.cdplayer.total_tracks or 0)
        if total <= 0:
            return 0
        if self.cdplayer.shuffle:
            order = list(self.cdplayer._play_order or range(1, total + 1))
        else:
            order = list(range(1, total + 1))
        try:
            position = order.index(int(self.cdplayer.current_track or 1))
        except ValueError:
            position = 0
        next_position = position + (1 if direction >= 0 else -1)
        if 0 <= next_position < len(order):
            return order[next_position]
        if self.cdplayer.repeat:
            return order[0] if direction >= 0 else order[-1]
        return 0

    async def _mass_control(self, action):
        if not self._mass_target_id:
            return False
        response = await self._mass_request(
            "/external/control",
            payload={"target_player_id": self._mass_target_id, "action": action},
        )
        return bool(response and response.get("state") != "error")

    async def _stop_playback(self):
        was_remote = self._remote_playback
        self._remote_playback = False
        self._remote_pause_requested = False
        stop_task = None
        if was_remote and self._mass_target_id:
            stop_task = self._spawn(self._mass_control("stop"), name="cd_mass_stop")
        await self._terminate_stream_processes()
        if stop_task:
            await stop_task
        await self.cdplayer.stop()
        task = self._remote_monitor_task
        self._remote_monitor_task = None
        if task and task is not asyncio.current_task() and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def _start_remote_monitor(self):
        if self._remote_monitor_task and not self._remote_monitor_task.done():
            return
        self._remote_monitor_task = self._spawn(
            self._remote_monitor_loop(), name="cd_mass_playback_monitor")

    async def _remote_monitor_loop(self):
        last_track = self.cdplayer.current_track
        last_state = self.cdplayer.state
        while self._remote_playback and self._mass_target_id:
            query = urllib.parse.urlencode({"target_player_id": self._mass_target_id})
            response = await self._mass_request(
                f"/external/status?{query}", method="GET", timeout=8)
            if response:
                state = str(response.get("state") or "idle").lower()
                uri = str(response.get("current_uri") or "")
                remote_track = self._track_number_from_remote_uri(uri)
                if remote_track:
                    self.cdplayer.current_track = remote_track
                if state in {"playing", "buffering"}:
                    # A successful pause request can race one final stale
                    # "playing" poll. Preserve the explicit pause intent until
                    # the user asks to play again; otherwise that stale poll can
                    # turn a following IDLE state into a spurious next-track.
                    self.cdplayer.state = (
                        "paused" if self._remote_pause_requested else "playing")
                elif state == "paused":
                    self._remote_pause_requested = True
                    self.cdplayer.state = "paused"
                elif state in {"idle", "stopped", "off", "unavailable"} and last_state in {"playing", "paused"}:
                    if self._remote_transitioning:
                        await asyncio.sleep(0.25)
                        continue
                    if self._remote_pause_requested or last_state == "paused":
                        # Preserve the paused track. Pressing play will resume the
                        # queue when possible and otherwise reopen this same track.
                        self.cdplayer.state = "paused"
                        if last_state != "paused":
                            last_state = "paused"
                            await self.register("paused")
                            await self._broadcast_cd_update()
                        await asyncio.sleep(1)
                        continue
                    next_track = self._adjacent_remote_track(1)
                    if next_track and await self._start_mass_playback(
                            next_track, {"target_player_id": self._mass_target_id}):
                        last_track = self.cdplayer.current_track
                        last_state = self.cdplayer.state
                        await self.register("playing")
                        await self._broadcast_cd_update()
                        continue
                    if self.ripper.active and self._rip_resume_playback:
                        # The next FLAC has not landed yet. Leave the source
                        # paused and let _on_rip_track_ready restart it from
                        # the native filesystem item as soon as rename commits.
                        self._remote_playback = False
                        self.cdplayer.state = "paused"
                        await self.register("paused")
                        await self._broadcast_cd_update()
                        return
                    self._remote_playback = False
                    self.cdplayer.state = "stopped"
                    await self._on_disc_end()
                    return
                if self.cdplayer.current_track != last_track or self.cdplayer.state != last_state:
                    last_track = self.cdplayer.current_track
                    last_state = self.cdplayer.state
                    await self.register("playing" if last_state == "playing" else "paused")
                    await self._broadcast_cd_update()
            await asyncio.sleep(1)

    async def _handle_track_playlist(self, request):
        """Serve one richly-described CD track as a Music Assistant M3U."""
        if not self.metadata:
            raise web.HTTPNotFound(text="No audio CD is available")
        expected_disc = str(self.metadata.get("disc_id") or "disc")
        if urllib.parse.unquote(request.match_info.get("disc_id", "")) != expected_disc:
            raise web.HTTPNotFound(text="The requested disc is no longer inserted")
        try:
            track_number = int(request.match_info.get("track", "0"))
        except ValueError:
            raise web.HTTPBadRequest(text="Invalid track")
        if track_number < 1 or track_number > int(self.cdplayer.total_tracks or 0):
            raise web.HTTPNotFound(text="Track not found")

        tracks = self.metadata.get("tracks") or []
        track = tracks[track_number - 1] if track_number <= len(tracks) else {}
        title = self._m3u_value(track.get("title") or f"Track {track_number}")
        artist = self._m3u_value(self.metadata.get("artist") or "Unknown Artist")
        album = self._m3u_value(self.metadata.get("title") or "Unknown Album")
        year = self._m3u_value(self.metadata.get("year") or "")
        album_display = f"{album} ({year})" if year else album
        album_id = self._m3u_value(
            self.metadata.get("release_id") or self.metadata.get("disc_id") or album)
        audio_url = self._track_url(track_number)
        duration = int(round(parse_duration(track.get("duration"))))
        content_type = "flac" if (
            self.ripper.active or self.ripper.completed_track_path(track_number)
        ) else "wav"

        lines = [
            "#EXTM3U",
            f"#PLAYLIST:{album_display}",
            f"#EXTMA:media_type=track||name={title}",
            f"#EXTPROV:builtin||{audio_url}||||{content_type}||44100||16||1411200",
            f"#EXTARTIST:{artist}||builtin||{artist}||builtin",
            f"#EXTALBUM:{album_display}||builtin||{album_id}||builtin||",
        ]
        if artwork_url := self._absolute_artwork_url():
            lines.append(f"#EXTIMG:thumb||{self._m3u_value(artwork_url)}||builtin||true")
        lines.extend((
            f"#EXTINF:{duration},{artist} - {title}",
            audio_url,
            "",
        ))
        return web.Response(
            text="\n".join(lines),
            content_type="audio/x-mpegurl",
            headers={**self._cors_headers(), "Cache-Control": "no-store"},
        )

    async def _handle_audio_track(self, request):
        if not self.metadata:
            raise web.HTTPNotFound(text="No audio CD is available")
        expected_disc = str(self.metadata.get("disc_id") or "disc")
        if urllib.parse.unquote(request.match_info.get("disc_id", "")) != expected_disc:
            raise web.HTTPNotFound(text="The requested disc is no longer inserted")
        try:
            track_number = int(request.match_info.get("track", "0"))
        except ValueError:
            raise web.HTTPBadRequest(text="Invalid track")
        if track_number < 1 or track_number > int(self.cdplayer.total_tracks or 0):
            raise web.HTTPNotFound(text="Track not found")

        ready = self.ripper.completed_track_path(track_number)
        if not ready and not self.drive.disc_inserted:
            raise web.HTTPNotFound(text="The requested disc is no longer inserted")
        if request.method == "HEAD":
            content_type = "audio/flac" if ready or self.ripper.active else "audio/wav"
            return web.Response(content_type=content_type, headers=self._cors_headers())
        if not ready and self.ripper.active:
            ready = await self.ripper.wait_for_track(track_number, timeout=1800)
        if ready:
            response = web.FileResponse(ready, headers={
                **self._cors_headers(),
                "Cache-Control": "no-store",
                "Content-Type": "audio/flac",
            })
            return response
        if self.ripper.active:
            raise web.HTTPServiceUnavailable(text="Track is still being ripped")
        if self._stream_lock.locked():
            raise web.HTTPServiceUnavailable(
                text="The optical drive is already streaming a track",
                headers={**self._cors_headers(), "Retry-After": "1"},
            )
        async with self._stream_lock:
            response = web.StreamResponse(
                status=200,
                headers={
                    **self._cors_headers(),
                    "Content-Type": "audio/wav",
                    "Cache-Control": "no-store",
                    "Accept-Ranges": "none",
                },
            )
            await response.prepare(request)
            process = await asyncio.create_subprocess_exec(
                "cdparanoia", "-d", self.drive.device_path, "-w",
                str(track_number), "-",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            self._stream_processes.add(process)
            try:
                while True:
                    chunk = await process.stdout.read(64 * 1024)
                    if not chunk:
                        break
                    await response.write(chunk)
                await process.wait()
                if process.returncode not in {0, -15, -9}:
                    log.error("CD stream track %d failed with exit code %d", track_number, process.returncode)
            except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
                if process.returncode is None:
                    process.terminate()
                    await process.wait()
            finally:
                self._stream_processes.discard(process)
            try:
                await response.write_eof()
            except (ConnectionResetError, RuntimeError):
                pass
            return response

    async def _terminate_stream_processes(self):
        processes = list(self._stream_processes)
        for process in processes:
            if process.returncode is None:
                process.terminate()
        for process in processes:
            if process.returncode is not None:
                continue
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        self._stream_processes.difference_update(processes)

    async def handle_resync(self) -> dict:
        """Re-register source state and metadata. Called by input.py on new WebSocket client."""
        if self.drive.disc_inserted or (self._remote_playback and self.metadata):
            state = self.cdplayer.state if self.cdplayer.state in ('playing', 'paused') else 'available'
            await self.register(state)
            if self.metadata:
                await self._broadcast_cd_update()
            log.info("Resync: state=%s, metadata=%s", state, self.metadata is not None)
            return {'status': 'ok', 'resynced': True}
        return {'status': 'ok', 'resynced': False}

    async def activate_playback(self):
        """Resume or start CD playback on source button press."""
        has_ripped_audio = bool(
            self.metadata and any(
                self.ripper.completed_track_path(track)
                for track in range(1, int(self.cdplayer.total_tracks or 0) + 1)
            )
        )
        if (not self.drive.disc_inserted and not has_ripped_audio) or self.cdplayer.total_tracks == 0:
            return
        if self.cdplayer.state in ('paused', 'stopped'):
            await self._play()
        await self._broadcast_cd_update()

    async def handle_raw_action(self, action, data):
        """Handle CD button before action_map."""
        # CD button → start playback if disc present
        if action == 'cd':
            return ('_cd_button', data)
        return None

    async def get_queue(self, start=0, max_items=50) -> dict:
        """Return CD tracklist as a queue for Beo6."""
        if not self.metadata:
            return {"tracks": [], "current_index": -1, "total": 0}
        all_tracks = self.metadata.get('tracks', [])
        artwork = self.metadata.get('artwork', '')
        current = self.cdplayer.current_track  # 1-based
        end = min(start + max_items, len(all_tracks))
        tracks = []
        for i in range(start, end):
            t = all_tracks[i]
            tracks.append({
                "id": f"q:{i}",
                "title": t.get('title', f'Track {t.get("num", i + 1)}'),
                "artist": self.metadata.get('artist', ''),
                "album": self.metadata.get('title', ''),
                "artwork": artwork or '',
                "index": i,
                "current": (i == current - 1),
            })
        return {
            "tracks": tracks,
            "current_index": current - 1,
            "total": len(all_tracks),
        }

    async def handle_command(self, cmd, data) -> dict:
        if cmd == '_cd_button':
            return await self._handle_cd_button_action(data)
        elif cmd == 'play':
            started = await self._play(data)
            await self.register(
                'playing' if started else 'paused', auto_power=bool(started))
            await self._broadcast_cd_update()
        elif cmd == 'pause':
            await self._pause()
            await self.register('paused')
            await self._broadcast_cd_update()
        elif cmd == 'toggle':
            await self._toggle_playback(data)
            if self.cdplayer.state == 'playing':
                await self.register('playing', auto_power=True)
            else:
                await self.register('paused')
            await self._broadcast_cd_update()
        elif cmd == 'next':
            await self._next_track()
            await self._broadcast_cd_update()
        elif cmd == 'prev':
            await self._prev_track()
            await self._broadcast_cd_update()
        elif cmd == 'stop':
            self._rip_resume_playback = False
            await self._stop_playback()
            if not self.drive.disc_inserted:
                self.metadata = None
                await self.ripper.reset_for_disc()
                await self.register('gone')
            else:
                await self.register('paused')
                await self._broadcast_cd_update()
        elif cmd == 'play_track':
            # Track number from action ("5") or explicit track field
            track = data.get('track') or int(data.get('action', 1))
            started = await self._play_track(track, data)
            await self.register(
                'playing' if started else 'paused', auto_power=bool(started))
            await self._broadcast_cd_update()
        elif cmd == 'play_index':
            track = data.get('index', 0) + 1  # 0-based index → 1-based track
            started = await self._play_track(track, data)
            await self.register(
                'playing' if started else 'paused', auto_power=bool(started))
            await self._broadcast_cd_update()
        elif cmd == 'eject':
            await self._stop_playback()
            if self.ripper.active:
                await self.ripper.cancel()
            await self.register('available')
            await self.drive.eject()
            # _on_disc_change will send 'gone' when disc is actually ejected
        elif cmd == 'set_speaker':
            await self.audio.set_output(data.get('sink', ''))
        elif cmd == 'toggle_shuffle':
            self.cdplayer.toggle_shuffle()
            if self._remote_playback:
                await self._start_mass_playback(self.cdplayer.current_track or 1, data)
            await self._broadcast_cd_update()
        elif cmd == 'toggle_repeat':
            self.cdplayer.toggle_repeat()
            await self._broadcast_cd_update()
        elif cmd == 'toggle_autoplay':
            await self.ripper.set_settings(
                autoplay=not bool(self.ripper.settings.get('autoplay', True)))
            await self._broadcast_cd_update()
        elif cmd == 'use_release':
            await self._use_alternative_release(data.get('release_id', ''))
        elif cmd in {'import', 'rip_start'}:
            await self._start_rip(data=data, manual=True)
        elif cmd == 'rip_cancel':
            self._rip_resume_playback = False
            await self.ripper.cancel()
        elif cmd == 'rip_confirm':
            if bool(data.get('rip')):
                prompt = self.ripper.status.get('prompt') or {}
                await self.ripper.notify_prompt(None)
                await self._start_rip(
                    data=data,
                    manual=False,
                    play_when_ready=bool(prompt.get('play_when_ready')),
                )
            else:
                await self.ripper.mark_skipped('Automatic rip skipped')
        elif cmd == 'set_rip_settings':
            await self._set_rip_settings(data)
        elif cmd == 'announce':
            await self._announce_track()
        else:
            return {'status': 'error', 'message': f'Unknown: {cmd}'}

        return {'playback': self.cdplayer.get_status(), 'rip': self._rip_payload()}

    # ── Drive event handlers ──

    async def _on_drive_change(self, connected):
        pass  # Status is available via /status for the debug screen

    async def _on_disc_change(self, inserted):
        self._rip_resume_playback = False
        self._rip_resume_track = 0
        self._rip_resume_target = ""
        if inserted:
            self._auto_eject_after_rip_started = False
            self._auto_ejecting = False
            self._mass_rip_queue_target = ""
            self._mass_rip_queued_paths = set()
            self._mass_rip_uri_tracks = {}
            import time
            self._action_ts = time.monotonic()  # disc insert = user action
            # Immediately discard the previous disc's title/artwork while the
            # new TOC and MusicBrainz result are loading.
            await self._stop_playback()
            self.metadata = None
            self._all_releases = []
            self.cdplayer.current_track = 0
            self.cdplayer.total_tracks = 0
            self.cdplayer.track_offsets = []
            await self.ripper.reset_for_disc()
            await self._broadcast_cd_clear()
            is_startup = self._is_first_detection
            self._is_first_detection = False
            navigate = not is_startup
            autoplay = (
                not is_startup
                and bool(self.ripper.settings.get('autoplay', True))
            )  # don't autoplay a disc already in the drive at boot
            # Register as available — router adds menu item
            await self.register('available', navigate=navigate)
            self._metadata_task = asyncio.create_task(
                self._fetch_and_update_metadata(
                    autoplay=autoplay,
                    navigate=navigate,
                    auto_rip=not is_startup,
                ))
        else:
            preserve_ripped_playback = bool(
                self._auto_ejecting
                and self._remote_playback
                and self.ripper.status.get('status') == 'completed'
            )
            self._auto_ejecting = False
            # Cancel in-flight metadata fetch to prevent phantom playback
            if self._metadata_task and not self._metadata_task.done():
                self._metadata_task.cancel()
                self._metadata_task = None
            if self.ripper.active:
                await self.ripper.cancel()
            if preserve_ripped_playback:
                # Every remaining stream is backed by a completed FLAC. Keep
                # the global Playing queue alive after the tray opens.
                await self.register('playing', auto_power=False)
                await self._broadcast_cd_update()
            else:
                await self._stop_playback()
                self.metadata = None
                await self.ripper.reset_for_disc()
                # Unregister — router removes menu item and deactivates if active
                await self.register('gone')

    async def _on_track_change(self):
        """Called when the current track changes during gapless playback."""
        await self.register('playing')  # refresh router grace period
        await self._broadcast_cd_update()

    async def _on_disc_end(self):
        """Called when disc playback reaches the end."""
        log.info("Disc ended — deactivating CD source")
        self.cdplayer.current_track = 0  # next play() starts from track 1
        if self.drive.disc_inserted:
            await self.register('available')
            await self._broadcast_cd_update()
        else:
            self.metadata = None
            await self.ripper.reset_for_disc()
            await self.register('gone')

    async def _on_pause_timeout(self):
        """Called when paused too long — release drive, deactivate source."""
        log.info("Pause timeout — deactivating CD source")
        await self.register('available')
        await self._broadcast_cd_update()

    async def _fetch_and_update_metadata(
            self, autoplay=True, navigate=True, auto_rip=True):
        self.metadata = await self.metadata_lookup.lookup()
        # Save track offsets from disc TOC for chapter-based seeking
        disc = self.metadata_lookup.last_disc
        if disc:
            self.cdplayer.track_offsets = [t.offset / 75.0 for t in disc.tracks]
            self.cdplayer.total_tracks = len(disc.tracks)
            log.info(f"TOC offsets saved: {len(disc.tracks)} tracks")
            # If metadata lookup failed entirely, create minimal fallback
            if not self.metadata:
                self.metadata = {
                    'disc_id': disc.id,
                    'title': 'Unknown Album',
                    'artist': 'Unknown Artist',
                    'year': '',
                    'album': 'Unknown Album',
                    'tracks': [{'num': i, 'title': f'Track {i}', 'duration': ''}
                               for i in range(1, len(disc.tracks) + 1)],
                    'track_count': len(disc.tracks),
                    'artwork': None,
                    'back_artwork': None,
                    'alternatives': []
                }
        if self.metadata:
            self.cdplayer.total_tracks = self.metadata.get('track_count', 0)
            if self.drive.disc_inserted:
                await self.ripper.discover_partial(
                    self.metadata,
                    destination=self.ripper.settings.get('destination'),
                )
                # Automatic ripping and autoplay are separate settings. When
                # both are enabled, playback waits for the first reliable FLAC
                # and then starts through the selected MASS target.
                auto_rip_result = (
                    await self._check_auto_rip(play_when_ready=autoplay)
                    if auto_rip else 'disabled')
                if auto_rip_result == 'started':
                    await self.register('available', navigate=navigate, auto_power=False)
                elif autoplay:
                    await self._play_track(1)
                    await self.register('playing', navigate=navigate, auto_power=True)
                else:
                    await self.register('available', navigate=navigate, auto_power=False)
                await self._broadcast_cd_update()
                if autoplay and auto_rip_result != 'started':
                    artist = self.metadata.get('artist', '')
                    album = self.metadata.get('title', '')
                    if album and album != 'Unknown Album':
                        tts = f"{album}, by {artist}" if artist and artist != 'Unknown Artist' else album
                    else:
                        tts = "Playing a CD"
                    await self._announce_track(volume=70, text=tts)
            else:
                await self._broadcast_cd_update()

    async def _broadcast_cd_clear(self):
        """Clear the previous disc from both the CD and global Playing views."""
        await self.broadcast('cd_update', {
            'clear': True,
            'state': 'loading',
            'title': '',
            'artist': '',
            'album': '',
            'year': '',
            'artwork': None,
            'back_artwork': None,
            'tracks': [],
            'track_count': 0,
            'current_track': 0,
            'rip': self._rip_payload(),
        })
        await self.post_media_update(
            title='', artist='', album='', artwork='', back_artwork='',
            state='available', reason='disc_inserted')

    async def _broadcast_cd_update(self):
        if not self.metadata:
            return
        cd_data = {
            'title': self.metadata.get('title', 'Unknown Album'),
            'artist': self.metadata.get('artist', 'Unknown Artist'),
            'album': self.metadata.get('album', ''),
            'year': self.metadata.get('year', ''),
            'artwork': self.metadata.get('artwork'),
            'back_artwork': self.metadata.get('back_artwork'),
            'tracks': self.metadata.get('tracks', []),
            'track_count': self.metadata.get('track_count', 0),
            'current_track': self.cdplayer.current_track,
            'state': self.cdplayer.state,
            'alternatives': self.metadata.get('alternatives', []),
            'shuffle': self.cdplayer.shuffle,
            'repeat': self.cdplayer.repeat,
            'autoplay': bool(self.ripper.settings.get('autoplay', True)),
            'has_external_drive': self._detect_external_drive(),
            'playback_target_id': self._mass_target_id,
            'playback_backend': 'mass' if self._mass_target_id else 'unavailable',
            'rip': self._rip_payload(),
        }
        await self.broadcast('cd_update', cd_data)
        # Unified PLAYING view metadata via router (only when we have active metadata)
        if cd_data['state'] in ('playing', 'paused'):
            # Upper bound too: metadata from an alternative MusicBrainz
            # release can list fewer tracks than the physical disc plays.
            track = (cd_data['tracks'][cd_data['current_track'] - 1]
                     if cd_data['tracks']
                     and 0 < cd_data['current_track'] <= len(cd_data['tracks'])
                     else None)
            album_text = f"{cd_data['title']} ({cd_data['year']})" if cd_data['year'] else cd_data['title']
            track_title = track.get('title', f"Track {cd_data['current_track']}") if track else cd_data['title']
            await self.post_media_update(
                title=track_title,
                artist=cd_data['artist'],
                album=album_text,
                artwork=cd_data.get('artwork', ''),
                back_artwork=cd_data.get('back_artwork', ''),
                state=cd_data['state'],
                track_number=cd_data['current_track'],
            )
            # Pre-cache TTS for instant announce on button press
            tts_text = f"{track_title}, by {cd_data['artist']}" if cd_data['artist'] else track_title
            self._spawn(tts_precache(tts_text), name="tts_precache")

    async def _announce_track(self, volume=100, text=None):
        """Speak text over the CD audio via TTS overlay.

        Ducks the CD stream to 60% during TTS, then ramps back up.
        If text is None, announces the current track title + artist.
        """
        if self._remote_playback:
            # Local PipeWire TTS cannot be guaranteed to reach the selected
            # MASS room, so never let a CD announcement leak to a stale sink.
            log.info("Announce skipped — CD playback is routed through MASS")
            return
        if not self.metadata or self.cdplayer.state != 'playing':
            log.info("Announce skipped — no metadata or not playing")
            return

        if text is None:
            track_num = self.cdplayer.current_track
            tracks = self.metadata.get('tracks', [])
            artist = self.metadata.get('artist', 'Unknown Artist')

            if tracks and 1 <= track_num <= len(tracks):
                title = tracks[track_num - 1].get('title', f'Track {track_num}')
            else:
                title = f'Track {track_num}'

            text = f"{title}, by {artist}"
        log.info(f"Announcing: {text}")

        # Duck CD volume, play TTS, then restore
        await self.cdplayer.fade_volume(60, duration=0.5)
        await tts_announce(text, volume=volume)
        await self.cdplayer.fade_volume(100, duration=0.8)

    def _detect_external_drive(self):
        """Check if an external USB drive is mounted (for ripping). Cached for 30s."""
        import time as _time
        now = _time.monotonic()
        if now - self._external_drive_cache_time < 30:
            return self._external_drive_cache
        try:
            result = subprocess.run(
                ['lsblk', '-nro', 'MOUNTPOINT,TRAN'],
                capture_output=True, text=True, timeout=3
            )
            for line in result.stdout.strip().split('\n'):
                parts = line.strip().split()
                if len(parts) >= 2 and parts[1] == 'usb' and parts[0].startswith('/'):
                    self._external_drive_cache = parts[0]
                    self._external_drive_cache_time = now
                    return parts[0]
        except Exception:
            pass
        self._external_drive_cache = None
        self._external_drive_cache_time = now
        return None

    # ── CD button action ──

    async def _handle_cd_button_action(self, data=None):
        """Handle CD button press from remote — start playback if disc present."""
        if not self.drive.disc_inserted:
            return {'message': 'no disc'}

        if self.cdplayer.total_tracks == 0:
            return {'message': 'loading'}  # metadata task still fetching TOC

        if self.cdplayer.state == 'playing':
            return {'message': 'already playing'}

        await self._play(data)
        await self.register('playing', navigate=True, auto_power=True)
        await self._broadcast_cd_update()
        return {'command': 'cd', 'playback': self.cdplayer.get_status()}

    async def _use_alternative_release(self, release_id):
        """Switch metadata to an alternative MusicBrainz release."""
        if not release_id or not self.metadata or not HAS_MB:
            return
        disc_id = self.metadata.get('disc_id', '')

        try:
            result = await asyncio.get_running_loop().run_in_executor(
                None, lambda: musicbrainzngs.get_release_by_id(
                    release_id, includes=['artists', 'recordings']
                )
            )
            release = result.get('release', {})
            artist = release.get('artist-credit-phrase', 'Unknown Artist')
            title = release.get('title', 'Unknown Album')
            date = release.get('date', '')[:4]

            tracks = []
            for medium in release.get('medium-list', []):
                for track in medium.get('track-list', []):
                    rec = track.get('recording', {})
                    length_ms = int(rec.get('length', 0) or 0)
                    mins = length_ms // 60000
                    secs = (length_ms % 60000) // 1000
                    tracks.append({
                        'num': int(track.get('position', 0)),
                        'title': rec.get('title', f'Track {track.get("position", "?")}'),
                        'duration': f'{mins}:{secs:02d}'
                    })

            artwork_path = await self.metadata_lookup._fetch_artwork(release_id, disc_id)
            back_artwork_path = await self.metadata_lookup._fetch_artwork(release_id, disc_id, 'back')

            # Rebuild alternatives: move current to alts, remove selected from alts
            old_alts = self.metadata.get('alternatives', [])
            new_alts = [{'release_id': self.metadata.get('release_id', ''),
                         'artist': self.metadata.get('artist', ''),
                         'title': self.metadata.get('title', ''),
                         'year': self.metadata.get('year', '')}]
            new_alts += [a for a in old_alts if a['release_id'] != release_id]

            self.metadata = {
                'disc_id': disc_id,
                'release_id': release_id,
                'title': title,
                'artist': artist,
                'year': date,
                'album': f'{title} ({date})' if date else title,
                'tracks': tracks,
                'track_count': len(tracks),
                'artwork': artwork_path,
                'back_artwork': back_artwork_path,
                'alternatives': new_alts
            }
            physical_tracks = len(self.cdplayer.track_offsets)
            if physical_tracks and len(tracks) != physical_tracks:
                log.warning(f"Release has {len(tracks)} tracks but disc has {physical_tracks} — clamping")
            self.cdplayer.total_tracks = min(len(tracks), physical_tracks) if physical_tracks else len(tracks)
            log.info(f"Switched to: {artist} — {title}")
            await self._broadcast_cd_update()

        except Exception as e:
            log.error(f"Failed to switch release: {e}")

    def _available_rip_destinations(self):
        destinations = []

        def add(path, label, kind):
            path = str(path or '').rstrip('/')
            if path and not any(item['path'] == path for item in destinations):
                destinations.append({'path': path, 'label': label, 'kind': kind})

        add(self._mass_library_path, 'MASS music library', 'mass')
        external = self._detect_external_drive()
        if external:
            add(str(Path(external) / 'Music'), 'External USB drive', 'external')
        current = self.ripper.settings.get('destination')
        add(current, 'Custom location', 'custom')
        return destinations

    def _rip_payload(self):
        payload = self.ripper.snapshot()
        payload['destinations'] = self._available_rip_destinations()
        payload['default_destination'] = self._mass_library_path
        payload['drive_speed_mode'] = self._rip_speed_mode
        payload['drive_speed'] = CD_QUIET_RIP_SPEED if self._rip_speed_limited else 0
        payload['drive_speed_message'] = self._rip_speed_message
        return payload

    @staticmethod
    def _destination_is_allowed(destination):
        path = Path(destination).expanduser()
        if not path.is_absolute():
            return False
        resolved = path.resolve(strict=False)
        allowed_roots = (Path('/media'), Path('/mnt'), Path('/run/media'))
        return any(resolved == root or root in resolved.parents for root in allowed_roots)

    async def _set_rip_settings(self, data):
        automatic = data.get('automatic') if 'automatic' in data else None
        autoeject = data.get('autoeject') if 'autoeject' in data else None
        destination = data.get('destination') if 'destination' in data else None
        if destination is not None:
            destination = str(destination).strip()
            if not self._destination_is_allowed(destination):
                raise ValueError('Rip destination must be an absolute mounted media path')
            try:
                await asyncio.to_thread(Path(destination).mkdir, parents=True, exist_ok=True)
            except OSError as exc:
                raise ValueError(f'Rip destination is not writable: {exc}') from exc
        await self.ripper.set_settings(
            automatic=automatic, autoeject=autoeject, destination=destination)

    async def _on_rip_update(self, _snapshot):
        if not bool(self.ripper.status.get('active')) and self._rip_speed_limited:
            await self._set_drive_speed(0)
        if self.ripper.status.get('status') == 'completed' and not self._refresh_after_rip_started:
            self._refresh_after_rip_started = True
            album_path = str(self.ripper.status.get('album_path') or '').strip()
            self._spawn(
                self._mass_request(
                    '/library/refresh',
                    payload={'path': album_path},
                    timeout=30,
                ),
                name='cd_refresh_mass_library',
            )
        if (
            self.ripper.status.get('status') == 'completed'
            and self.ripper.settings.get('autoeject', True)
            and not self._auto_eject_after_rip_started
        ):
            self._auto_eject_after_rip_started = True
            self._spawn(self._auto_eject_after_rip(), name='cd_auto_eject_after_rip')
        if self.metadata:
            await self._broadcast_cd_update()

    async def _auto_eject_after_rip(self):
        """Eject only after the rip task has released the optical process."""
        for _attempt in range(20):
            if not self.ripper.active:
                break
            await asyncio.sleep(0.1)
        if self.ripper.active or not self.drive.disc_inserted:
            return
        self._auto_ejecting = True
        try:
            await self.drive.eject()
            log.info('CD auto-ejected after successful rip')
        except Exception:
            self._auto_ejecting = False
            log.exception('Unable to auto-eject CD after rip')

    async def _on_rip_track_ready(self, track_number, path):
        should_start = bool(
            self._rip_resume_playback
            and track_number == self._rip_resume_track
            and not self._remote_playback
        )
        target = self._rip_resume_target if should_start else await self._selected_target_id()
        if not target:
            log.warning('Ripped track %s is ready but no MASS playback target is configured', track_number)
            return

        if should_start:
            started = await self._start_mass_playback(
                track_number,
                {'target_player_id': target},
            )
            if started:
                self._rip_resume_playback = False
                await self.register('playing', auto_power=True)
                await self._broadcast_cd_update()
            return

        queued = await self._queue_ripped_paths(
            [str(path)], target, option='add', start_playback=False)
        if not queued:
            log.warning('Unable to queue finalised ripped track %s in MASS', track_number)

    async def _set_drive_speed(self, speed):
        """Request a drive read speed; 0 means the drive's maximum rate."""
        requested = max(0, int(speed or 0))
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ['eject', '-x', str(requested), self.drive.device_path],
                capture_output=True,
                text=True,
                timeout=8,
            )
            if result.returncode != 0:
                detail = ' '.join((result.stderr or result.stdout or '').split())
                raise RuntimeError(detail or f'eject exited {result.returncode}')
            self._rip_speed_limited = requested > 0
            self._rip_speed_mode = 'quiet' if requested else 'maximum'
            self._rip_speed_message = (
                f'Quiet ripping at {requested}x while CD playback continues'
                if requested
                else 'Maximum drive speed while no CD playback is active'
            )
            return True
        except Exception as exc:
            self._rip_speed_limited = False
            self._rip_speed_mode = 'unsupported'
            self._rip_speed_message = f'Drive speed control unavailable: {exc}'
            log.warning(self._rip_speed_message)
            return False

    def _artwork_file(self):
        artwork = str((self.metadata or {}).get('artwork') or '').strip()
        if not artwork:
            return ''
        direct = Path(artwork)
        if direct.is_file():
            return str(direct)
        cached = Path(CD_CACHE_DIR) / Path(urllib.parse.urlparse(artwork).path).name
        return str(cached) if cached.is_file() else ''

    @staticmethod
    def _library_match_is_duplicate(payload):
        if not isinstance(payload, dict):
            return False
        if str(payload.get('match') or '').strip().lower() == 'exact':
            return True
        for match in payload.get('matches') or []:
            if not isinstance(match, dict):
                continue
            try:
                score = float(match.get('score'))
            except (TypeError, ValueError):
                try:
                    score = float(match.get('score_percent')) / 100.0
                except (TypeError, ValueError):
                    continue
            if score > 0.95:
                return True
        return False

    async def _library_match_for_disc(self):
        request = getattr(self, '_mass_request', None)
        if not callable(request):
            return None
        return await request(
            '/library/match',
            payload={
                'artist': self.metadata.get('artist', ''),
                'album': self.metadata.get('title', ''),
                'track_count': self.metadata.get('track_count', 0),
            },
        )

    async def _start_rip(
            self, data=None, manual=False, play_when_ready=False,
            library_checked=False):
        if not self.drive.disc_inserted or not self.metadata:
            raise RuntimeError('No audio CD is ready to rip')
        if self.ripper.active:
            return False
        if not library_checked:
            payload = await self._library_match_for_disc()
            if self._library_match_is_duplicate(payload):
                await self.ripper.mark_skipped(
                    'A greater than 95% album match is already in the MASS library')
                log.info('CD rip suppressed because the MASS library match exceeds 95%%')
                return False

        was_playing = self.cdplayer.state == 'playing'
        should_play = bool(was_playing or play_when_ready)
        target = await self._selected_target_id(data) if should_play else ''
        start_track = (self.cdplayer.current_track or 1) if was_playing else 1
        self._rip_resume_track = int(start_track) if should_play else 0
        self._rip_resume_target = target if should_play else ''
        self._rip_resume_playback = bool(should_play and target)
        self._mass_rip_queue_target = target if should_play else ''
        self._mass_rip_queued_paths = set()
        self._mass_rip_uri_tracks = {}
        self._refresh_after_rip_started = False
        self._auto_eject_after_rip_started = False

        # A single optical reader is more reliable than simultaneous playback
        # and extraction. MASS resumes from each completed FLAC track.
        await self._stop_playback()
        await self._set_drive_speed(CD_QUIET_RIP_SPEED if should_play else 0)

        rip_metadata = dict(self.metadata)
        rip_metadata['artwork_file'] = self._artwork_file()
        started = await self.ripper.start(
            metadata=rip_metadata,
            device_path=self.drive.device_path,
            destination=self.ripper.settings.get('destination'),
            start_track=start_track,
        )
        if started:
            self.cdplayer.current_track = int(start_track) if should_play else 0
            self.cdplayer.state = 'paused' if should_play else 'stopped'
            await self.register('available')
            log.info(
                'Started %s CD rip to %s',
                'manual' if manual else 'automatic',
                self.ripper.settings.get('destination'),
            )
        else:
            self._rip_resume_playback = False
            if self._rip_speed_limited:
                await self._set_drive_speed(0)
        return started

    async def _check_auto_rip(self, *, play_when_ready=False):
        if not self.ripper.settings.get('automatic'):
            return 'disabled'
        if self.ripper.status.get('resumable'):
            return 'started' if await self._start_rip(
                manual=False, play_when_ready=play_when_ready,
                library_checked=True) else 'failed'
        if self.metadata.get('title') in {'', 'Unknown Album'}:
            await self.ripper.mark_skipped(
                'Album metadata is unknown; automatic rip was not started')
            return 'unknown'
        payload = await self._library_match_for_disc()
        if not payload:
            await self.ripper.mark_skipped(
                'Library check unavailable; automatic rip was not started')
            return 'unavailable'
        match_type = payload.get('match')
        if match_type == 'exact':
            await self.ripper.mark_skipped('This CD is already in the MASS library')
            return 'exact'
        if self._library_match_is_duplicate(payload):
            await self.ripper.mark_skipped(
                'A greater than 95% album match is already in the MASS library')
            return 'duplicate'
        if match_type == 'similar':
            await self.ripper.notify_prompt({
                'type': 'similar_album',
                'message': 'Similar album found in library, do you still want to rip this cd?',
                'matches': payload.get('matches') or [],
                'play_when_ready': bool(play_when_ready),
            })
            return 'similar'
        return 'started' if await self._start_rip(
            manual=False, play_when_ready=play_when_ready,
            library_checked=True) else 'failed'

    async def _handle_speakers(self, request):
        return web.json_response(
            await self.audio.get_outputs(),
            headers=self._cors_headers())


if __name__ == '__main__':
    service = CDService()
    asyncio.run(service.run())
