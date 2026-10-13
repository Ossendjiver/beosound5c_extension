"""Fixed, non-interrupting service starts for the system maintenance cards."""
import asyncio
import fcntl
from pathlib import Path

SCANS = {
    'tidal-download': ('beo-tidal-download.service',),
    'provider': ('beo-provider-resume.service', 'beo-provider-sweep.service',
                 'beo-provider-profile.service'),
}
LOCKS = {
    'tidal-download': Path('/media/local/cache/tidal-download/worker.lock'),
    'provider': Path('/media/local/cache/provider_profiles.lock'),
}
_start_lock = asyncio.Lock()


async def _command(*args):
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        output, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode, output.decode('utf-8', errors='replace')


def _locked(path):
    # Do not create or truncate worker files. Workers still enforce their own lock.
    try:
        with path.open('r') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            return False
    except FileNotFoundError:
        return False


async def start_scan(scan):
    units = SCANS.get(scan) if isinstance(scan, str) else None
    if not units:
        return {'status': 'error', 'message': 'Unknown scan'}
    async with _start_lock:
        try:
            for unit in units:
                code, output = await _command('/bin/systemctl', 'show', unit,
                                              '--property=LoadState,ActiveState')
                state = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
                if code or state.get('LoadState') != 'loaded':
                    return {'status': 'error', 'message': 'Scan service unavailable; update required'}
                if state.get('ActiveState') in ('active', 'activating', 'reloading'):
                    return {'status': 'already_running', 'message': 'Scan already running'}
                if state.get('ActiveState') not in ('inactive', 'failed'):
                    return {'status': 'error', 'message': 'Cannot verify scan is idle'}
            if await asyncio.to_thread(_locked, LOCKS[scan]):
                return {'status': 'already_running', 'message': 'Scan already running'}
            code, _ = await _command('sudo', '-n', '/bin/systemctl', 'start', units[0], '--no-block')
            if code:
                return {'status': 'error', 'message': 'Could not start scan'}
            return {'status': 'accepted', 'message': 'Start requested · waits for idle playback'}
        except asyncio.TimeoutError:
            return {'status': 'error', 'message': 'Request timed out; check status before retrying'}
        except OSError:
            return {'status': 'error', 'message': 'Could not check or start scan'}
