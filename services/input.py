#!/usr/bin/env python3
import asyncio, threading, json, time, sys, re
import hid, websockets
import subprocess
from concurrent.futures import ThreadPoolExecutor
import os
import logging
import aiohttp
from aiohttp import web, ClientSession
from lib.background_tasks import BackgroundTaskSet
from lib.transport import Transport
from lib.config import cfg, reload_config
from lib.correlation import install_logging
from lib.endpoints import (
    ROUTER_BROADCAST,
    ROUTER_MEDIA,
    ROUTER_EVENT,
    ROUTER_OUTPUT_OFF,
    ROUTER_TOUCH,
    ROUTER_RESYNC,
    router_url,
)
from lib.loop_monitor import LoopMonitor
from lib.watchdog import watchdog_loop
from lib.beacon import send_beacon

logger = install_logging('beo-input')

# Module-level background task set — exceptions in fire-and-forget tasks
# go to the journal instead of disappearing.
_background_tasks = BackgroundTaskSet(logger, label="input")

VID, PID = 0x0cd4, 0x1112
BTN_MAP = {0x20:'left', 0x10:'right', 0x40:'go', 0x80:'power'}
clients = set()
dev = None  # HID device handle, set by scan_loop when connected

# Unified transport for HA communication (webhook, MQTT, or both)
transport = Transport()

# Base path for BeoSound 5c installation (from env, or derive from script location)
BS5C_BASE_PATH = os.getenv('BS5C_BASE_PATH', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROUTER_BROADCAST_URL = ROUTER_BROADCAST
ROUTER_MEDIA_URL = ROUTER_MEDIA
ROUTER_STATUS_URL = router_url('/router/status')

SHOWING_RELAY_ID = 'showing'
SHOWING_RELAY_ACTIVE_STATES = {'playing', 'paused', 'buffering'}
SHOWING_RELAY_IDLE_STATES = {'', 'idle', 'unknown', 'off', 'standby', 'unavailable'}
SCREEN_ACTIVE_PLAYBACK_STATES = {'playing', 'buffering', 'transitioning'}
DEFAULT_SCREEN_POLICY_POLL_SECONDS = 1.0
DEFAULT_SCREEN_PRESENCE_OFF_DELAY_SECONDS = 180.0
DEFAULT_SCREEN_IDLE_OFF_DELAY_SECONDS = 180.0
DEFAULT_SCREEN_WAKE_HOLD_SECONDS = 30.0
DEFAULT_SCREEN_MANUAL_OVERRIDE_SECONDS = 300.0
HLK_STATE_URL = 'http://127.0.0.1:8784/state'
HLK_RELOAD_URL = 'http://127.0.0.1:8784/reload'
LOCAL_HLK_WAKE_ENTITY_ID = 'local_hlk.wake'
LOCAL_HLK_PRESENCE_ENTITY_ID = 'local_hlk.presence_stable'

# ——— Update management ———

DEFAULT_UPDATE_REPO = 'mkirsten/beosound5c'
_update_cache: dict = {'data': None, 'fetched_at': 0.0, 'repo': ''}
_UPDATE_CACHE_TTL = 3600  # seconds
_update_in_progress = False
_update_step = 'idle'  # 'downloading' | 'extracting' | 'installing' | 'restarting'
_UPDATE_EXCLUDES = [
    'device_id',
    'web/json/config.json',
    'web/json/spotify_playlists.json',
    'web/json/digit_playlists.json',
    'web/json/apple_music_playlists.json',
    'web/json/apple_music_digit_playlists.json',
    'web/json/tidal_playlists.json',
    'web/json/tidal_digit_playlists.json',
    'web/json/plex_playlists.json',
    'web/json/plex_digit_playlists.json',
    'web/assets/cd-cache',
    'services/sources/spotify/spotify_tokens.json',
    'services/sources/apple_music/apple_music_tokens.json',
    'services/sources/tidal/tidal_tokens.json',
    'services/sources/plex/plex_tokens.json',
    'services/sources/radio/radio_last_station.json',
    'services/sources/radio/radio_favourites.json',
]


def _get_update_repo() -> str:
    configured = str(
        os.getenv('BS5C_UPDATE_REPO')
        or cfg('system', 'update_repo', default='')
        or ''
    ).strip()
    if configured:
        owner, _, repo = configured.partition('/')
        if owner and repo and '/' not in repo:
            return f'{owner}/{repo}'
        logger.warning('Invalid update repo %r; falling back to %s',
                       configured, DEFAULT_UPDATE_REPO)
    return DEFAULT_UPDATE_REPO


def _get_releases_url(repo: str | None = None) -> str:
    return f'https://api.github.com/repos/{repo or _get_update_repo()}/releases/latest'

# Shared HTTP client session (created lazily in async context)
_http_session = None

async def get_http_session():
    global _http_session
    if _http_session is None or _http_session.closed:
        _http_session = ClientSession()
    return _http_session


def _new_screen_policy_state() -> dict:
    return {
        'applied_target': None,
        'all_off_since': None,
        'last_target': None,
        'last_reason': 'startup',
        'last_local_input_at': 0.0,
        'last_playing': False,
        'last_presence': 'unknown',
        'last_presence_states': {},
        'last_wake': 'unknown',
        'last_wake_states': {},
        'last_wake_sensor_state': 'unknown',
        'manual_off_latched': False,
        'manual_off_until': 0.0,
        'wake_hold_until': 0.0,
        'last_tick_monotonic': 0.0,
    }


_screen_policy_state = _new_screen_policy_state()

# ——— track current "byte1" state (LED/backlight bits) ———
state_byte1 = 0x00
last_power_press_time = 0  # For debouncing power button
POWER_DEBOUNCE_TIME = 0.5  # Seconds to ignore repeated power button presses
power_button_state = 0  # 0 = released, 1 = pressed
GO_LONG_PRESS_TIME = float(os.getenv('BS5C_GO_LONG_PRESS_TIME', '0.55'))
go_button_state = 0  # 0 = released, 1 = pressed
go_press_started_at = 0.0
go_long_sent = False
go_long_timer_handle = None
power_button_pressed_at = 0.0  # wall time of the current press (long-press detection)
# Hold the power button this long to send ALL-STANDBY (local standby +
# ML broadcast so link speakers in other rooms power down too).
POWER_LONGPRESS_ALL_STANDBY = 5.0

def is_backlight_on():
    """Check backlight state from the hardware state byte."""
    return (state_byte1 & 0x40) != 0

def bs5_send(data: bytes):
    """Low-level HID write."""
    if dev is None:
        return
    try:
        dev.write(data)
    except Exception as e:
        logger.error("HID write failed: %s", e)

def bs5_send_cmd(byte1, byte2=0x00):
    """Build & send HID report."""
    bs5_send(bytes([byte1, byte2]))

def do_click():
    """Send click bit on top of current state."""
    global state_byte1
    bs5_send_cmd(state_byte1 | 0x01)

def set_led(mode: str):
    """mode in {'on','off','blink'}"""
    global state_byte1
    state_byte1 &= ~(0x80 | 0x10)       # clear LED bits
    if mode == 'on':
        state_byte1 |= 0x80
    elif mode == 'blink':
        state_byte1 |= 0x10
    bs5_send_cmd(state_byte1)

# Single worker so on/off xrandr calls can't reorder; running them off
# the caller's thread keeps a slow HDMI mode-set (up to the 2s timeout)
# from freezing the event loop that serves the hardware-event WebSocket.
_xrandr_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="xrandr")

def _run_xrandr(on: bool):
    """Control screen using xrandr (Linux only, skip on Mac)."""
    try:
        env = os.environ.copy()
        env["DISPLAY"] = ":0"
        subprocess.run(
            ["xrandr", "--output", "HDMI-1"] +
            (["--mode", "1024x768", "--rate", "60"] if on else ["--off"]),
            env=env,
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            check=False,
            timeout=2
        )
    except FileNotFoundError:
        # xrandr not available (e.g., on macOS) - skip screen control
        pass
    except Exception as e:
        logger.warning("xrandr failed: %s", e)

def set_backlight(on: bool):
    """Turn backlight bit on/off."""
    global state_byte1

    if on:
        state_byte1 |= 0x40
    else:
        state_byte1 &= ~0x40
    bs5_send_cmd(state_byte1)

    _xrandr_pool.submit(_run_xrandr, on)

def toggle_backlight():
    """Toggle backlight state."""
    current = is_backlight_on()
    new_state = not current
    logger.info("Toggling backlight from %s to %s", current, new_state)
    set_backlight(new_state)


async def _set_display_awake(on: bool, *, power_audio: bool = False):
    """Control the panel backlight, optionally powering audio output down."""
    set_backlight(bool(on))
    if power_audio and not on:
        try:
            s = await get_http_session()
            await s.post(ROUTER_OUTPUT_OFF, timeout=aiohttp.ClientTimeout(total=2))
        except Exception:
            pass

def _emit_button_event(loop, button: str):
    """Broadcast a synthetic button event from the HID state machine."""
    if loop is None:
        logger.debug("No event loop available for synthetic button: %s", button)
        return
    try:
        asyncio.run_coroutine_threadsafe(
            broadcast(json.dumps({'type': 'button', 'data': {'button': button}})),
            loop
        )
    except Exception as e:
        logger.debug("Failed to emit synthetic button %s: %s", button, e)

def _cancel_go_long_timer():
    global go_long_timer_handle
    if go_long_timer_handle is not None:
        go_long_timer_handle.cancel()
        go_long_timer_handle = None

def _fire_go_long(loop):
    global go_long_sent, go_long_timer_handle
    go_long_timer_handle = None
    if go_button_state == 1 and not go_long_sent:
        go_long_sent = True
        logger.info("GO long press detected")
        _emit_button_event(loop, 'go_long')

def _reset_go_button_tracking():
    global go_button_state, go_press_started_at, go_long_sent
    _cancel_go_long_timer()
    go_button_state = 0
    go_press_started_at = 0.0
    go_long_sent = False

def get_service_logs(service: str, lines: int = 100) -> list:
    """Fetch logs for a systemd service using journalctl."""
    try:
        result = subprocess.run(
            ['journalctl', '-u', service, '-n', str(lines), '--no-pager', '-o', 'short'],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip().split('\n') if result.stdout else []
    except Exception as e:
        return [f'Error fetching logs: {e}']

def get_system_info() -> dict:
    """Get system information including uptime, temp, memory, and service status."""
    info = {}
    try:
        # Uptime
        result = subprocess.run(['uptime', '-p'], capture_output=True, text=True, timeout=2)
        info['uptime'] = result.stdout.strip().replace('up ', '') if result.stdout else '--'

        # CPU Temperature
        try:
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                temp = int(f.read().strip()) / 1000
                info['cpu_temp'] = f'{temp:.1f}C'
        except Exception:
            info['cpu_temp'] = '--'

        # Memory usage
        result = subprocess.run(['free', '-h'], capture_output=True, text=True, timeout=2)
        if result.stdout:
            lines = result.stdout.strip().split('\n')
            if len(lines) >= 2:
                parts = lines[1].split()
                if len(parts) >= 3:
                    info['memory'] = f'{parts[2]} / {parts[1]}'

        # IP Address
        try:
            result = subprocess.run(['hostname', '-I'], capture_output=True, text=True, timeout=2)
            if result.stdout:
                info['ip_address'] = result.stdout.strip().split()[0]
        except Exception:
            info['ip_address'] = '--'

        # Hostname
        try:
            result = subprocess.run(['hostname'], capture_output=True, text=True, timeout=2)
            info['hostname'] = result.stdout.strip() if result.stdout else '--'
        except Exception:
            info['hostname'] = '--'

        # Backlight status
        info['backlight'] = 'On' if is_backlight_on() else 'Off'

        # Version: prefer VERSION file (written by OTA + deploy.sh) over
        # `git describe`. The .git dir, if present from a clone install,
        # is not touched by OTA, so git describe goes stale after update.
        info['git_tag'] = '--'
        try:
            with open(os.path.join(BS5C_BASE_PATH, 'VERSION')) as f:
                v = f.read().strip()
                if v:
                    info['git_tag'] = v
        except Exception:
            pass
        if info['git_tag'] == '--':
            try:
                result = subprocess.run(
                    ['git', 'describe', '--tags', '--always'],
                    capture_output=True, text=True, timeout=2,
                    cwd=BS5C_BASE_PATH
                )
                if result.stdout and result.stdout.strip():
                    info['git_tag'] = result.stdout.strip()
            except Exception:
                pass
        info['update_repo'] = _get_update_repo()

        # Device UUID (stable, generated by lib/beacon.py on first run)
        info['device_id'] = '--'
        try:
            with open(os.path.join(BS5C_BASE_PATH, 'device_id')) as f:
                v = f.read().strip()
                if v:
                    info['device_id'] = v
        except Exception:
            pass

        # Audio HAT info (from install-time detection)
        info['audio_hat'] = None
        try:
            with open('/etc/beosound5c/audio-hat') as f:
                hat = {}
                for line in f:
                    if '=' in line:
                        k, v = line.strip().split('=', 1)
                        hat[k.lower()] = v
                if hat.get('hat_name'):
                    info['audio_hat'] = hat.get('hat_name')
        except Exception:
            pass

        # Service status — discover all beo-* units dynamically
        info['services'] = {}
        try:
            result = subprocess.run(
                ['systemctl', 'list-units', 'beo-*', '--no-legend', '--no-pager', '--plain'],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.strip().splitlines():
                parts = line.split()
                if not parts:
                    continue
                unit = parts[0]  # e.g. "beo-input.service" or "beo-health.timer"
                # Skip timers — they're background infra, not user-facing
                if unit.endswith('.timer'):
                    continue
                svc = unit.removesuffix('.service')
                active = parts[2] if len(parts) > 2 else 'unknown'  # "active" or "failed" etc.
                info['services'][svc] = 'Running' if active == 'active' else active.capitalize()
        except Exception:
            pass

        # Config from JSON file
        info['config'] = {}
        try:
            import json as _json
            for p in ['/etc/beosound5c/config.json', 'config.json']:
                if os.path.exists(p):
                    with open(p) as f:
                        info['config'] = _json.load(f)
                    break
        except Exception as e:
            logger.error('Config read error: %s', e)

    except Exception as e:
        logger.error('System info error: %s', e)
    return info

def get_network_status() -> dict:
    """Ping default gateway and internet (8.8.8.8) to check connectivity."""
    net = {}
    try:
        result = subprocess.run(
            ['ip', 'route', 'show', 'default'],
            capture_output=True, text=True, timeout=2
        )
        if result.stdout:
            parts = result.stdout.strip().split()
            if 'via' in parts:
                gw = parts[parts.index('via') + 1]
                net['gateway'] = gw
                result = subprocess.run(
                    ['ping', '-c', '1', '-W', '1', gw],
                    capture_output=True, text=True, timeout=3
                )
                if result.returncode == 0 and 'time=' in result.stdout:
                    net['gateway_ping'] = result.stdout.split('time=')[1].split()[0]
                else:
                    net['gateway_ping'] = 'timeout'
        # Ping internet
        result = subprocess.run(
            ['ping', '-c', '1', '-W', '1', '8.8.8.8'],
            capture_output=True, text=True, timeout=3
        )
        if result.returncode == 0 and 'time=' in result.stdout:
            net['internet_ping'] = result.stdout.split('time=')[1].split()[0]
        else:
            net['internet_ping'] = 'timeout'
    except Exception as e:
        logger.error('Network check error: %s', e)
    return net

def _parse_semver(tag: str) -> tuple:
    """Parse 'v0.7.0', 'v0.7.0-dev.21', 'v0.7.0-21-gabc' into a comparable int tuple."""
    import re
    t = re.sub(r'[-+].*$', '', tag.lstrip('v'))
    try:
        return tuple(int(x) for x in t.split('.'))
    except ValueError:
        return (0, 0, 0)


def _is_newer(latest: str, current: str) -> bool:
    return _parse_semver(latest) > _parse_semver(current)


def _get_current_version() -> str:
    """Read installed version from VERSION file, fallback to git describe."""
    try:
        with open(os.path.join(BS5C_BASE_PATH, 'VERSION')) as f:
            v = f.read().strip()
            if v:
                return v
    except Exception:
        pass
    try:
        result = subprocess.run(
            ['git', 'describe', '--tags', '--always'],
            capture_output=True, text=True, timeout=2, cwd=BS5C_BASE_PATH
        )
        if result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return '--'


async def _fetch_latest_release():
    """Fetch latest GitHub release info. Cached for 1 hour."""
    now = time.time()
    repo = _get_update_repo()
    releases_url = _get_releases_url(repo)
    if (_update_cache['data']
            and _update_cache.get('repo') == repo
            and now - _update_cache['fetched_at'] < _UPDATE_CACHE_TTL):
        return _update_cache['data']
    try:
        session = await get_http_session()
        async with session.get(
            releases_url,
            headers={'Accept': 'application/vnd.github.v3+json', 'User-Agent': 'beosound5c'},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            result = {
                'repo': repo,
                'api_url': releases_url,
                'latest': data.get('tag_name', ''),
                'release_url': data.get('html_url', ''),
                'tarball_url': data.get('tarball_url', ''),
                'release_notes': (data.get('body') or '').strip(),
                'published_at': data.get('published_at', ''),
            }
            _update_cache['data'] = result
            _update_cache['fetched_at'] = now
            _update_cache['repo'] = repo
            return result
    except Exception as e:
        logger.warning('GitHub release check failed: %s', e)
        return None


async def _run_update():
    """Download and install the latest release, then restart all beo-* services."""
    global _update_in_progress, _update_step
    import tempfile, shutil

    tmp_dir = tempfile.mkdtemp(prefix='beo5c-update-')
    try:
        release = await _fetch_latest_release()
        if not release or not release.get('tarball_url'):
            raise RuntimeError('No release info available')

        latest_tag = release['latest']
        tarball_path = os.path.join(tmp_dir, 'release.tar.gz')
        extract_dir = os.path.join(tmp_dir, 'src')
        os.makedirs(extract_dir)

        logger.info('[update] Downloading %s', latest_tag)
        _update_step = 'downloading'
        session = await get_http_session()
        async with session.get(
            release['tarball_url'],
            headers={'User-Agent': 'beosound5c'},
            timeout=aiohttp.ClientTimeout(total=120),
            allow_redirects=True,
        ) as resp:
            if resp.status not in (200, 302):
                raise RuntimeError(f'Download failed: HTTP {resp.status}')
            with open(tarball_path, 'wb') as f:
                async for chunk in resp.content.iter_chunked(65536):
                    f.write(chunk)

        logger.info('[update] Extracting')
        _update_step = 'extracting'
        await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                ['tar', 'xzf', tarball_path, '-C', extract_dir, '--strip-components=1'],
                capture_output=True, timeout=60, check=True,
            ),
        )

        logger.info('[update] Installing files to %s', BS5C_BASE_PATH)
        _update_step = 'installing'
        exclude_args = []
        for exc in _UPDATE_EXCLUDES:
            exclude_args += ['--exclude', exc]
        await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                ['rsync', '-a'] + exclude_args + [extract_dir + '/', BS5C_BASE_PATH + '/'],
                capture_output=True, timeout=60, check=True,
            ),
        )

        # Write new VERSION file and invalidate cache
        with open(os.path.join(BS5C_BASE_PATH, 'VERSION'), 'w') as f:
            f.write(latest_tag + '\n')
        _update_cache['data'] = None
        _update_cache['fetched_at'] = 0.0

        # Run post-update script as root (sudoers, daemon-reload, pip packages).
        # Non-fatal — log and continue if it fails (e.g. missing sudoers entry
        # on a device that hasn't run install.sh since v0.8).
        _update_step = 'post-update'
        post_update = os.path.join(BS5C_BASE_PATH, 'install', 'post-update.sh')
        if os.path.isfile(post_update):
            logger.info('[update] Running post-update script')
            try:
                result = await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: subprocess.run(
                        ['sudo', post_update],
                        capture_output=True, text=True, timeout=120,
                    ),
                )
                if result.returncode == 0:
                    logger.info('[update] Post-update done')
                else:
                    logger.warning('[update] Post-update failed (non-fatal): %s', result.stderr.strip())
            except Exception as e:
                logger.warning('[update] Post-update error (non-fatal): %s', e)

        logger.info('[update] Scheduling service restart')
        _update_step = 'restarting'

        # Discover active beo-* services
        res = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                ['systemctl', 'list-units', 'beo-*.service', '--state=active',
                 '--no-legend', '--no-pager', '--plain'],
                capture_output=True, text=True, timeout=5,
            ),
        )
        services = [
            line.split()[0].removesuffix('.service')
            for line in res.stdout.splitlines() if line.split()
        ]
        backend = [s for s in services if s != 'beo-ui']
        has_ui = 'beo-ui' in services

        parts = ['sleep 2']
        if backend:
            parts.append(f"sudo systemctl restart {' '.join(backend)}")
        if has_ui:
            parts.append('sleep 3 && sudo systemctl restart beo-ui')

        subprocess.Popen(
            ['bash', '-c', ' && '.join(parts)],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # _update_in_progress deliberately stays True here: the restart is
        # imminent and this process is about to be killed. But if the
        # detached restart never happens (e.g. missing sudoers entry, so
        # the `sudo systemctl restart` inside the bash -c fails), the flag
        # would otherwise stay True forever and /update/run would 409 on
        # every retry. Clear it after a generous deadline — if we're still
        # alive by then, the restart didn't happen.
        _background_tasks.spawn(_clear_update_flag_after_deadline(),
                                name='update_restart_deadline')

    except Exception as e:
        logger.error('[update] Failed: %s', e)
        _update_in_progress = False
        _update_step = 'idle'
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def _clear_update_flag_after_deadline(deadline: float = 120.0):
    """Reset the update flag if the scheduled restart never killed us."""
    global _update_in_progress, _update_step
    await asyncio.sleep(deadline)
    if _update_in_progress:
        logger.warning('[update] Service restart did not happen within %.0fs '
                       '— clearing update-in-progress flag', deadline)
        _update_in_progress = False
        _update_step = 'idle'


async def handle_update_check(request):
    """GET /update/check — current version vs latest GitHub release."""
    current = _get_current_version()
    update_repo = _get_update_repo()

    release = await _fetch_latest_release()

    result = {
        'current': current,
        'update_in_progress': _update_in_progress,
        'update_step': _update_step,
        'update_repo': update_repo,
    }
    if release:
        latest = release['latest']
        result['latest'] = latest
        result['update_available'] = _is_newer(latest, current) and not _update_in_progress
        result['release_url'] = release['release_url']
        result['release_notes'] = release['release_notes']
        result['published_at'] = release.get('published_at', '')
        result['update_repo'] = release.get('repo', update_repo)
    else:
        result['error'] = 'Could not reach GitHub'

    return web.json_response(result, headers={'Access-Control-Allow-Origin': '*'})


async def handle_update_run(request):
    """POST /update/run — start background update, return 202 immediately."""
    global _update_in_progress
    if request.method == 'OPTIONS':
        return web.Response(headers={
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type',
        })

    if _update_in_progress:
        return web.json_response(
            {'status': 'already_in_progress'},
            status=409,
            headers={'Access-Control-Allow-Origin': '*'},
        )

    release = await _fetch_latest_release()
    if not release:
        return web.json_response(
            {'status': 'error', 'message': 'Cannot reach GitHub'},
            status=503,
            headers={'Access-Control-Allow-Origin': '*'},
        )

    current = _get_current_version()
    if not _is_newer(release['latest'], current):
        return web.json_response(
            {'status': 'up_to_date'},
            headers={'Access-Control-Allow-Origin': '*'},
        )

    _update_in_progress = True
    _background_tasks.spawn(_run_update(), name='system_update')

    return web.json_response(
        {'status': 'started', 'latest': release['latest']},
        status=202,
        headers={'Access-Control-Allow-Origin': '*'},
    )


def _get_device_ip() -> str:
    """Return the primary LAN IP address of this device."""
    try:
        result = subprocess.run(['hostname', '-I'], capture_output=True, text=True, timeout=2)
        if result.stdout:
            return result.stdout.strip().split()[0]
    except Exception:
        pass
    return '127.0.0.1'


async def handle_qrcode(request):
    """GET /qrcode — QR code PNG pointing to this device's config page."""
    try:
        import qrcode as _qrcode
        import io
        ip = _get_device_ip()
        url = f'http://{ip}/config'
        qr = _qrcode.QRCode(
            error_correction=_qrcode.constants.ERROR_CORRECT_M,
            box_size=8,
            border=2,
        )
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color='white', back_color='black')
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        return web.Response(
            body=buf.getvalue(),
            content_type='image/png',
            headers={'Access-Control-Allow-Origin': '*', 'Cache-Control': 'no-cache'},
        )
    except ImportError:
        return web.json_response(
            {'error': 'qrcode package not installed'},
            status=503,
            headers={'Access-Control-Allow-Origin': '*'},
        )
    except Exception as e:
        logger.warning('QR code generation failed: %s', e)
        return web.Response(status=500, headers={'Access-Control-Allow-Origin': '*'})


async def handle_discover_sonos(request):
    """GET /discover/sonos — find Sonos speakers on the local network."""
    try:
        import soco
        loop = asyncio.get_event_loop()

        def _find():
            # SSDP multicast — fast when it works.
            devices = soco.discover(timeout=5) or set()
            if not devices:
                # Multicast is routinely blocked on WiFi (client isolation)
                # and across VLANs/subnets, so discover() comes back empty on
                # plenty of real networks. Fall back to a direct IP scan of the
                # local subnet, which only needs unicast to reach the speakers.
                try:
                    from soco import discovery as _disc
                    devices = _disc.scan_network(
                        multi_household=False,
                        scan_timeout=0.5,
                        max_threads=256,
                    ) or set()
                except Exception as e:
                    logger.debug('Sonos scan_network fallback failed: %s', e)
            return devices

        devices = await loop.run_in_executor(None, _find)
        result = sorted(
            [{'ip': d.ip_address, 'name': d.player_name} for d in devices],
            key=lambda x: x['name'],
        )
        return web.json_response(result, headers={'Access-Control-Allow-Origin': '*'})
    except ImportError:
        return web.json_response(
            {'error': 'soco not installed'},
            status=503,
            headers={'Access-Control-Allow-Origin': '*'},
        )
    except Exception as e:
        logger.warning('Sonos discovery failed: %s', e)
        return web.json_response([], headers={'Access-Control-Allow-Origin': '*'})


async def handle_discover_bluesound(request):
    """GET /discover/bluesound — find BluOS/Bluesound players via mDNS (_musc._tcp)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            'avahi-browse', '-r', '-t', '-p', '_musc._tcp',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        devices = []
        seen: set = set()
        for line in stdout.decode(errors='replace').splitlines():
            parts = line.split(';')
            # avahi-browse -p resolved record: =;<iface>;<proto>;<name>;<type>;<domain>;<host>;<addr>;<port>;<txt>
            if len(parts) < 9 or parts[0] != '=' or parts[2] != 'IPv4':
                continue
            name, addr = parts[3], parts[7]
            if addr and addr not in seen:
                seen.add(addr)
                devices.append({'name': name, 'ip': addr})
        devices.sort(key=lambda x: x['name'])
        return web.json_response(devices, headers={'Access-Control-Allow-Origin': '*'})
    except asyncio.TimeoutError:
        return web.json_response([], headers={'Access-Control-Allow-Origin': '*'})
    except FileNotFoundError:
        logger.debug('avahi-browse not found — Bluesound discovery unavailable')
        return web.json_response([], headers={'Access-Control-Allow-Origin': '*'})
    except Exception as e:
        logger.warning('Bluesound discovery failed: %s', e)
        return web.json_response([], headers={'Access-Control-Allow-Origin': '*'})


async def _write_secrets(updates: dict) -> None:
    """Update specific env vars in /etc/beosound5c/secrets.env, preserving others."""
    secrets_path = '/etc/beosound5c/secrets.env'
    # Read current content via sudo
    try:
        read_proc = await asyncio.create_subprocess_exec(
            'sudo', 'cat', secrets_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(read_proc.communicate(), timeout=5)
        lines = stdout.decode(errors='replace').splitlines(keepends=True)
    except Exception:
        lines = []

    # Replace matching lines, append any new ones
    new_lines = []
    seen: set = set()
    for line in lines:
        key = line.split('=', 1)[0].strip() if '=' in line else None
        if key and key in updates:
            val = updates[key].replace('\\', '\\\\').replace('"', '\\"')
            new_lines.append(f'{key}="{val}"\n')
            seen.add(key)
        else:
            new_lines.append(line if line.endswith('\n') else line + '\n')
    for key, val in updates.items():
        if key not in seen:
            val_esc = val.replace('\\', '\\\\').replace('"', '\\"')
            new_lines.append(f'{key}="{val_esc}"\n')

    content = ''.join(new_lines)
    try:
        write_proc = await asyncio.create_subprocess_exec(
            'sudo', 'tee', secrets_path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(write_proc.communicate(content.encode()), timeout=10)
        if write_proc.returncode != 0:
            msg = stderr.decode().strip() if stderr else 'tee failed'
            logger.error('Secrets write failed: %s', msg)
    except asyncio.TimeoutError:
        logger.error('Timeout writing secrets.env')
    except Exception as e:
        logger.error('Secrets write error: %s', e)


def _load_current_config_json() -> dict:
    for path in ['/etc/beosound5c/config.json', 'config.json']:
        try:
            if os.path.exists(path):
                with open(path) as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception as e:
            logger.warning('Config read failed from %s: %s', path, e)
    return {}


async def _write_config_json(body: dict) -> tuple[bool, str | None]:
    config_path = '/etc/beosound5c/config.json'
    config_json = json.dumps(body, indent=2, ensure_ascii=False)
    try:
        proc = await asyncio.create_subprocess_exec(
            'sudo', 'tee', config_path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(config_json.encode()), timeout=10)
        if proc.returncode != 0:
            msg = stderr.decode().strip() if stderr else 'sudo tee failed'
            logger.error('Config write failed: %s', msg)
            return False, msg
        return True, None
    except asyncio.TimeoutError:
        return False, 'Timeout writing config'
    except Exception as e:
        logger.error('Config write error: %s', e)
        return False, str(e)


async def _reload_local_hlk_service(*, reconnect: bool = False):
    if not _hlk_enabled():
        return
    session = await get_http_session()
    try:
        await session.post(
            HLK_RELOAD_URL,
            json={'reconnect': reconnect},
            timeout=aiohttp.ClientTimeout(total=2.0),
        )
    except Exception as e:
        logger.debug('Local HLK reload failed: %s', e)


def _reload_runtime_config_safe():
    try:
        reload_config()
    except Exception as e:
        logger.warning('Runtime config reload failed: %s', e)


def _hlk_state_response_payload(hlk_state: dict | None) -> dict:
    hlk_cfg = dict(_hlk_config())
    screen_cfg = dict(_screen_config())
    runtime = (hlk_state or {}).get('runtime') if isinstance(hlk_state, dict) else None
    return {
        'status': 'ok',
        'config': {
            **hlk_cfg,
            'wake_hold_s': screen_cfg.get('wake_hold_s', screen_cfg.get('local_input_wake_s', DEFAULT_SCREEN_WAKE_HOLD_SECONDS)),
        },
        'runtime': runtime if isinstance(runtime, dict) else {},
    }


async def handle_hlk_state(request):
    if request.method == 'OPTIONS':
        return web.Response(headers={
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type',
        })
    payload = _hlk_state_response_payload(await _fetch_local_hlk_state())
    return web.json_response(payload, headers={'Access-Control-Allow-Origin': '*'})


async def handle_hlk_tune(request):
    if request.method == 'OPTIONS':
        return web.Response(headers={
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type',
        })

    try:
        body = await request.json()
    except Exception:
        return web.json_response(
            {'status': 'error', 'message': 'Invalid JSON'},
            status=400,
            headers={'Access-Control-Allow-Origin': '*'},
        )

    if not isinstance(body, dict):
        return web.json_response(
            {'status': 'error', 'message': 'Expected JSON object'},
            status=400,
            headers={'Access-Control-Allow-Origin': '*'},
        )

    current = _load_current_config_json()
    if not current.get('device'):
        current['device'] = cfg('device', default='BeoSound5c')
    hlk_cfg = current.setdefault('hlk', {})
    screen_cfg = current.setdefault('screen', {})

    changed = False
    if 'wake_distance_max_cm' in body:
        try:
            hlk_cfg['wake_distance_max_cm'] = max(0, int(body.get('wake_distance_max_cm')))
            changed = True
        except (TypeError, ValueError):
            return web.json_response(
                {'status': 'error', 'message': 'wake_distance_max_cm must be a number'},
                status=400,
                headers={'Access-Control-Allow-Origin': '*'},
            )
    if 'wake_hold_s' in body:
        try:
            screen_cfg['wake_hold_s'] = max(0.0, float(body.get('wake_hold_s')))
            changed = True
        except (TypeError, ValueError):
            return web.json_response(
                {'status': 'error', 'message': 'wake_hold_s must be a number'},
                status=400,
                headers={'Access-Control-Allow-Origin': '*'},
            )

    if not changed:
        return web.json_response(
            _hlk_state_response_payload(await _fetch_local_hlk_state()),
            headers={'Access-Control-Allow-Origin': '*'},
        )

    ok, error = await _write_config_json(current)
    if not ok:
        return web.json_response(
            {'status': 'error', 'message': error or 'Config write failed'},
            status=500,
            headers={'Access-Control-Allow-Origin': '*'},
        )

    _reload_runtime_config_safe()
    await _reload_local_hlk_service(reconnect=False)
    return web.json_response(
        _hlk_state_response_payload(await _fetch_local_hlk_state()),
        headers={'Access-Control-Allow-Origin': '*'},
    )


async def handle_config_save(request):
    """POST /config — write a new config.json and restart all beo-* services."""
    if request.method == 'OPTIONS':
        return web.Response(headers={
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type',
        })

    try:
        body = await request.json()
    except Exception:
        return web.json_response(
            {'status': 'error', 'message': 'Invalid JSON'},
            status=400,
            headers={'Access-Control-Allow-Origin': '*'},
        )

    if not isinstance(body, dict) or not body.get('device'):
        return web.json_response(
            {'status': 'error', 'message': 'Config must be a JSON object with a "device" field'},
            status=400,
            headers={'Access-Control-Allow-Origin': '*'},
        )

    # Saving via the web UI implies setup is done — flip the first-boot flag so
    # the BS5 stops auto-opening System/Config on the next reload.
    body['setup_complete'] = True

    # Extract secrets — they go to secrets.env, not config.json
    raw_secrets = body.pop('_secrets', None) or {}
    _SECRET_KEY_MAP = {
        'ha_token': 'HA_TOKEN',
        'mqtt_user': 'MQTT_USER',
        'mqtt_password': 'MQTT_PASSWORD',
        'hass_fallback_entity': 'HASS_FALLBACK_ENTITY',
        'hass_volume_priority': 'HASS_VOLUME_PRIORITY',
        'mlgw_host': 'MLGW_HOST',
        'mlgw_port': 'MLGW_PORT',
        'mlgw_user': 'MLGW_USER',
        'mlgw_password': 'MLGW_PASSWORD',
        'mlgw_entity_to_mln': 'MLGW_ENTITY_TO_MLN',
        'mass_ws_url': 'MASS_WS_URL',
        'mass_token': 'MASS_TOKEN',
        'mass_queue_id': 'MASS_QUEUE_ID',
        'mass_player_id': 'MASS_PLAYER_ID',
        'kodi_host': 'KODI_HOST',
        'kodi_port': 'KODI_PORT',
        'kodi_user': 'KODI_USER',
        'kodi_password': 'KODI_PASSWORD',
    }
    secrets_to_write = {
        _SECRET_KEY_MAP[k]: v
        for k, v in raw_secrets.items()
        if k in _SECRET_KEY_MAP and v
    }

    # Fill in the real Sonos zone name when it's missing or the generic
    # default. Picking a speaker from discovery auto-fills output_name, but
    # typing the IP manually leaves it as "Sonos" — query the speaker itself
    # so the UI shows e.g. "Sheep Lounge" instead. Never overrides a name the
    # user chose; never blocks the save (5s cap, best-effort).
    volume_cfg = body.get('volume')
    player_cfg = body.get('player') or {}
    if (isinstance(volume_cfg, dict)
            and player_cfg.get('type') == 'sonos' and player_cfg.get('ip')
            and (volume_cfg.get('output_name') or '').strip().lower() in ('', 'sonos')):
        try:
            import soco
            zone_name = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None, lambda: soco.SoCo(player_cfg['ip']).player_name),
                timeout=5,
            )
            if zone_name:
                volume_cfg['output_name'] = zone_name
                logger.info('Sonos output_name auto-derived: %s', zone_name)
        except Exception as e:
            logger.info('Could not derive Sonos zone name: %r', e)

    ok, error = await _write_config_json(body)
    if not ok:
        return web.json_response(
            {'status': 'error', 'message': error or 'Config write failed'},
            status=500,
            headers={'Access-Control-Allow-Origin': '*'},
        )

    if secrets_to_write:
        await _write_secrets(secrets_to_write)
        logger.info('Secrets updated: %s', ', '.join(secrets_to_write.keys()))

    _reload_runtime_config_safe()
    await _reload_local_hlk_service(reconnect=True)

    logger.info('Config saved to /etc/beosound5c/config.json — scheduling reconcile')

    # Reconcile services after a short delay so the HTTP response can be sent
    # before this process is itself restarted. reconcile-services.sh enables/
    # starts the right player + sources for the new config, disables/stops the
    # old ones, and try-restarts running beo-* services so they pick up changes.
    reconcile_script = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        'system', 'reconcile-services.sh',
    )

    async def _reconcile():
        await asyncio.sleep(1.5)

        # Reload the kiosk FIRST so it re-reads the new config. reconcile-
        # services.sh deliberately skips beo-ui (the arc menu re-fetches on
        # media-WS reconnect), but a config *save* also changes menu visibility
        # and the setup_complete flag — both of which the kiosk only evaluates
        # at page load. This must run *before* the reconcile below, because
        # reconcile-services.sh restarts beo-input last (this very process), and
        # anything after that call would be killed with our own cgroup. The
        # kiosk's menu-manager retries /router/menu with backoff, so reloading
        # ahead of the backend reconcile is safe.
        try:
            await asyncio.create_subprocess_exec(
                'sudo', 'systemctl', 'restart', 'beo-ui',
            )
        except Exception as e:
            logger.error('beo-ui restart after config save failed: %s', e)

        try:
            await asyncio.create_subprocess_exec(
                'sudo', 'bash', reconcile_script,
            )
        except Exception as e:
            logger.error('Service reconcile failed: %s', e)

    _background_tasks.spawn(_reconcile(), name='config_reconcile')

    return web.json_response(
        {'status': 'ok', 'message': 'Config saved, services restarting'},
        headers={'Access-Control-Allow-Origin': '*'},
    )


def get_bt_remotes() -> list:
    """Get paired Bluetooth devices with connection info."""
    remotes = []
    try:
        result = subprocess.run(
            ['bluetoothctl', 'paired-devices'],
            capture_output=True, text=True, timeout=5
        )
        if not result.stdout:
            return remotes

        for line in result.stdout.strip().split('\n'):
            # Format: "Device XX:XX:XX:XX:XX:XX Name"
            parts = line.strip().split(' ', 2)
            if len(parts) < 3 or parts[0] != 'Device':
                continue
            mac = parts[1]
            name = parts[2]

            remote = {'mac': mac, 'name': name, 'connected': False, 'rssi': None, 'battery': None, 'icon': 'input-gaming'}

            # Get detailed info for each device
            try:
                info_result = subprocess.run(
                    ['bluetoothctl', 'info', mac],
                    capture_output=True, text=True, timeout=3
                )
                if info_result.stdout:
                    for info_line in info_result.stdout.split('\n'):
                        info_line = info_line.strip()
                        if info_line.startswith('Connected:'):
                            remote['connected'] = 'yes' in info_line.lower()
                        elif info_line.startswith('RSSI:'):
                            try:
                                # Format: "RSSI: 0xffffffcc" or "RSSI: -52"
                                val = info_line.split(':', 1)[1].strip()
                                if val.startswith('0x'):
                                    rssi = int(val, 16)
                                    if rssi > 0x7FFFFFFF:
                                        rssi -= 0x100000000
                                    remote['rssi'] = rssi
                                else:
                                    remote['rssi'] = int(val)
                            except (ValueError, IndexError):
                                pass
                        elif info_line.startswith('Battery Percentage:'):
                            try:
                                # Format: "Battery Percentage: 0x55 (85)"
                                val = info_line.split('(')[1].rstrip(')')
                                remote['battery'] = int(val)
                            except (ValueError, IndexError):
                                pass
                        elif info_line.startswith('Icon:'):
                            remote['icon'] = info_line.split(':', 1)[1].strip()
            except Exception as e:
                logger.error('BT info error for %s: %s', mac, e)

            remotes.append(remote)
    except Exception as e:
        logger.error('BT remotes error: %s', e)
    return remotes


async def _run_cmd(*args, timeout: float = 3.0) -> int:
    """Run a subprocess without blocking the asyncio event loop."""
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode or 0


async def start_bt_pairing() -> dict:
    """Start Bluetooth discoverable + scanning mode for pairing."""
    try:
        await _run_cmd('bluetoothctl', 'discoverable', 'on', timeout=3)
        await _run_cmd('bluetoothctl', 'scan', 'on', timeout=3)
        logger.info('BT pairing mode started')
        return {'status': 'started', 'message': 'Scanning for remotes... Press pairing button on remote.'}
    except Exception as e:
        logger.error('BT pairing error: %s', e)
        return {'status': 'error', 'message': str(e)}


# Live log streaming
log_stream_processes = {}

async def start_log_stream(ws, service: str):
    """Start streaming logs for a service."""
    global log_stream_processes

    # Stop any existing stream for this websocket
    await stop_log_stream(ws)

    try:
        process = subprocess.Popen(
            ['journalctl', '-u', service, '-f', '-n', '50', '--no-pager', '-o', 'short'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        log_stream_processes[id(ws)] = process
        logger.info('Log stream started for %s', service)

        # Read and send log lines in background
        async def stream_logs():
            try:
                while process.poll() is None:
                    line = await asyncio.get_running_loop().run_in_executor(
                        None, process.stdout.readline
                    )
                    if line and id(ws) in log_stream_processes:
                        try:
                            await ws.send(json.dumps({
                                'type': 'log_line',
                                'service': service,
                                'line': line.rstrip()
                            }))
                        except Exception:
                            break
                    await asyncio.sleep(0.01)
            except Exception as e:
                logger.error('Log stream error: %s', e)

        _background_tasks.spawn(stream_logs(), name=f"log_stream_{service}")
    except Exception as e:
        logger.error('Log stream failed to start: %s', e)

async def stop_log_stream(ws):
    """Stop log streaming for a websocket."""
    global log_stream_processes
    ws_id = id(ws)
    if ws_id in log_stream_processes:
        process = log_stream_processes[ws_id]
        process.terminate()
        del log_stream_processes[ws_id]
        logger.info('Log stream stopped')

_ALLOWED_SERVICES = {
    'beo-bluetooth', 'beo-masterlink', 'beo-router', 'beo-input', 'beo-http', 'beo-ui',
    'beo-player-sonos', 'beo-player-bluesound', 'beo-player-local', 'beo-player-mass',
    'beo-source-cd', 'beo-source-spotify', 'beo-source-plex', 'beo-source-radio',
    'beo-source-usb', 'beo-source-news', 'beo-source-tidal', 'beo-source-apple-music',
    'beo-source-kodi', 'beo-source-mass',
    'beo-librespot', 'beo-health', 'beo-beo6',
}

async def restart_service(action: str):
    """Restart a service or reboot the system."""
    logger.info('Executing restart action: %s', action)
    try:
        if action == 'reboot':
            subprocess.Popen(['sudo', 'reboot'])  # fire-and-forget, non-blocking
        elif action == 'restart-all':
            subprocess.Popen([
                'sudo', 'systemctl', 'restart',
                'beo-masterlink', 'beo-bluetooth', 'beo-router',
                'beo-player-sonos', 'beo-player-bluesound', 'beo-player-local', 'beo-player-mass',
                'beo-source-cd', 'beo-source-spotify', 'beo-source-apple-music',
                'beo-source-tidal', 'beo-source-plex', 'beo-source-radio',
                'beo-source-usb', 'beo-source-news', 'beo-source-kodi', 'beo-source-mass',
                'beo-input', 'beo-http', 'beo-ui'
            ])
        elif action.startswith('restart-'):
            service = 'beo-' + action.replace('restart-', '')
            # CD source: eject disc first, use correct service name
            if service == 'beo-cd':
                try:
                    await _run_cmd('eject', '/dev/sr0', timeout=5)
                except asyncio.TimeoutError:
                    logger.warning('eject /dev/sr0 timed out — proceeding with restart')
                service = 'beo-source-cd'
            if service not in _ALLOWED_SERVICES:
                logger.warning('Blocked restart of unknown service: %s', service)
                return
            subprocess.Popen(['sudo', 'systemctl', 'restart', service])
    except Exception as e:
        logger.error('Restart error: %s', e)

async def refresh_spotify_playlists(ws):
    """Run the Spotify playlist fetch script."""
    logger.info('Starting Spotify playlist refresh')
    try:
        # Run fetch_playlists.py in background
        spotify_dir = os.path.join(BS5C_BASE_PATH, 'services/sources/spotify')
        process = subprocess.Popen(
            ['python3', os.path.join(spotify_dir, 'fetch.py')],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=spotify_dir
        )

        # Send initial status
        await ws.send(json.dumps({
            'type': 'spotify_refresh',
            'status': 'started',
            'message': 'Fetching playlists from Spotify...'
        }))

        # Wait for completion (non-blocking via executor)
        def wait_for_process():
            stdout, stderr = process.communicate(timeout=120)
            return process.returncode, stdout, stderr

        returncode, stdout, stderr = await asyncio.get_running_loop().run_in_executor(
            None, wait_for_process
        )

        if returncode == 0:
            logger.info('Spotify playlist refresh completed')
            await ws.send(json.dumps({
                'type': 'spotify_refresh',
                'status': 'completed',
                'message': 'Playlists updated successfully'
            }))
        else:
            logger.error('Spotify playlist refresh failed: %s', stderr)
            await ws.send(json.dumps({
                'type': 'spotify_refresh',
                'status': 'error',
                'message': f'Error: {stderr[:200] if stderr else "Unknown error"}'
            }))

    except subprocess.TimeoutExpired:
        process.kill()
        logger.warning('Spotify playlist refresh timed out')
        await ws.send(json.dumps({
            'type': 'spotify_refresh',
            'status': 'error',
            'message': 'Refresh timed out after 2 minutes'
        }))
    except Exception as e:
        logger.error('Spotify error: %s', e)
        await ws.send(json.dumps({
            'type': 'spotify_refresh',
            'status': 'error',
            'message': str(e)
        }))

# ——— HTTP Webhook Server ———

async def handle_camera_stream(request):
    """Proxy camera stream from Home Assistant to avoid CORS issues."""
    ha_url = cfg("home_assistant", "url", default="http://homeassistant.local:8123")
    ha_token = os.getenv('HA_TOKEN', '')

    # Get camera entity from query params, default to doorbell
    entity = request.query.get('entity', 'camera.doorbell_medium_resolution_channel')

    response = None
    try:
        session = await get_http_session()
        headers = _showing_ha_headers()

        camera_url = f'{ha_url}/api/camera_proxy_stream/{entity}'
        logger.info('Proxying camera stream from: %s', camera_url)

        # Per-request timeout: the shared session's default total=300 would
        # kill the MJPEG stream at exactly 5 minutes. No total limit while
        # streaming — only a per-read timeout so a dead camera still errors.
        async with session.get(
            camera_url, headers=headers,
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=30),
        ) as resp:
            if resp.status == 200:
                response = web.StreamResponse(
                    status=200,
                    headers={
                        'Content-Type': resp.content_type or 'multipart/x-mixed-replace;boundary=frame',
                        'Access-Control-Allow-Origin': '*',
                        'Cache-Control': 'no-cache, no-store, must-revalidate',
                    }
                )
                await response.prepare(request)

                async for chunk in resp.content.iter_any():
                    await response.write(chunk)

                return response
            else:
                logger.warning('Camera HA returned status: %s', resp.status)
                return web.json_response(
                    {'error': f'Camera unavailable: HTTP {resp.status}'},
                    status=resp.status,
                    headers={'Access-Control-Allow-Origin': '*'}
                )
    except Exception as e:
        logger.error('Camera error: %s', e)
        if response is not None and response.prepared:
            # Stream already started — headers are sent, so a second
            # (JSON) response is impossible. Just end the stream.
            return response
        return web.json_response(
            {'error': str(e)},
            status=500,
            headers={'Access-Control-Allow-Origin': '*'}
        )

async def handle_camera_snapshot(request):
    """Get a single snapshot from camera via Home Assistant."""
    ha_url = cfg("home_assistant", "url", default="http://homeassistant.local:8123")
    ha_token = os.getenv('HA_TOKEN', '')

    entity = request.query.get('entity', 'camera.doorbell_medium_resolution_channel')

    try:
        session = await get_http_session()
        headers = {'Authorization': f'Bearer {ha_token}'} if ha_token else {}

        camera_url = f'{ha_url}/api/camera_proxy/{entity}'
        logger.info('Getting camera snapshot from: %s', camera_url)

        async with session.get(camera_url, headers=headers) as resp:
            if resp.status == 200:
                content = await resp.read()
                return web.Response(
                    body=content,
                    content_type=resp.content_type or 'image/jpeg',
                    headers={'Access-Control-Allow-Origin': '*'}
                )
            else:
                logger.warning('Camera HA returned status: %s', resp.status)
                return web.json_response(
                    {'error': f'Camera unavailable: HTTP {resp.status}'},
                    status=resp.status,
                    headers={'Access-Control-Allow-Origin': '*'}
                )
    except Exception as e:
        logger.error('Camera error: %s', e)
        return web.json_response(
            {'error': str(e)},
            status=500,
            headers={'Access-Control-Allow-Origin': '*'}
        )

async def _forward_to_router(event_type: str, data: dict):
    """Forward a UI event to the router's broadcast endpoint for WS delivery."""
    try:
        s = await get_http_session()
        async with s.post(
            ROUTER_BROADCAST_URL,
            json={'type': event_type, 'data': data},
            timeout=aiohttp.ClientTimeout(total=2),
        ) as resp:
            logger.debug('→ router broadcast %s: HTTP %d', event_type, resp.status)
    except Exception as e:
        logger.warning('Router broadcast %s failed: %s', event_type, e)


def _screen_config() -> dict:
    configured = cfg('screen', default={}) or {}
    return configured if isinstance(configured, dict) else {}


def _screen_policy_poll_interval_seconds() -> float:
    return DEFAULT_SCREEN_POLICY_POLL_SECONDS


def _screen_presence_off_delay_seconds() -> float:
    try:
        seconds = float(_screen_config().get('presence_off_delay_s', DEFAULT_SCREEN_PRESENCE_OFF_DELAY_SECONDS))
    except (TypeError, ValueError):
        seconds = DEFAULT_SCREEN_PRESENCE_OFF_DELAY_SECONDS
    return max(0.0, seconds)


def _screen_idle_off_delay_seconds() -> float:
    try:
        seconds = float(_screen_config().get('idle_off_delay_s', DEFAULT_SCREEN_IDLE_OFF_DELAY_SECONDS))
    except (TypeError, ValueError):
        seconds = DEFAULT_SCREEN_IDLE_OFF_DELAY_SECONDS
    return max(0.0, seconds)


def _screen_wake_hold_seconds() -> float:
    screen_cfg = _screen_config()
    raw = screen_cfg.get('wake_hold_s', screen_cfg.get('local_input_wake_s', DEFAULT_SCREEN_WAKE_HOLD_SECONDS))
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        seconds = DEFAULT_SCREEN_WAKE_HOLD_SECONDS
    return max(0.0, seconds)


def _hlk_config() -> dict:
    configured = cfg('hlk', default={})
    return configured if isinstance(configured, dict) else {}


def _hlk_enabled() -> bool:
    raw = _hlk_config().get('enabled', False)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return raw != 0
    return str(raw or '').strip().lower() in {'1', 'true', 'yes', 'on'}


def _screen_manual_override_seconds() -> float:
    try:
        seconds = float(_hlk_config().get('manual_override_timeout_s', DEFAULT_SCREEN_MANUAL_OVERRIDE_SECONDS))
    except (TypeError, ValueError):
        seconds = DEFAULT_SCREEN_MANUAL_OVERRIDE_SECONDS
    return max(0.0, seconds)


def _screen_policy_local_input_recent(now: float | None = None) -> bool:
    if now is None:
        now = time.monotonic()
    idle_off_delay_seconds = _screen_idle_off_delay_seconds()
    if idle_off_delay_seconds <= 0:
        return False
    last_input_at = float(_screen_policy_state.get('last_local_input_at') or 0.0)
    return last_input_at > 0.0 and max(0.0, now - last_input_at) < idle_off_delay_seconds


def _screen_policy_wake_hold_active(now: float | None = None) -> bool:
    if now is None:
        now = time.monotonic()
    return now < float(_screen_policy_state.get('wake_hold_until') or 0.0)


def _screen_policy_manual_override_active(now: float | None = None) -> bool:
    if now is None:
        now = time.monotonic()
    if _screen_policy_state.get('manual_off_latched'):
        return True
    return now < float(_screen_policy_state.get('manual_off_until') or 0.0)


def _clear_screen_manual_override(reason: str = 'local_input'):
    if _screen_policy_state.get('manual_off_until') or _screen_policy_state.get('manual_off_latched'):
        logger.debug('Screen manual override cleared (%s)', reason)
    _screen_policy_state['manual_off_latched'] = False
    _screen_policy_state['manual_off_until'] = 0.0


def _set_screen_manual_override(source: str = 'manual_off', *, persistent: bool = False):
    if persistent:
        _screen_policy_state['manual_off_latched'] = True
        _screen_policy_state['manual_off_until'] = 0.0
        logger.info('Screen manual override latched (%s)', source)
        return

    _screen_policy_state['manual_off_latched'] = False
    seconds = _screen_manual_override_seconds()
    if seconds <= 0:
        _screen_policy_state['manual_off_until'] = 0.0
        return
    _screen_policy_state['manual_off_until'] = time.monotonic() + seconds
    logger.info('Screen manual override -> %.1fs (%s)', seconds, source)


def _note_local_screen_activity(source: str = 'hid'):
    now = time.monotonic()
    canceled_wake_hold = _screen_policy_wake_hold_active(now)
    _clear_screen_manual_override(source)
    _screen_policy_state['last_local_input_at'] = now
    _screen_policy_state['wake_hold_until'] = 0.0
    logger.debug('Screen local activity -> %s%s', source, ' (wake hold canceled)' if canceled_wake_hold else '')


def _arm_screen_wake_hold(source: str = 'wake_sensor'):
    hold_seconds = _screen_wake_hold_seconds()
    if hold_seconds <= 0:
        _screen_policy_state['wake_hold_until'] = 0.0
        return

    _screen_policy_state['wake_hold_until'] = time.monotonic() + hold_seconds
    logger.debug('Screen wake hold -> %.1fs (%s)', hold_seconds, source)


def _clear_local_screen_activity():
    _screen_policy_state['last_local_input_at'] = 0.0
    _screen_policy_state['wake_hold_until'] = 0.0


def _screen_policy_normalize_state(state) -> str:
    return str(state or '').strip().lower()


def _screen_policy_state_is_playing(state) -> bool:
    return _screen_policy_normalize_state(state) in SCREEN_ACTIVE_PLAYBACK_STATES


def _screen_policy_router_is_playing(status: dict | None) -> bool:
    status = status or {}
    media_state = _screen_policy_normalize_state((status.get('media') or {}).get('state'))
    if media_state:
        return _screen_policy_state_is_playing(media_state)

    active_source_id = str(status.get('active_source') or '').strip()
    active_source = ((status.get('sources') or {}).get(active_source_id) or {})
    return _screen_policy_state_is_playing(active_source.get('state'))


def _screen_policy_router_playback_context(status: dict | None) -> dict:
    status = status or {}
    media_state = _screen_policy_normalize_state((status.get('media') or {}).get('state'))
    if not media_state:
        active_source_id = str(status.get('active_source') or '').strip()
        active_source = ((status.get('sources') or {}).get(active_source_id) or {})
        media_state = _screen_policy_normalize_state(active_source.get('state'))

    active_view = str(status.get('active_view') or '').strip().lower()
    paused_on_playing_screen = media_state == 'paused' and active_view == 'menu/playing'
    return {
        'media_state': media_state,
        'active_view': active_view,
        'playing': _screen_policy_state_is_playing(media_state),
        'paused': media_state == 'paused',
        'paused_on_playing_screen': paused_on_playing_screen,
    }


def _screen_policy_presence_snapshot(states_by_entity: dict | None) -> dict:
    normalized: dict[str, str] = {}
    for entity_id, state in (states_by_entity or {}).items():
        normalized_id = str(entity_id or '').strip()
        if not normalized_id:
            continue
        normalized[normalized_id] = _screen_policy_normalize_state(state)

    if not normalized:
        return {
            'state': 'unconfigured',
            'known': False,
            'any_on': False,
            'all_off': False,
            'states': {},
        }

    any_on = any(state == 'on' for state in normalized.values())
    known = all(state in {'on', 'off'} for state in normalized.values())
    all_off = known and all(state == 'off' for state in normalized.values())
    state = 'on' if any_on else ('off' if all_off else 'unknown')
    return {
        'state': state,
        'known': known,
        'any_on': any_on,
        'all_off': all_off,
        'states': normalized,
    }


def _screen_policy_target_state(
    *,
    manual_override_active: bool,
    wake_snapshot: dict,
    wake_hold_active: bool,
    local_input_recent: bool,
    playing: bool,
    paused: bool,
    paused_on_playing_screen: bool,
    currently_awake: bool,
    presence_snapshot: dict,
    all_off_since: float | None,
    now: float,
    presence_off_delay_seconds: float,
) -> str | None:
    if manual_override_active:
        return 'off'

    if wake_snapshot.get('any_on') and not paused:
        return 'on'

    if wake_hold_active and not paused:
        return 'on'

    if local_input_recent:
        return 'on'

    media_should_hold_awake = playing or (paused_on_playing_screen and currently_awake)
    if not media_should_hold_awake:
        return 'off'

    if presence_snapshot.get('any_on'):
        return 'on'

    if not (presence_snapshot.get('states') or {}):
        return 'on'

    if presence_snapshot.get('all_off'):
        if presence_off_delay_seconds <= 0:
            return 'off'
        if all_off_since is None:
            return 'on'
        if now - all_off_since >= presence_off_delay_seconds:
            return 'off'
        return 'on'

    return None


def _screen_command_wake_blocked(command: str) -> bool:
    if not _screen_policy_manual_override_active():
        return False
    logger.info('Ignoring %s wake while manual screen override is active', command)
    return True


def _screen_policy_status_snapshot() -> dict:
    all_off_since = _screen_policy_state.get('all_off_since')
    last_local_input_at = float(_screen_policy_state.get('last_local_input_at') or 0.0)
    manual_off_until = float(_screen_policy_state.get('manual_off_until') or 0.0)
    wake_hold_until = float(_screen_policy_state.get('wake_hold_until') or 0.0)
    now = time.monotonic()
    local_input_recent = _screen_policy_local_input_recent(now)
    manual_override_active = _screen_policy_manual_override_active(now)
    wake_hold_active = _screen_policy_wake_hold_active(now)
    idle_for_s = round(max(0.0, now - last_local_input_at), 1) if last_local_input_at > 0.0 else None
    manual_override_for_s = round(max(0.0, manual_off_until - now), 1) if manual_off_until > now else None
    wake_hold_for_s = round(max(0.0, wake_hold_until - now), 1) if wake_hold_until > now else None
    presence_off_for_s = round(max(0.0, now - all_off_since), 1) if all_off_since is not None else None
    return {
        'applied_target': _screen_policy_state.get('applied_target'),
        'last_target': _screen_policy_state.get('last_target'),
        'last_reason': _screen_policy_state.get('last_reason'),
        'manual_override_active': manual_override_active,
        'manual_override_latched': bool(_screen_policy_state.get('manual_off_latched')),
        'manual_override_for_s': manual_override_for_s,
        'manual_override_timeout_s': _screen_manual_override_seconds(),
        'local_input_recent': local_input_recent,
        'local_input_active': local_input_recent,
        'idle_for_s': idle_for_s,
        'idle_off_delay_s': _screen_idle_off_delay_seconds(),
        'wake_hold_active': wake_hold_active,
        'wake_hold_for_s': wake_hold_for_s,
        'wake_hold_s': _screen_wake_hold_seconds(),
        'local_wake_for_s': wake_hold_for_s,
        'local_input_wake_s': _screen_wake_hold_seconds(),
        'playing': bool(_screen_policy_state.get('last_playing')),
        'wake': _screen_policy_state.get('last_wake'),
        'wake_states': dict(_screen_policy_state.get('last_wake_states') or {}),
        'presence': _screen_policy_state.get('last_presence'),
        'presence_states': dict(_screen_policy_state.get('last_presence_states') or {}),
        'presence_off_for_s': presence_off_for_s,
        'all_off_for_s': presence_off_for_s,
        'poll_interval_s': _screen_policy_poll_interval_seconds(),
        'presence_off_delay_s': _screen_presence_off_delay_seconds(),
        'off_delay_s': _screen_presence_off_delay_seconds(),
    }


async def _fetch_local_hlk_state() -> dict | None:
    if not _hlk_enabled():
        return None
    session = await get_http_session()
    try:
        async with session.get(
            HLK_STATE_URL,
            timeout=aiohttp.ClientTimeout(total=1.5),
        ) as resp:
            if resp.status != 200:
                logger.debug('Local HLK state fetch failed (HTTP %d)', resp.status)
                return None
            payload = await resp.json()
            return payload if isinstance(payload, dict) else None
    except Exception as e:
        logger.debug('Local HLK state fetch error: %s', e)
        return None


def _screen_policy_local_hlk_states(
    hlk_state: dict | None,
    *,
    field: str,
    entity_id: str,
) -> dict[str, str | None]:
    if not _hlk_enabled():
        return {}

    runtime = (hlk_state or {}).get('runtime') if isinstance(hlk_state, dict) else None
    config = (hlk_state or {}).get('config') if isinstance(hlk_state, dict) else None
    if isinstance(config, dict) and config.get('enabled') is False:
        return {}

    if not isinstance(runtime, dict) or not runtime.get('available'):
        return {entity_id: None}

    return {entity_id: 'on' if runtime.get(field) else 'off'}


async def _screen_policy_tick() -> str:
    router_task = asyncio.create_task(_fetch_router_status_snapshot())
    hlk_task = asyncio.create_task(_fetch_local_hlk_state()) if _hlk_enabled() else None
    status = await router_task
    now = time.monotonic()
    if status is None:
        _screen_policy_state.update({
            'last_target': None,
            'last_reason': 'router_status_unavailable',
            'last_tick_monotonic': now,
        })
        return 'router_status_unavailable'

    playback_context = _screen_policy_router_playback_context(status)
    playing = playback_context.get('playing', False)
    paused = playback_context.get('paused', False)
    paused_on_playing_screen = playback_context.get('paused_on_playing_screen', False)
    currently_awake = _screen_policy_state.get('applied_target') == 'on' or is_backlight_on()
    hlk_state = await hlk_task if hlk_task is not None else None
    wake_states = _screen_policy_local_hlk_states(
        hlk_state,
        field='wake',
        entity_id=LOCAL_HLK_WAKE_ENTITY_ID,
    )
    wake_snapshot = _screen_policy_presence_snapshot(wake_states)
    previous_wake_state = str(_screen_policy_state.get('last_wake_sensor_state') or 'unknown')
    current_wake_state = str(wake_snapshot.get('state') or 'unknown')
    if previous_wake_state == 'on' and current_wake_state == 'off':
        _arm_screen_wake_hold('wake_sensor_off')
    elif current_wake_state == 'on':
        _screen_policy_state['wake_hold_until'] = 0.0
    _screen_policy_state['last_wake_sensor_state'] = current_wake_state

    presence_states = _screen_policy_local_hlk_states(
        hlk_state,
        field='presence_stable',
        entity_id=LOCAL_HLK_PRESENCE_ENTITY_ID,
    )
    presence_snapshot = _screen_policy_presence_snapshot(presence_states)

    if presence_snapshot.get('all_off'):
        if _screen_policy_state.get('all_off_since') is None:
            _screen_policy_state['all_off_since'] = now
    else:
        _screen_policy_state['all_off_since'] = None

    local_input_recent = _screen_policy_local_input_recent(now)
    manual_override_active = _screen_policy_manual_override_active(now)
    wake_hold_active = _screen_policy_wake_hold_active(now)
    presence_off_delay_seconds = _screen_presence_off_delay_seconds()
    target = _screen_policy_target_state(
        manual_override_active=manual_override_active,
        wake_snapshot=wake_snapshot,
        wake_hold_active=wake_hold_active,
        local_input_recent=local_input_recent,
        playing=playing,
        paused=paused,
        paused_on_playing_screen=paused_on_playing_screen,
        currently_awake=currently_awake,
        presence_snapshot=presence_snapshot,
        all_off_since=_screen_policy_state.get('all_off_since'),
        now=now,
        presence_off_delay_seconds=presence_off_delay_seconds,
    )

    if manual_override_active:
        reason = 'manual_override'
    elif wake_snapshot.get('any_on') and not paused:
        reason = 'wake_on'
    elif wake_hold_active and not paused:
        reason = 'wake_grace'
    elif local_input_recent:
        reason = 'local_input_recent'
    elif paused and not paused_on_playing_screen:
        reason = 'paused_off_playing_screen'
    elif paused and not currently_awake:
        reason = 'paused_screen_stays_off'
    elif not playing:
        reason = 'idle_timeout'
    elif presence_snapshot.get('any_on'):
        reason = 'presence_on'
    elif not (presence_snapshot.get('states') or {}):
        reason = 'playing_no_presence_sensor'
    elif presence_snapshot.get('all_off'):
        all_off_since = _screen_policy_state.get('all_off_since')
        elapsed = 0.0 if all_off_since is None else max(0.0, now - all_off_since)
        reason = (
            'presence_off_timeout'
            if presence_off_delay_seconds <= 0 or elapsed >= presence_off_delay_seconds
            else 'presence_off_grace'
        )
    else:
        reason = 'presence_unknown'

    _screen_policy_state.update({
        'last_target': target,
        'last_reason': reason,
        'last_playing': playing,
        'last_wake': wake_snapshot.get('state'),
        'last_wake_states': dict(wake_snapshot.get('states') or {}),
        'last_presence': presence_snapshot.get('state'),
        'last_presence_states': dict(presence_snapshot.get('states') or {}),
        'last_tick_monotonic': now,
    })

    if target is None:
        return f'hold:{reason}'

    if target == _screen_policy_state.get('applied_target'):
        return f'unchanged:{target}:{reason}'

    await _set_display_awake(target == 'on')
    _screen_policy_state['applied_target'] = target
    logger.info('Screen policy -> %s (%s)', target, reason)
    return f'{target}:{reason}'


async def _screen_policy_loop():
    last_state = None
    while True:
        try:
            state = await _screen_policy_tick()
            if state != last_state:
                logger.info('Screen policy: %s', state)
                last_state = state
        except Exception as e:
            logger.warning('Screen policy error: %s', e)
            last_state = 'error'
        await asyncio.sleep(_screen_policy_poll_interval_seconds())


async def process_command(data: dict) -> dict:
    """Process an incoming command (from HTTP webhook or MQTT).

    Returns a result dict with 'status' and other fields.
    """
    command = data.get('command', '')
    params = data.get('params', {})

    if command in ('screen_on', 'display_on'):
        if _screen_command_wake_blocked(command):
            return {'status': 'ok', 'screen': 'off', 'blocked': 'manual_override'}
        logger.info('Turning screen ON')
        await _set_display_awake(True)
        return {'status': 'ok', 'screen': 'on'}

    elif command == 'screen_off':
        logger.info('Turning screen OFF')
        _set_screen_manual_override('command:screen_off', persistent=True)
        _clear_local_screen_activity()
        await _set_display_awake(False, power_audio=True)
        return {'status': 'ok', 'screen': 'off'}

    elif command == 'display_off':
        logger.info('Turning display OFF (panel only)')
        _set_screen_manual_override('command:display_off', persistent=True)
        _clear_local_screen_activity()
        await _set_display_awake(False)
        return {'status': 'ok', 'screen': 'off'}

    elif command == 'screen_toggle':
        if _screen_policy_manual_override_active() and not is_backlight_on():
            logger.info('Ignoring %s wake while manual screen override is active', command)
            return {'status': 'ok', 'screen': 'off', 'blocked': 'manual_override'}
        logger.info('Toggling screen')
        toggle_backlight()
        return {'status': 'ok', 'screen': 'on' if is_backlight_on() else 'off'}

    elif command == 'show_page':
        page = params.get('page', 'now_playing')
        logger.info('Showing page: %s', page)
        await _forward_to_router('navigate', {'page': page})
        return {'status': 'ok', 'page': page}

    elif command == 'restart':
        target = params.get('target', 'all')
        logger.info('Restarting: %s', target)
        if target == 'system':
            await restart_service('reboot')
        else:
            await restart_service('restart-all')
        return {'status': 'ok', 'restart': target}

    elif command == 'wake':
        if _screen_command_wake_blocked(command):
            return {'status': 'ok', 'screen': 'off', 'blocked': 'manual_override'}
        page = params.get('page', 'now_playing')
        logger.info('Waking up and showing: %s', page)
        await _set_display_awake(True)
        # Tell the router this is user/HA activity so its auto-standby
        # idle clock resets. Otherwise `_standby_dispatched` stays True
        # forever after the first dispatch (HA's wake/screen_on commands
        # bypass /router/event and /router/volume).
        try:
            s = await get_http_session()
            await s.post(ROUTER_TOUCH, timeout=aiohttp.ClientTimeout(total=2))
        except Exception:
            pass
        await _forward_to_router('navigate', {'page': page})
        return {'status': 'ok', 'screen': 'on', 'page': page}

    elif command == 'status':
        # get_system_info() runs ~7 blocking subprocess.run calls with multi-
        # second timeouts; stay off the event loop.
        info = await asyncio.get_running_loop().run_in_executor(None, get_system_info)
        info['screen'] = 'on' if is_backlight_on() else 'off'
        info['screen_policy'] = _screen_policy_status_snapshot()
        return {'status': 'ok', **info}

    elif command == 'next_screen':
        if _screen_command_wake_blocked(command):
            return {'status': 'ok', 'screen': 'off', 'blocked': 'manual_override'}
        logger.info('Next screen')
        await _set_display_awake(True)
        await _forward_to_router('navigate', {'page': 'next'})
        return {'status': 'ok', 'action': 'next_screen'}

    elif command == 'prev_screen':
        if _screen_command_wake_blocked(command):
            return {'status': 'ok', 'screen': 'off', 'blocked': 'manual_override'}
        logger.info('Previous screen')
        await _set_display_awake(True)
        await _forward_to_router('navigate', {'page': 'previous'})
        return {'status': 'ok', 'action': 'prev_screen'}

    elif command == 'show_camera':
        if _screen_command_wake_blocked(command):
            return {'status': 'ok', 'screen': 'off', 'blocked': 'manual_override'}
        title = params.get('title', 'Camera')
        camera_entity = params.get('camera_entity', 'camera.doorbell_medium_resolution_channel')
        camera_id = params.get('camera_id', 'doorbell')
        actions = params.get('actions', {})

        logger.info('Showing camera overlay: %s (%s)', title, camera_entity)
        await _set_display_awake(True)
        await _forward_to_router('camera_overlay', {
            'action': 'show',
            'title': title,
            'camera_entity': camera_entity,
            'camera_id': camera_id,
            'actions': actions
        })
        return {'status': 'ok', 'command': 'show_camera', 'title': title}

    elif command == 'dismiss_camera':
        logger.info('Dismissing camera overlay')
        await _forward_to_router('camera_overlay', {'action': 'hide'})
        return {'status': 'ok', 'command': 'dismiss_camera'}

    elif command == 'add_menu_item':
        preset = params.get('preset')
        logger.info('Adding menu item (preset=%s)', preset)
        data = {'action': 'add'}
        if preset:
            data['preset'] = preset
        else:
            data.update({
                'title': params.get('title', 'Item'),
                'path': params.get('path', 'menu/item'),
                'after': params.get('after', 'menu/playing')
            })
        await _forward_to_router('menu_item', data)
        return {'status': 'ok', 'command': 'add_menu_item'}

    elif command == 'remove_menu_item':
        path = params.get('path')
        preset = params.get('preset')
        logger.info('Removing menu item (path=%s, preset=%s)', path, preset)
        data = {'action': 'remove'}
        if path:
            data['path'] = path
        if preset:
            data['preset'] = preset
        await _forward_to_router('menu_item', data)
        return {'status': 'ok', 'command': 'remove_menu_item'}

    elif command in ('hide_menu_item', 'show_menu_item'):
        path = params.get('path')
        action = 'hide' if command == 'hide_menu_item' else 'show'
        logger.info('%s menu item: %s', action.capitalize(), path)
        await _forward_to_router('menu_item', {'action': action, 'path': path})
        return {'status': 'ok', 'command': command}

    elif command == 'broadcast':
        # Forward an arbitrary event to UI via router WS
        evt_type = params.get('type', 'unknown')
        evt_data = params.get('data', {})
        logger.info('Broadcasting event: %s', evt_type)
        await _forward_to_router(evt_type, evt_data)
        return {'status': 'ok', 'command': 'broadcast', 'event_type': evt_type}

    else:
        return {'status': 'error', 'message': f'Unknown command: {command}'}


async def handle_webhook(request):
    """Handle incoming webhook requests from Home Assistant (HTTP)."""
    # Handle CORS preflight
    if request.method == 'OPTIONS':
        return web.Response(headers={
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type',
        })

    try:
        data = await request.json()
        logger.info('Webhook received: %s', data)

        result = await process_command(data)

        status_code = 400 if result.get('status') == 'error' else 200
        response = web.json_response(result, status=status_code)
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response

    except json.JSONDecodeError:
        response = web.json_response({'status': 'error', 'message': 'Invalid JSON'}, status=400)
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response
    except Exception as e:
        logger.error('Webhook error: %s', e)
        response = web.json_response({'status': 'error', 'message': str(e)}, status=500)
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response


async def handle_mqtt_command(data: dict):
    """Handle incoming commands via MQTT (fire-and-forget, no response needed)."""
    logger.info('MQTT command received: %s', data)
    try:
        await process_command(data)
    except Exception as e:
        logger.error('MQTT command error: %s', e)

async def handle_health(request):
    """Health check endpoint."""
    return web.json_response({
        'status': 'ok',
        'service': 'beo-input',
        'screen': 'on' if is_backlight_on() else 'off',
        'hid_connected': dev is not None,
    })

async def handle_info(request):
    """GET /info — device info (IP, hostname) for UI use."""
    return web.json_response(
        {'ip_address': _get_device_ip(), 'hostname': subprocess.run(['hostname'], capture_output=True, text=True).stdout.strip()},
        headers={'Access-Control-Allow-Origin': '*'},
    )

async def handle_led(request):
    """Quick LED control for visual feedback. GET /led?mode=pulse|on|off|blink"""
    mode = request.query.get('mode', 'pulse')

    if mode == 'pulse':
        # Quick pulse: on then off after 100ms
        set_led('on')
        asyncio.get_running_loop().call_later(0.1, lambda: set_led('off'))
    else:
        set_led(mode)

    return web.Response(text='ok')

async def handle_forward(request):
    """Forward event to Home Assistant via configured transport (webhook/MQTT/both)."""
    # Handle CORS preflight
    if request.method == 'OPTIONS':
        return web.Response(headers={
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type',
        })

    try:
        data = await request.json()
        logger.info('Forwarding via transport (%s): %s', transport.mode, data)

        await transport.send_event(data)

        response = web.json_response({'status': 'forwarded', 'transport': transport.mode})
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response

    except json.JSONDecodeError:
        response = web.json_response({'status': 'error', 'message': 'Invalid JSON'}, status=400)
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response
    except Exception as e:
        logger.error('Forward error: %s', e)
        response = web.json_response({'status': 'error', 'message': str(e)}, status=500)
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response

def _showing_ha_headers() -> dict:
    ha_token = os.getenv('HA_TOKEN', '')
    return {'Authorization': f'Bearer {ha_token}'} if ha_token else {}


def _showing_payload(data: dict, ha_url: str, entity_id: str) -> dict:
    attrs = data.get('attributes', {}) or {}
    artwork = attrs.get('entity_picture', '') or ''
    if artwork and not artwork.startswith('http'):
        artwork = f'{ha_url}{artwork}'

    app_name = attrs.get('app_name') or attrs.get('source') or '—'
    friendly_name = attrs.get('friendly_name') or entity_id or '—'
    artist = attrs.get('media_artist') or attrs.get('media_series_title') or app_name or '—'
    album = attrs.get('media_album_name') or attrs.get('source') or friendly_name or '—'

    return {
        'entity_id': entity_id,
        'title': attrs.get('media_title') or attrs.get('title') or '—',
        'artist': artist,
        'album': album,
        'app_name': app_name,
        'friendly_name': friendly_name,
        'artwork': artwork,
        'state': data.get('state', 'unknown'),
        'supported_features': attrs.get('supported_features', 0),
    }


def _showing_command_service(command: str) -> str | None:
    normalized = str(command or '').strip().lower()
    return {
        'toggle': 'media_play_pause',
        'previous': 'media_previous_track',
        'next': 'media_next_track',
        'stop': 'media_stop',
    }.get(normalized)


def _showing_error_payload(entity_id: str, *, error: str, state: str) -> dict:
    return {
        'error': error,
        'entity_id': entity_id,
        'title': '—',
        'artist': '—',
        'album': '—',
        'app_name': '—',
        'friendly_name': '—',
        'artwork': '',
        'state': state,
        'supported_features': 0,
    }


async def _fetch_showing_media_payload() -> tuple[dict | None, int, str | None]:
    ha_url = cfg("home_assistant", "url", default="http://homeassistant.local:8123")
    entity_id = cfg("showing", "entity_id")
    if not entity_id:
        return None, 400, 'showing.entity_id not configured'

    session = await get_http_session()
    headers = _showing_ha_headers()
    async with session.get(f'{ha_url}/api/states/{entity_id}', headers=headers) as resp:
        if resp.status != 200:
            return None, resp.status, 'Failed to fetch'
        data = await resp.json()
        return _showing_payload(data, ha_url, entity_id), 200, None


def _showing_relay_enabled() -> bool:
    return bool(cfg("showing", "relay_to_playing", default=True))


def _showing_relay_interval_seconds() -> float:
    try:
        return max(1.0, float(cfg("showing", "relay_interval", default=5)))
    except (TypeError, ValueError):
        return 5.0


def _showing_relay_normalize_state(state) -> str:
    return str(state or '').strip().lower()


def _showing_relay_owns_router_media(router_media: dict | None) -> bool:
    return str((router_media or {}).get('relay_id') or '').strip().lower() == SHOWING_RELAY_ID


def _showing_relay_router_is_idle(router_media: dict | None) -> bool:
    if not router_media:
        return True
    state = _showing_relay_normalize_state((router_media or {}).get('state'))
    has_content = any(
        str((router_media or {}).get(key) or '').strip()
        for key in ('title', 'artist', 'album', 'artwork', 'canvas_url', 'music_video_url')
    )
    return not has_content and state in SHOWING_RELAY_IDLE_STATES


def _showing_relay_media_is_active(showing_media: dict | None) -> bool:
    return _showing_relay_normalize_state((showing_media or {}).get('state')) in SHOWING_RELAY_ACTIVE_STATES


def _showing_relay_signature(media: dict | None) -> tuple[str, ...]:
    media = media or {}
    keys = (
        'relay_id', 'state', 'title', 'artist', 'album', 'artwork',
        'back_artwork', 'canvas_url', 'music_video_url', 'track_id',
    )
    return tuple(str(media.get(key) or '') for key in keys)


def _showing_router_media_payload(showing_media: dict | None) -> dict:
    if not _showing_relay_media_is_active(showing_media):
        return {
            'relay_id': SHOWING_RELAY_ID,
            'title': '',
            'artist': '',
            'album': '',
            'artwork': '',
            'back_artwork': '',
            'canvas_url': '',
            'music_video_url': '',
            'track_id': '',
            'state': 'idle',
            'position': '0:00',
            'duration': '0:00',
            'app_name': '',
            'friendly_name': '',
            'entity_id': '',
        }

    return {
        'relay_id': SHOWING_RELAY_ID,
        'title': showing_media.get('title') or '—',
        'artist': showing_media.get('artist') or showing_media.get('app_name') or '—',
        'album': showing_media.get('album') or showing_media.get('friendly_name') or '—',
        'artwork': showing_media.get('artwork') or '',
        'back_artwork': '',
        'canvas_url': '',
        'music_video_url': '',
        'track_id': '',
        'state': _showing_relay_normalize_state(showing_media.get('state')) or 'playing',
        'position': '0:00',
        'duration': '0:00',
        'app_name': showing_media.get('app_name') or '',
        'friendly_name': showing_media.get('friendly_name') or '',
        'entity_id': showing_media.get('entity_id') or '',
    }


def _decide_showing_relay_action(
    active_source_id: str | None,
    router_media: dict | None,
    showing_media: dict | None,
) -> tuple[str, dict | None]:
    if active_source_id:
        return 'blocked_active_source', None

    relay_owns_router_media = _showing_relay_owns_router_media(router_media)
    if not relay_owns_router_media and not _showing_relay_router_is_idle(router_media):
        return 'blocked_existing_media', None

    next_payload = _showing_router_media_payload(showing_media)
    if next_payload.get('state') == 'idle':
        if relay_owns_router_media and not _showing_relay_router_is_idle(router_media):
            return 'clear_relay', next_payload
        return 'idle', None

    if relay_owns_router_media and _showing_relay_signature(router_media) == _showing_relay_signature(next_payload):
        return 'unchanged', None

    return ('update_relay' if relay_owns_router_media else 'takeover_idle_router', next_payload)


async def _fetch_router_status_snapshot() -> dict | None:
    session = await get_http_session()
    async with session.get(
        ROUTER_STATUS_URL,
        timeout=aiohttp.ClientTimeout(total=2.0),
    ) as resp:
        if resp.status != 200:
            return None
        return await resp.json()


async def _fetch_router_media_snapshot() -> dict | None:
    session = await get_http_session()
    async with session.get(
        ROUTER_MEDIA_URL,
        timeout=aiohttp.ClientTimeout(total=2.0),
    ) as resp:
        if resp.status != 200:
            return None
        return await resp.json()


async def _post_showing_router_media(payload: dict) -> dict | None:
    session = await get_http_session()
    body = dict(payload)
    body["_reason"] = "showing_relay"
    async with session.post(
        ROUTER_MEDIA_URL,
        json=body,
        timeout=aiohttp.ClientTimeout(total=3.0),
    ) as resp:
        if resp.status != 200:
            logger.warning('Showing relay media post failed (HTTP %d)', resp.status)
            return None
        return await resp.json()


async def _showing_relay_tick() -> str:
    if not _showing_relay_enabled():
        return 'disabled'

    entity_id = cfg("showing", "entity_id")
    if not entity_id:
        return 'unconfigured'

    status = await _fetch_router_status_snapshot()
    if status is None:
        return 'router_status_unavailable'
    active_source_id = status.get('active_source')
    if active_source_id:
        return 'blocked_active_source'

    router_media = await _fetch_router_media_snapshot()
    if not _showing_relay_owns_router_media(router_media) and not _showing_relay_router_is_idle(router_media):
        return 'blocked_existing_media'

    showing_media, fetch_status, fetch_error = await _fetch_showing_media_payload()
    if showing_media is None:
        if fetch_status == 400:
            return 'unconfigured'
        logger.debug('Showing relay fetch skipped: %s', fetch_error or fetch_status)
        return 'showing_unavailable'

    action, relay_payload = _decide_showing_relay_action(active_source_id, router_media, showing_media)
    if relay_payload is None:
        return action

    response = await _post_showing_router_media(relay_payload)
    if response and response.get('dropped'):
        return f'dropped:{response.get("reason", "unknown")}'
    return action


async def _showing_relay_loop():
    last_state = None
    while True:
        try:
            state = await _showing_relay_tick()
            if state != last_state:
                logger.info('Showing relay: %s', state)
                last_state = state
        except Exception as e:
            logger.warning('Showing relay error: %s', e)
            last_state = 'error'
        await asyncio.sleep(_showing_relay_interval_seconds())


async def _handle_appletv_impl(request):
    entity_id = cfg("showing", "entity_id") or ''
    if not entity_id:
        response = web.json_response(
            _showing_error_payload('', error='showing.entity_id not configured', state='error')
        )
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response

    try:
        payload, status, error = await _fetch_showing_media_payload()
        if payload is not None:
            response = web.json_response(payload)
        else:
            response = web.json_response(
                _showing_error_payload(
                    entity_id,
                    error=error or 'Failed to fetch',
                    state='unavailable',
                ),
                status=status,
            )
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response
    except Exception as e:
        logger.error('Apple TV error: %s', e)
        response = web.json_response(
            _showing_error_payload(entity_id, error=str(e), state='error')
        )
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response


async def handle_appletv(request):
    """Fetch Apple TV media info from Home Assistant."""
    # Handle CORS preflight
    if request.method == 'OPTIONS':
        return web.Response(headers={
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type',
        })
    return await _handle_appletv_impl(request)

async def handle_appletv_command(request):
    """Forward basic transport commands to the configured showing entity."""
    if request.method == 'OPTIONS':
        return web.Response(headers={
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type',
        })

    ha_url = cfg("home_assistant", "url", default="http://homeassistant.local:8123")
    entity_id = cfg("showing", "entity_id")
    if not entity_id:
        response = web.json_response({'error': 'showing.entity_id not configured'}, status=400)
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response

    try:
        payload = await request.json()
    except json.JSONDecodeError:
        response = web.json_response({'error': 'Invalid JSON'}, status=400)
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response

    command = str((payload or {}).get('command', '')).strip().lower()
    service = _showing_command_service(command)
    if not service:
        response = web.json_response({'error': f'Unsupported command: {command or "empty"}'}, status=400)
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response

    try:
        session = await get_http_session()
        headers = _showing_ha_headers()
        headers['Content-Type'] = 'application/json'
        async with session.post(
            f'{ha_url}/api/services/media_player/{service}',
            headers=headers,
            json={'entity_id': entity_id},
        ) as resp:
            if resp.status >= 400:
                details = await resp.text()
                response = web.json_response({
                    'error': f'HA service call failed: HTTP {resp.status}',
                    'details': details[:400],
                    'command': command,
                    'service': service,
                }, status=resp.status)
            else:
                response = web.json_response({
                    'status': 'ok',
                    'entity_id': entity_id,
                    'command': command,
                    'service': service,
                })
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response
    except Exception as e:
        logger.error('Apple TV command error: %s', e)
        response = web.json_response({'error': str(e), 'command': command, 'service': service}, status=500)
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response

async def handle_people(request):
    """Fetch all person.* entities from Home Assistant."""
    # Handle CORS preflight
    if request.method == 'OPTIONS':
        return web.Response(headers={
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type',
        })

    ha_url = cfg("home_assistant", "url", default="http://homeassistant.local:8123")
    ha_token = os.getenv('HA_TOKEN', '')

    # No HA token means Home Assistant was never configured — don't probe
    # (the system page polls this every 30s, which on an HA-less device
    # produced an error log line every cycle, forever).
    if not ha_token:
        response = web.json_response([])
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response

    try:
        session = await get_http_session()
        headers = {'Authorization': f'Bearer {ha_token}'} if ha_token else {}
        async with session.get(f'{ha_url}/api/states', headers=headers) as resp:
                if resp.status == 200:
                    all_states = await resp.json()
                    # Filter for person.* entities, excluding system users
                    excluded_users = {'person.mqtt', 'person.ha_user', 'person.ha-user'}
                    people = []
                    for entity in all_states:
                        entity_id = entity.get('entity_id', '')
                        if entity_id.startswith('person.') and entity_id not in excluded_users:
                            attrs = entity.get('attributes', {})
                            entity_picture = attrs.get('entity_picture', '')
                            # Prepend HA URL to picture if relative
                            if entity_picture and not entity_picture.startswith('http'):
                                entity_picture = f'{ha_url}{entity_picture}'
                            people.append({
                                'entity_id': entity_id,
                                'friendly_name': attrs.get('friendly_name', entity_id.replace('person.', '').title()),
                                'state': entity.get('state', 'unknown'),
                                'entity_picture': entity_picture
                            })
                    response = web.json_response(people)
                else:
                    response = web.json_response({'error': 'Failed to fetch'}, status=resp.status)
                response.headers['Access-Control-Allow-Origin'] = '*'
                return response
    except Exception as e:
        logger.error('People error: %s', e)
        response = web.json_response({'error': str(e)})
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response

async def handle_bt_remotes(request):
    """Get paired Bluetooth remotes."""
    # Handle CORS preflight
    if request.method == 'OPTIONS':
        return web.Response(headers={
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type',
        })

    try:
        remotes = await asyncio.get_running_loop().run_in_executor(None, get_bt_remotes)
        response = web.json_response(remotes)
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response
    except Exception as e:
        logger.error('BT remotes error: %s', e)
        response = web.json_response({'error': str(e)}, status=500)
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response

async def handler(ws, path=None):
    clients.add(ws)
    # Ask router to re-probe all sources so menu items are up-to-date for this new client
    _background_tasks.spawn(_notify_sources_resync(), name="notify_sources_resync")
    recv_task = asyncio.create_task(receive_commands(ws))
    try:
        await ws.wait_closed()
    finally:
        recv_task.cancel()
        clients.remove(ws)
        await stop_log_stream(ws)  # Clean up any active log streams


async def _notify_sources_resync():
    """Ask router to re-probe all sources (handles any service that restarted)."""
    try:
        session = await get_http_session()
        async with session.post(ROUTER_RESYNC,
                                timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                data = await resp.json()
                resynced = data.get('resynced', [])
                if resynced:
                    logger.info('Sources resynced for new client: %s', resynced)
    except Exception as e:
        logger.debug('Source resync skipped (router not reachable): %s', e)

async def broadcast(msg: str):
    if not clients:
        return
    await asyncio.gather(
        *(ws.send(msg) for ws in clients),
        return_exceptions=True
    )

async def receive_commands(ws):
    async for raw in ws:
        try:
            msg = json.loads(raw)
            logger.debug('WS received: %s', msg)

            # Handle hardware commands
            if msg.get('type') != 'command':
                continue

            cmd    = msg.get('command')
            params = msg.get('params', {})
            if cmd == 'click':
                do_click()
            elif cmd == 'led':
                set_led(params.get('mode','on'))
            elif cmd == 'backlight':
                set_backlight(bool(params.get('on',True)))
            elif cmd == 'get_logs':
                service = params.get('service', 'beo-input')
                lines = params.get('lines', 100)
                logs = await asyncio.get_running_loop().run_in_executor(
                    None, get_service_logs, service, lines)
                await ws.send(json.dumps({'type': 'logs', 'service': service, 'logs': logs}))
            elif cmd == 'start_log_stream':
                await start_log_stream(ws, params.get('service', 'beo-masterlink'))
            elif cmd == 'stop_log_stream':
                await stop_log_stream(ws)
            elif cmd == 'get_system_info':
                info = await asyncio.get_running_loop().run_in_executor(
                    None, get_system_info)
                await ws.send(json.dumps({'type': 'system_info', **info}))
            elif cmd == 'get_network_status':
                net = await asyncio.get_running_loop().run_in_executor(None, get_network_status)
                await ws.send(json.dumps({'type': 'network_status', **net}))
            elif cmd == 'restart_service':
                await restart_service(params.get('action', ''))
            elif cmd == 'refresh_playlists':
                await refresh_spotify_playlists(ws)
            elif cmd == 'get_bt_remotes':
                remotes = await asyncio.get_running_loop().run_in_executor(None, get_bt_remotes)
                await ws.send(json.dumps({'type': 'bt_remotes', 'remotes': remotes}))
            elif cmd == 'start_bt_pairing':
                result = await start_bt_pairing()
                await ws.send(json.dumps({'type': 'bt_pairing', **result}))
        except Exception as e:
            logger.error('WebSocket error: %s', e)

# ——— HID parse & broadcast loop ———

def parse_report(rep: list, loop=None):
    global last_power_press_time, power_button_state
    global go_button_state, go_press_started_at, go_long_sent, go_long_timer_handle
    global power_button_pressed_at
    if len(rep) < 4:
        logger.warning("Truncated HID report (%d bytes), ignoring", len(rep))
        return None, None, None, None
    nav_evt = vol_evt = btn_evt = None
    laser_pos = rep[2]

    if rep[0] != 0:
        d = rep[0]
        nav_evt = {
            'direction': 'clock' if d < 0x80 else 'counter',
            'speed':     d if d < 0x80 else 256-d
        }
    if rep[1] != 0:
        d = rep[1]
        vol_evt = {
            'direction': 'clock' if d < 0x80 else 'counter',
            'speed':     d if d < 0x80 else 256-d
        }
    
    # Handle power button with state machine
    b = rep[3]
    is_power_pressed = (b & 0x80) != 0  # Check if power bit is set
    is_go_pressed = (b & 0x40) != 0     # Check if GO bit is set

    # Immediate button events for non-power / non-GO buttons
    if b in BTN_MAP and b not in (0x80, 0x40):
        btn_evt = {'button': BTN_MAP[b]}

    # State machine for GO button:
    # short press -> emit "go" on release
    # long press  -> emit "go_long" once while held, no extra "go" on release
    if is_go_pressed:
        if go_button_state == 0:
            go_button_state = 1
            go_press_started_at = time.time()
            go_long_sent = False
            _cancel_go_long_timer()
            _note_local_screen_activity('hid_button:go_press')
            if loop is not None:
                go_long_timer_handle = loop.call_later(GO_LONG_PRESS_TIME, _fire_go_long, loop)
            logger.debug("GO button pressed")
    else:
        if go_button_state == 1:
            held_for = max(0.0, time.time() - go_press_started_at)
            was_long = go_long_sent
            _reset_go_button_tracking()
            if was_long:
                logger.info("GO button released after long press (%.3fs)", held_for)
            else:
                logger.info("GO short press (%.3fs)", held_for)
                btn_evt = {'button': 'go'}
    
    # State machine for power button
    if is_power_pressed:
        # Button is pressed
        if power_button_state == 0:  # Was released before
            power_button_state = 1  # Now pressed
            power_button_pressed_at = time.time()
            logger.info("Power button pressed")
    else:
        # Button is released
        if power_button_state == 1:  # Was pressed before
            power_button_state = 0  # Now released
            held = time.time() - power_button_pressed_at if power_button_pressed_at else 0.0
            logger.info("Power button released (held %.1fs)", held)

            current_time = time.time()
            if held >= POWER_LONGPRESS_ALL_STANDBY:
                # Long-press → ALL STANDBY: screen off + local standby +
                # ML broadcast (the router handles the fan-out on 'alloff').
                logger.info("Power long-press (%.1fs) -> ALL STANDBY", held)
                do_click()
                if is_backlight_on():
                    set_backlight(False)
                try:
                    asyncio.run_coroutine_threadsafe(_send_all_standby(), loop)
                except Exception:
                    pass
                last_power_press_time = current_time
            # Check debounce time
            elif current_time - last_power_press_time > POWER_DEBOUNCE_TIME:
                logger.info("Power button action triggered")
                toggle_backlight()
                do_click()
                if is_backlight_on():
                    _note_local_screen_activity('hid_button:power_on')
                else:
                    _set_screen_manual_override('hid_button:power_off', persistent=True)
                    _clear_local_screen_activity()
                # Power off speakers when screen turns off (speakers power on via playback)
                if not is_backlight_on():
                    try:
                        asyncio.run_coroutine_threadsafe(
                            _output_power(ROUTER_OUTPUT_OFF), loop)
                    except Exception:
                        pass
                last_power_press_time = current_time
                # Create button event for power button release
                btn_evt = {'button': 'power'}
            else:
                logger.debug("Power button debounced (pressed too soon)")

    if nav_evt or vol_evt:
        _note_local_screen_activity('hid_rotary')
    if btn_evt and btn_evt.get('button') != 'power':
        _note_local_screen_activity(f"hid_button:{btn_evt.get('button')}")

    return nav_evt, vol_evt, btn_evt, laser_pos

async def _send_all_standby():
    """Forward an 'alloff' event to the router (long-press power).

    The router does the local standby (player stop, output power off,
    screen off), broadcasts STANDBY on the ML bus, and falls through to
    HA so automations can react too.
    """
    try:
        s = await get_http_session()
        await s.post(ROUTER_EVENT, json={
            'device_name': 'BeoSound5c',
            'source': 'input',
            'action': 'alloff',
            'device_type': 'All',
            'count': 1,
        }, timeout=aiohttp.ClientTimeout(total=2))
    except Exception as e:
        logger.warning('All-standby forward failed: %s', e)


async def _output_power(url):
    """Fire-and-forget call to router output power endpoint."""
    try:
        s = await get_http_session()
        await s.post(url, timeout=aiohttp.ClientTimeout(total=2))
    except Exception:
        pass

_hid_alive = True   # cleared when scan_loop thread dies

HID_RETRY_INTERVAL = 3  # seconds between device scan retries

def scan_loop(loop):
    global dev, _hid_alive

    while _hid_alive:
        # --- Try to find and open the device ---
        if dev is None:
            devices = hid.enumerate(VID, PID)
            if not devices:
                time.sleep(HID_RETRY_INTERVAL)
                continue
            try:
                d = hid.device()
                d.open(VID, PID)
                d.set_nonblocking(True)
                dev = d
                logger.info("Opened BS5 @ VID:PID=%04x:%04x", VID, PID)
                # Send current state (backlight/LED bits) to hardware on connect
                bs5_send_cmd(state_byte1)
            except Exception as e:
                logger.warning("Failed to open BS5: %s", e)
                time.sleep(HID_RETRY_INTERVAL)
                continue

        # --- Read loop (runs while device is connected) ---
        last_laser = None
        first = True
        last_probe_time = time.monotonic()
        HID_PROBE_INTERVAL = 60  # seconds between liveness probes
        try:
            while True:
                rpt = dev.read(64, timeout_ms=50)
                if rpt:
                    rep = list(rpt)
                    nav_evt, vol_evt, btn_evt, laser_pos = parse_report(rep, loop)
                    if laser_pos is None:
                        continue

                    for evt_type, evt in (
                        ('nav',    nav_evt),
                        ('volume', vol_evt),
                        ('button', btn_evt),
                    ):
                        if evt:
                            asyncio.run_coroutine_threadsafe(
                                broadcast(json.dumps({'type':evt_type,'data':evt})),
                                loop
                            )

                    if first or laser_pos != last_laser:
                        if not first and laser_pos != last_laser:
                            _note_local_screen_activity('hid_laser')
                        asyncio.run_coroutine_threadsafe(
                            broadcast(json.dumps({'type':'laser','data':{'position':laser_pos}})),
                            loop
                        )
                        last_laser, first = laser_pos, False

                # Periodic liveness probe: re-send current state to device.
                # A stale handle will throw here, triggering reconnect.
                now = time.monotonic()
                if now - last_probe_time > HID_PROBE_INTERVAL:
                    dev.write(bytes([state_byte1, 0x00]))
                    last_probe_time = now

                time.sleep(0.001)
        except Exception as e:
            logger.warning("BS5 disconnected: %s — will retry", e)
            try:
                dev.close()
            except Exception:
                pass
            _reset_go_button_tracking()
            dev = None
            time.sleep(HID_RETRY_INTERVAL)

    _hid_alive = False

# ——— Main & server start ———

async def main():
    # Start transport (webhook/MQTT/both for HA communication)
    transport.set_command_handler(handle_mqtt_command)
    await transport.start()
    logger.info("Transport started (mode: %s)", transport.mode)

    ws_srv = await websockets.serve(handler, '0.0.0.0', 8765)
    logger.info("WebSocket server listening on ws://0.0.0.0:8765")

    # Start HTTP webhook server
    app = web.Application()
    app.router.add_post('/webhook', handle_webhook)
    app.router.add_options('/webhook', handle_webhook)  # CORS preflight
    app.router.add_post('/forward', handle_forward)
    app.router.add_options('/forward', handle_forward)  # CORS preflight
    app.router.add_get('/appletv', handle_appletv)
    app.router.add_options('/appletv', handle_appletv)  # CORS preflight
    app.router.add_post('/appletv/command', handle_appletv_command)
    app.router.add_options('/appletv/command', handle_appletv_command)  # CORS preflight
    app.router.add_get('/people', handle_people)
    app.router.add_options('/people', handle_people)  # CORS preflight
    app.router.add_get('/health', handle_health)
    app.router.add_get('/info', handle_info)
    app.router.add_get('/led', handle_led)
    app.router.add_get('/bt/remotes', handle_bt_remotes)
    app.router.add_options('/bt/remotes', handle_bt_remotes)  # CORS preflight
    app.router.add_get('/camera/stream', handle_camera_stream)
    app.router.add_get('/camera/snapshot', handle_camera_snapshot)
    app.router.add_get('/update/check', handle_update_check)
    app.router.add_post('/update/run', handle_update_run)
    app.router.add_options('/update/run', handle_update_run)
    app.router.add_get('/qrcode', handle_qrcode)
    app.router.add_get('/discover/sonos', handle_discover_sonos)
    app.router.add_get('/discover/bluesound', handle_discover_bluesound)
    app.router.add_get('/hlk/state', handle_hlk_state)
    app.router.add_options('/hlk/state', handle_hlk_state)
    app.router.add_post('/hlk/tune', handle_hlk_tune)
    app.router.add_options('/hlk/tune', handle_hlk_tune)
    app.router.add_post('/config', handle_config_save)
    app.router.add_options('/config', handle_config_save)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    http_site = web.TCPSite(runner, '0.0.0.0', 8767)
    await http_site.start()
    logger.info("HTTP webhook server listening on http://0.0.0.0:8767")

    # Start HID scanning thread
    threading.Thread(target=scan_loop, args=(asyncio.get_running_loop(),), daemon=True).start()

    # Start with the display visible while services settle; the screen policy
    # loop below will blank or wake it based on playback + presence.
    set_backlight(True)
    _screen_policy_state['last_local_input_at'] = time.monotonic()

    # Startup beacon (fire-and-forget, opt-out via NO_TELEMETRY file)
    asyncio.create_task(send_beacon(BS5C_BASE_PATH))

    # Backend SHOWING relay: feed idle PLAYING/immersive from the configured entity.
    _background_tasks.spawn(_showing_relay_loop(), name="showing_relay")
    _background_tasks.spawn(_screen_policy_loop(), name="screen_policy")

    # Start systemd watchdog heartbeat
    asyncio.create_task(watchdog_loop())

    # Event-loop lag detector
    loop_monitor = LoopMonitor().start()

    try:
        # Wait for server to close
        await ws_srv.wait_closed()
    finally:
        await loop_monitor.stop()
        await _background_tasks.cancel_all()
        await transport.stop()
        if _http_session and not _http_session.closed:
            await _http_session.close()

if __name__ == '__main__':
    asyncio.run(main())
