#!/usr/bin/env python3
"""Bounded idle-only recovery passes; timers keep retrying unfinished work."""
import json,os,subprocess,sys
from pathlib import Path
from analyze_music_audio import atomic_write


def pending(status):
    return bool(status.get('deferred') or status.get('failed') or status.get('complete') is False)


def main():
    tools=Path(__file__).resolve().parent
    cache=Path(os.getenv('BS5C_AUDIO_CACHE','/media/local/cache'))
    path=cache/'audio_features.status.json'
    try:status=json.loads(path.read_text())
    except FileNotFoundError:status={'complete':False}
    commands=[]
    if pending(status):
        commands.append([sys.executable,str(tools/'profile_music_library.py'),'--max-tracks','250','--max-seconds','1800'])
    commands.append([sys.executable,str(tools/'profile_provider_library.py'),'--retry-pending','--respect-retry-backoff','--max-items','10000','--max-samples','250','--max-seconds','1800'])
    if len(commands)==2:
        cursor=cache/'profile_resume.json'
        try:next_kind=json.loads(cursor.read_text()).get('next','local')
        except FileNotFoundError:next_kind='local'
        if next_kind=='provider':commands.reverse()
        atomic_write(cursor,{'next':'provider' if next_kind=='local' else 'local'})
    result=0
    for command in commands:
        run=subprocess.run(command,check=False)
        result=1 if run.returncode else result
    return result


if __name__=='__main__':raise SystemExit(main())
