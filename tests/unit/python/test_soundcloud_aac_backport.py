"""Contract tests for a staged MA provider; set MA_SOUNDCLOUD_SOURCE to run.

Run against the patched provider before deploying, without importing MA, logging
in, fetching audio, or touching a running server.
"""
import ast
import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest


@pytest.fixture
def resolver():
    source_path = os.environ.get('MA_SOUNDCLOUD_SOURCE')
    if not source_path:
        pytest.skip('MA_SOUNDCLOUD_SOURCE must point to the staged provider source')
    tree = ast.parse(Path(source_path).read_text())
    provider = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'SoundcloudMusicProvider')
    method = next(n for n in provider.body if getattr(n, 'name', '') == '_get_stream_url')
    namespace = {'Any': object, 'urlparse': urlparse, 'PREVIEW_CDN_HOST': 'cf-preview-media.sndcdn.com'}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), str(source_path), 'exec'), namespace)
    return namespace['_get_stream_url']


def run(resolver, formats, replies):
    calls = []

    async def get(url, headers):
        key = url.split('?')[0]
        calls.append(key)
        return replies[key]

    api = SimpleNamespace(client_id='test', headers={}, get=get)
    track = {'track_authorization': 'test', 'media': {'transcodings': formats}}
    return asyncio.run(resolver(SimpleNamespace(_soundcloud=api), track)), calls


def stream(preset, protocol, url, **extras):
    return {'preset': preset, 'format': {'protocol': protocol}, 'url': url, **extras}


def test_aac_only_track_resolves(resolver):
    value, calls = run(resolver, [stream('aac_160k', 'hls', 'https://api.test/aac')],
                       {'https://api.test/aac': {'url': 'https://cdn.test/full.m3u8'}})
    assert value == 'https://cdn.test/full.m3u8'
    assert len(calls) == 1


def test_progressive_mp3_retains_precedence(resolver):
    value, calls = run(resolver, [stream('aac_160k', 'hls', 'https://api.test/aac'),
                                 stream('mp3_128', 'progressive', 'https://api.test/mp3')],
                       {'https://api.test/mp3': {'url': 'https://cdn.test/full.mp3'}})
    assert value.endswith('full.mp3')
    assert calls == ['https://api.test/mp3']


def test_full_stream_wins_over_preview(resolver):
    value, _ = run(resolver, [stream('mp3_128', 'progressive', 'https://api.test/preview', snipped=True),
                             stream('aac_160k', 'hls', 'https://api.test/aac')],
                   {'https://api.test/preview': {'url': 'https://cf-preview-media.sndcdn.com/preview.mp3'},
                    'https://api.test/aac': {'url': 'https://cdn.test/full.m3u8'}})
    assert value == 'https://cdn.test/full.m3u8'


def test_missing_authorization_does_not_request_stream(resolver):
    assert asyncio.run(resolver(SimpleNamespace(), {'media': {'transcodings': []}})) is None


def test_unsupported_format_is_not_requested(resolver):
    value, calls = run(resolver, [stream('abr_sq', 'hls', 'https://api.test/abr')], {})
    assert value is None
    assert calls == []


def test_invalid_response_falls_through_to_next_format(resolver):
    value, calls = run(resolver, [stream('mp3_128', 'progressive', 'https://api.test/mp3'),
                                 stream('aac_160k', 'hls', 'https://api.test/aac')],
                       {'https://api.test/mp3': {'error': 'unavailable'},
                        'https://api.test/aac': {'url': 'https://cdn.test/full.m3u8'}})
    assert value.endswith('full.m3u8')
    assert len(calls) == 2
