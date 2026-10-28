import asyncio
import copy
from unittest.mock import AsyncMock

from lib.youtube_watchdog import YouTubeWatchdog, DEFAULT_PIRS


def watchdog(saved=None):
    writes = []
    now = [1000.0]
    w = YouTubeWatchdog(None, {'enabled': True, 'host': 'tv.test'},
        ha_base='http://ha.test:8123/api', headers={}, load=lambda: copy.deepcopy(saved or {}),
        save=lambda data: writes.append(copy.deepcopy(data)), clock=lambda: now[0])
    return w, writes, now


def test_detection_survives_restart_and_acknowledgement_is_incident_specific():
    w, writes, now = watchdog()
    w.observe(True)
    first = w.state['incident']
    assert not w.acknowledge('music-prompt')
    assert w.acknowledge(first)
    restored, _, _ = watchdog(writes[-1])
    assert restored.state['acknowledged']
    restored.observe(None)
    assert restored.state['present']
    restored.observe(False)
    assert restored.state['present']
    restored.observe(False)
    assert not restored.state['present']
    restored.observe(True)
    assert restored.state['incident'] != first
    assert not restored.state['acknowledged']


def test_timeouts_and_one_404_never_rearm_a_dismissed_warning():
    w, writes, _ = watchdog()
    w.observe(True)
    w.acknowledge(w.state['incident'])
    w.observe(False)
    w.observe(None)
    w.observe(False)
    assert w.state['present'] and w.state['acknowledged']
    assert len(writes) == 2


def test_only_fresh_edges_from_three_configured_pirs_are_accepted():
    w, _, now = watchdog()
    assert set(DEFAULT_PIRS) == w.pirs
    assert not w.note_state('binary_sensor.bedroom_pir', 'off', 'on')
    assert not w.note_state(DEFAULT_PIRS[0], 'on', 'on')
    assert not w.note_state(DEFAULT_PIRS[0], 'off', 'off')
    assert not w.note_state(DEFAULT_PIRS[0], 'off', 'on', 900)
    assert not w.note_state(DEFAULT_PIRS[0], 'off', 'on', 2000)
    for e in DEFAULT_PIRS:
        assert w.note_state(e, 'off', 'on')


def test_ha_unavailable_and_ancient_snapshots_do_not_trigger():
    w, _, _ = watchdog()
    w._ha_state(None, None)
    w._ha_state({'entity_id': DEFAULT_PIRS[0], 'state': 'unavailable'}, None)
    w._ha_state({'entity_id': DEFAULT_PIRS[0], 'state': 'on', 'last_changed': '1970-01-01T00:00:01+00:00'}, None)
    assert not w.motion.is_set()


def test_warning_requires_incident_and_motion_and_has_no_playback_side_effects():
    async def run():
        w, writes, now = watchdog()
        w._post = AsyncMock()
        assert not await w.notify()
        w.observe(True)
        assert not await w.notify()
        w.note_state(DEFAULT_PIRS[0], 'off', 'on')
        assert await w.notify()
        calls = w._post.call_args_list
        assert len(calls) == 2
        assert calls[0].args[0].endswith('/router/broadcast')
        assert calls[0].args[1]['data']['kind'] == 'youtube_watchdog'
        assert calls[1].args == ('http://127.0.0.1:8767/webhook', {'command': 'display_on'})
        assert not await w.notify()
        now[0] += 1801
        assert not await w.notify()  # No new motion, no screen wake.
        w.note_state(DEFAULT_PIRS[1], 'off', 'on')
        assert await w.notify()
        w.acknowledge(w.state['incident'])
        now[0] += 1801
        w.note_state(DEFAULT_PIRS[2], 'off', 'on')
        assert not await w.notify()
    asyncio.run(run())


def test_failed_delivery_does_not_mark_warning_shown():
    async def run():
        w, _, _ = watchdog()
        w.observe(True)
        w.note_state(DEFAULT_PIRS[0], 'off', 'on')
        w._post = AsyncMock(side_effect=RuntimeError('not connected'))
        try:
            await w.notify()
        except RuntimeError:
            pass
        assert not w.state['last_shown']
    asyncio.run(run())


class Response:
    def __init__(self, status, data=None):
        self.status, self.data = status, data
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        return False
    async def json(self, **kwargs):
        return self.data


class Session:
    def __init__(self, responses):
        self.responses, self.urls = iter(responses), []
    def get(self, url, **kwargs):
        self.urls.append(url)
        return next(self.responses)


def test_probes_only_known_stock_ids_not_replacement_or_firmware_stub():
    async def run():
        w, _, _ = watchdog()
        w.session = Session([Response(404), Response(404)])
        assert await w.check() is False
        assert len(w.session.urls) == 2
        assert all('tUb3Xq7Lm9' not in u and 'cobalt' not in u for u in w.session.urls)
        assert w.last_result == 'absent'
        w.session = Session([Response(200, {'name': 'YouTube', 'id': '111299001912'})])
        assert await w.check() is True
        assert w.state['present']
    asyncio.run(run())


def test_error_responses_malformed_or_wrong_apps_do_not_mean_absence():
    async def run():
        w, _, _ = watchdog()
        w.observe(True)
        for response in (Response(403), Response(500), Response(200, []), Response(200, {'name': 'Netflix'})):
            w.session = Session([response, Response(404)])
            assert await w.check() is None
            assert w.state['present']
    asyncio.run(run())


def test_polling_does_not_write_on_every_successful_check():
    w, writes, _ = watchdog()
    for _ in range(20):
        w.observe(True)
    assert len(writes) == 1


def test_explicitly_disabled_until_configured():
    w = YouTubeWatchdog(None, {}, ha_base='', headers={}, load=lambda: {}, save=lambda _: None)
    assert not w.enabled


def test_same_name_replacement_response_cannot_create_stock_incident():
    async def run():
        w, writes, _ = watchdog()
        w.session = Session([Response(200, {'name': 'YouTube', 'id': 'tUb3Xq7Lm9.Tube'}), Response(404)])
        assert await w.check() is None
        assert not w.state['present']
        assert writes == []
    asyncio.run(run())


def test_unknown_tv_checks_retry_with_backoff_then_resume_normal_polling(monkeypatch):
    async def run():
        import pytest
        import lib.youtube_watchdog as module
        w, _, _ = watchdog()
        results = iter([None, None, False])
        async def check():
            result = next(results)
            w.observe(result)
            return result
        sleeps = []
        async def sleep(delay):
            sleeps.append(delay)
            if len(sleeps) == 3:
                raise asyncio.CancelledError()
        w.check = check
        monkeypatch.setattr(module.asyncio, 'sleep', sleep)
        with pytest.raises(asyncio.CancelledError):
            await w.poll_loop()
        assert sleeps == [30, 60, 300]
        assert not w.state['present']
    asyncio.run(run())
