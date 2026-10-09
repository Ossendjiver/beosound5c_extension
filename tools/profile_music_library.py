#!/usr/bin/env python3
"""Incremental, low-priority audio profiling through MA's read-only catalogue API.

Never opens MA's database, commands players, or fetches protected provider
streams. Only locally accessible music is sampled. Deploy this worker on SSD
storage with the optional ONNX environment and documented models installed.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import time
import subprocess
import urllib.request
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'services'))
from lib.music_features import library_calibration

from analyze_music_audio import atomic_write, segments


def source_fingerprint(path, model_fingerprint):
    path = Path(path).resolve(strict=True)
    stat = path.stat()
    return hashlib.sha256(f'{path}:{stat.st_size}:{stat.st_mtime_ns}:{model_fingerprint}'.encode()).hexdigest()


def catalogue(command):
    result = []
    for offset in range(0, 1000000, 500):
        page = command('music/tracks/library_items', {'limit': 500, 'offset': offset})
        if not isinstance(page, list):
            raise ValueError('Invalid MA catalogue response')
        result.extend(page)
        if len(page) < 500:
            return result
    raise ValueError('MA catalogue exceeded safety limit')


def inventory(items, root):
    root = Path(root).resolve(strict=True)
    result = []
    for item in items:
        if not isinstance(item, dict) or not item.get('uri'):
            continue
        mappings = item.get('provider_mappings') or []
        aliases = {item['uri']}
        local = []
        for mapping in mappings:
            provider, identifier = mapping.get('provider_instance'), mapping.get('item_id')
            if isinstance(provider, str) and isinstance(identifier, str):
                aliases.add(f'{provider}://track/{identifier}')
            if mapping.get('provider_domain') == 'filesystem_local' and mapping.get('available'):
                candidate = (root / str(identifier or '')).resolve()
                if candidate.is_relative_to(root) and candidate.is_file():
                    local.append(candidate)
        result.append({'uri': item['uri'], 'aliases': sorted(aliases),
                       'path': str(sorted(local)[0]) if local else None,
                       'duration': item.get('duration'), 'name': item.get('name'),
                       'status': 'available' if local else 'no_local_audio'})
    return result


def update(entries, payload, analyzer, *, max_tracks=250, max_seconds=3600, now=None, sample=segments, checkpoint=None):
    now = time.time() if now is None else now
    started = time.monotonic()
    tracks = payload.setdefault('tracks', {})
    if payload.get('version') != 1 or not isinstance(tracks, dict):
        raise ValueError('Invalid audio feature cache')
    previous_failures = payload.get('failures', {})
    failures, counts = {}, {'unchanged': 0, 'analysed': 0, 'failed': 0, 'no_local_audio': 0, 'deferred': 0}
    changed = False
    for entry in entries:
        uri = entry['uri']
        if not entry.get('path'):
            counts['no_local_audio'] += 1
            continue
        fingerprint = None
        try:
            fingerprint = source_fingerprint(entry['path'], analyzer.fingerprint)
            existing = tracks.get(uri, {})
            if existing.get('fingerprint') == fingerprint:
                counts['unchanged'] += 1
                if existing.get('aliases') != entry['aliases']:
                    existing['aliases'] = list(entry['aliases'])
                    changed = True
                continue
            failed = previous_failures.get(uri, {})
            if failed.get('fingerprint') == fingerprint and now < failed.get('retry_after', 0):
                failures[uri] = failed
                counts['deferred'] += 1
                continue
            if counts['analysed'] + counts['failed'] >= max_tracks or time.monotonic()-started >= max_seconds:
                counts['deferred'] += 1
                continue
            features = analyzer.analyze(sample(Path(entry['path'])))
            # A file modified while sampling must be retried, not stamped current.
            if source_fingerprint(entry['path'], analyzer.fingerprint) != fingerprint:
                raise ValueError('Audio changed during analysis')
            tracks[uri] = {'fingerprint': fingerprint, 'aliases': list(entry['aliases']), 'audio_features': features}
            counts['analysed'] += 1
            changed = True
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            failures[uri] = {'fingerprint': fingerprint, 'reason': type(exc).__name__, 'retry_after': now+86400}
            counts['failed'] += 1
        # Checkpoint bounded batches, not an SD-card write for every recording.
        if checkpoint and (counts['analysed']+counts['failed']) % 25 == 0:
            payload['failures'] = failures
            checkpoint(payload)
    if failures != previous_failures:
        changed = True
    payload['failures'] = failures
    return counts, changed


def api_client(base, token):
    if not base.startswith(('http://', 'https://')):
        raise ValueError('An HTTP(S) MA URL is required')
    def command(name, args):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer '+token
        request = urllib.request.Request(base.rstrip('/')+'/api',
            data=json.dumps({'command': name, 'args': args}).encode(), headers=headers)
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    return command


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/media/local/music'))
    parser.add_argument('--models', type=Path, default=Path('/media/local/cache/audio-analysis/models'))
    parser.add_argument('--output', type=Path, default=Path('/media/local/cache/audio_features.json'))
    parser.add_argument('--max-tracks', type=int, default=250)
    parser.add_argument('--max-seconds', type=int, default=3600)
    args = parser.parse_args()
    if not 1 <= args.max_tracks <= 10000 or not 1 <= args.max_seconds <= 86400:
        raise ValueError('Invalid work limits')
    os.umask(0o077)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # systemd serializes scheduled runs; also protect manual invocations.
    with args.output.with_suffix('.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        configured = os.getenv('MASS_WS_URL') or os.getenv('BS5C_MASS_WS_URL') or 'http://localhost:8095'
        base = configured.replace('wss://', 'https://').replace('ws://', 'http://').removesuffix('/ws')
        items = catalogue(api_client(base, os.getenv('MASS_TOKEN', '')))
        entries = inventory(items, args.root)
        payload = json.loads(args.output.read_text()) if args.output.exists() else {'version': 1, 'tracks': {}}
        from music_audio_onnx import Analyzer
        counts, changed = update(entries, payload, Analyzer(args.models), max_tracks=args.max_tracks,
                                max_seconds=args.max_seconds, checkpoint=lambda p: atomic_write(args.output, p))
        calibration = library_calibration(payload['tracks'].values())
        if payload.get('mood_calibration') != calibration:
            payload['mood_calibration'] = calibration
            changed = True
        if changed:
            payload['generated_at'] = time.time()
            atomic_write(args.output, payload)
        # Inventory/status can change independently of audio and uses a separate file.
        status = {'checked_at': time.time(), 'catalogue_tracks': len(entries), **counts,
                  'profiled': sum(bool(payload['tracks'].get(e['uri'], {}).get('audio_features')) for e in entries)}
        atomic_write(args.output.with_suffix('.status.json'), status)
        print(json.dumps(status))
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
