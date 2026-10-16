"""Exercise the staged MA streaming method with a fake decoder, without playback."""
import ast
import asyncio
import logging
import textwrap
import os
from pathlib import Path
from types import SimpleNamespace as NS

import pytest


@pytest.fixture
def staged_stream():
    path = os.environ.get('MA_STREAMS_AUDIO_SOURCE')
    if not path:
        pytest.skip('Set MA_STREAMS_AUDIO_SOURCE to the staged MA streams/audio.py')
    source = Path(path).read_text()
    start = source.index('    async def _get_media_stream(')
    end = source.index('    async def _resolve_media_stream_source(', start)
    method = ast.parse(textwrap.dedent(source[start:end])).body[0]
    calls = []

    class Decoder:
        def __init__(self, **kwargs):
            calls.append(kwargs)
            self.input_format = kwargs['input_format']
            self.returncode = 0
            self.stdin_feeder_exception = None
            self.parsed_duration = None
            self.log_history = []
        async def start(self): self.proc = NS(pid=1)
        async def iter_chunked(self, size): yield b'1234'
        async def wait_with_timeout(self, timeout): pass
        async def close(self): pass
        async def kill(self): pass

    namespace = {'asyncio': asyncio, 'FFMpeg': Decoder,
        'VERBOSE_LOG_LEVEL': 5, 'STREAM_START_TIMEOUT': 2, 'STREAM_STALL_TIMEOUT': 2,
        'StreamType': NS(HLS='hls'), 'MediaType': NS(TRACK='track', AUDIO_SOURCE='audio_source'),
        'arriving_audio_format': lambda details: details.audio_format,
        'calculate_content_length': lambda fmt, seconds: 4,
        'AudioError': RuntimeError, 'ProviderStreamLimitError': type('ProviderStreamLimitError', (Exception,), {})}
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), method], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), 'exec'), namespace)
    return namespace['_get_media_stream'], calls


def collect(staged_stream, domain='soundcloud', kind='hls', position=135, allow=True):
    method, calls = staged_stream
    async def run():
        async def resolve(details, seek, args): return 'https://cdn.test/stream.m3u8', seek, args
        mass = NS(get_provider=lambda *args, **kwargs: NS(domain=domain), loop=asyncio.get_running_loop())
        owner = NS(mass=mass, logger=logging.getLogger('test.stream'), _resolve_media_stream_source=resolve)
        details = NS(uri='test://track/1', provider='soundcloud-instance', media_type='track',
            stream_type=kind, extra_input_args=['-user_agent', 'test'], duration=500,
            allow_seek=allow, audio_format=NS(codec_type='aac'), decoded_audio_format=None)
        output = [chunk async for chunk in method(owner, details, NS(pcm_sample_size=4), position, None, 1)]
        assert output == [b'1234']
    asyncio.run(run())
    return calls[-1]


def test_soundcloud_hls_uses_output_seek(staged_stream):
    args = collect(staged_stream)
    assert args['extra_output_args'] == ['-ss', '135']
    assert args['extra_input_args'] == ['-user_agent', 'test']


def test_other_provider_retains_input_seek(staged_stream):
    args = collect(staged_stream, domain='other')
    assert args['extra_output_args'] == []
    assert args['extra_input_args'][-2:] == ['-ss', '135']


def test_soundcloud_progressive_retains_input_seek(staged_stream):
    args = collect(staged_stream, kind='http')
    assert args['extra_output_args'] == []
    assert args['extra_input_args'][-2:] == ['-ss', '135']


@pytest.mark.parametrize('position,allow', [(0, True), (135, False)])
def test_start_and_disallowed_seek_add_no_seek_arguments(staged_stream, position, allow):
    args = collect(staged_stream, position=position, allow=allow)
    assert args['extra_output_args'] == []
    assert '-ss' not in args['extra_input_args']
