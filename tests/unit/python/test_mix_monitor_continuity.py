import asyncio
from types import SimpleNamespace
import pytest
import library

@pytest.mark.asyncio
async def test_unreachable_queue_does_not_block_healthy_queue_and_shutdown_cancels_checks(monkeypatch):
    sleep=asyncio.sleep
    async def quick_sleep(_):await sleep(.005)
    monkeypatch.setattr(library.asyncio,'sleep',quick_sleep)
    stopped=asyncio.Event();count=0
    async def tick(q):
        nonlocal count
        if q=='bad':
            try:await asyncio.Event().wait()
            finally:stopped.set()
        else:count+=1
    service=SimpleNamespace(mixes=SimpleNamespace(sessions={'bad':{},'good':{}},tick=tick))
    task=asyncio.create_task(library.LibraryService._mix_loop(service))
    await sleep(.055)
    assert count>=3
    task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert stopped.is_set()

@pytest.mark.asyncio
async def test_transient_error_keeps_session_and_records_retry(monkeypatch):
    sleep=asyncio.sleep
    async def quick_sleep(_):await sleep(.005)
    monkeypatch.setattr(library.asyncio,'sleep',quick_sleep)
    async def tick(_):raise OSError('temporary connection gap')
    service=SimpleNamespace(mixes=SimpleNamespace(sessions={'q':{'owned':['song']}},tick=tick))
    task=asyncio.create_task(library.LibraryService._mix_loop(service))
    await sleep(.035);task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert service.mixes.sessions['q']['refresh_status']=='retrying'
    assert service.mixes.sessions['q']['owned']==['song']
