import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'services'))
from lib import maintenance_scans as scans

@pytest.fixture
def command(monkeypatch):
    mock = AsyncMock(return_value=(0, 'LoadState=loaded\nActiveState=inactive\n'))
    monkeypatch.setattr(scans, '_command', mock)
    monkeypatch.setattr(scans, '_locked', lambda _: False)
    return mock

def run(scan):
    return asyncio.run(scans.start_scan(scan))

@pytest.mark.parametrize('scan', ['../beo-input', 'beo-library.service', {}, None])
def test_rejects_arbitrary_services(command, scan):
    assert run(scan)['status'] == 'error'
    command.assert_not_called()

@pytest.mark.parametrize('scan,unit,checks', [('tidal-download','beo-tidal-download.service',1),('provider','beo-provider-resume.service',3)])
def test_fixed_nonblocking_start(command, scan, unit, checks):
    assert run(scan)['status'] == 'accepted'
    assert command.await_count == checks+1
    assert command.await_args.args == ('sudo','-n','/bin/systemctl','start',unit,'--no-block')

@pytest.mark.parametrize('state', ['active','activating','reloading'])
def test_active_job_never_restarted(command, state):
    command.return_value = (0, f'LoadState=loaded\nActiveState={state}\n')
    assert run('provider')['status'] == 'already_running'
    assert command.await_count == 1

def test_sweep_busy_blocks_pending_scan(command):
    command.side_effect=[(0,'LoadState=loaded\nActiveState=inactive'),(0,'LoadState=loaded\nActiveState=active')]
    assert run('provider')['status'] == 'already_running'
    assert command.await_count == 2

def test_worker_lock_blocks_start(command,monkeypatch):
    monkeypatch.setattr(scans,'_locked',lambda _:True)
    assert run('tidal-download')['status'] == 'already_running'
    assert command.await_count == 1

@pytest.mark.parametrize('output', ['LoadState=not-found\nActiveState=inactive','LoadState=loaded\nActiveState=deactivating',''])
def test_unknown_state_fails_closed(command,output):
    command.return_value=(0,output)
    assert run('tidal-download')['status']=='error'
    assert command.await_count==1

def test_start_failure(command):
    command.side_effect=[(0,'LoadState=loaded\nActiveState=inactive'),(1,'')]
    assert run('tidal-download')['status']=='error'
    assert command.await_count==2

def test_timeout_not_replayed(command):
    command.side_effect=asyncio.TimeoutError
    assert run('provider')['status']=='error'
    assert command.await_count==1

def test_optional_full_sweep_unit_may_be_absent(command):
    command.side_effect=[(0,'LoadState=loaded\nActiveState=inactive'),(0,'LoadState=not-found\nActiveState=inactive'),(0,'LoadState=loaded\nActiveState=inactive'),(0,'')]
    assert run('provider')['status']=='accepted'
    assert command.await_args.args[4]=='beo-provider-resume.service'
