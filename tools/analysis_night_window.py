#!/usr/bin/env python3
"""Melbourne-time autostart policy; manual profiler scripts remain available."""
import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import urllib.request
from zoneinfo import ZoneInfo

TASK = 'audio_analysis_background_scan'
ZONE = ZoneInfo('Australia/Melbourne')

def in_window(now):
    local = now.astimezone(ZONE)
    return local.hour >= 23 or local.hour < 5

def night_key(now):
    local = now.astimezone(ZONE)
    return (local.date() - dt.timedelta(days=local.hour < 5)).isoformat()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    now = dt.datetime.now(dt.timezone.utc)
    if args.check:
        return 0 if in_window(now) else 1
    base = os.environ['MASS_WS_URL'].replace('ws://','http://').replace('wss://','https://').removesuffix('/ws').rstrip('/')
    def api(command, **params):
        request = urllib.request.Request(base+'/api', data=json.dumps({'command':command,'args':params}).encode(), headers={'Authorization':'Bearer '+os.environ['MASS_TOKEN'],'Content-Type':'application/json'})
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)
    cache = Path(os.getenv('BS5C_AUDIO_CACHE','/media/local/cache'))
    with (cache/'ma-analysis-window.lock').open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return 0
        # Persistently disable MA's UTC daily autostart, without cancelling manual work.
        task = api('tasks/get', task_id=TASK)
        if task.get('schedule',{}).get('enabled'):
            api('tasks/set_enabled', task_id=TASK, enabled=False)
        if not in_window(now) or task.get('status') in ('running','pending','queued'):
            return 0
        queues = api('player_queues/all')
        if not isinstance(queues,list) or not queues:
            return 0
        if any(q.get('state') not in ('idle','off','stopped','paused') for q in queues):
            return 0
        ledger = cache/'ma-analysis-night.json'
        try: previous = json.loads(ledger.read_text())
        except FileNotFoundError: previous = {}
        night = night_key(now)
        if previous.get('night') == night: return 0
        # Commit before dispatch: an uncertain response must never start a duplicate.
        temp = ledger.with_suffix('.tmp')
        with temp.open('w') as output:
            json.dump({'night':night,'requested_at':now.isoformat()},output)
            output.flush(); os.fsync(output.fileno())
        temp.replace(ledger)
        # Recheck after I/O and immediately before dispatch.
        if not in_window(dt.datetime.now(dt.timezone.utc)): return 0
        queues = api('player_queues/all')
        if not queues or any(q.get('state') not in ('idle','off','stopped','paused') for q in queues): return 0
        api('tasks/run', task_id=TASK)
        print('Started MA audio analysis for Melbourne night '+night)
    return 0

if __name__ == '__main__': raise SystemExit(main())
