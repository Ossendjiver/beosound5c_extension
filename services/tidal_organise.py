#!/usr/bin/env python3
"""Organise completed Tidal passes only, under the downloader's shared lock."""
import fcntl,json,time,subprocess
from pathlib import Path
from lib.ipod_sync import atomic_json
from lib.tidal_organise import destination,move_no_replace,matches
import tidal_download as download
CACHE=download.CACHE;ROOT=Path('/media/local/music');STAGING=ROOT/'HomeMedia-Tidal';STATUS=Path('/home/thomas/beosound5c/web/json/tidal_tidy_status.json')
def publish(state,**fields):atomic_json(STATUS,dict(state=state,updated=time.time(),**fields));STATUS.chmod(0o644)
def main():
    if subprocess.check_output(['findmnt','-n','-o','TARGET','/media/local'],text=True).strip()!='/media/local':raise ValueError('SSD missing')
    with (CACHE/'worker.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:publish('waiting_for_download');return
        index=download.read(CACHE/'index.json',{})
        if not index.get('last_completed'):publish('waiting_for_completed_pass');return
        if index.get('last_organised',0)>=index['last_completed']:return
        if not download.idle():publish('waiting_for_idle');return
        journal=CACHE/'tidy-journal.json';pending=download.read(journal,{})
        if pending:
            record=index['tracks'][pending['id']]
            source=Path(pending['source']);target=Path(pending['target'])
            if not source.resolve().is_relative_to(STAGING.resolve()) or not target.resolve().is_relative_to(ROOT.resolve()):raise ValueError('Unsafe recovery')
            if not source.exists() and matches(target,record):
                record['path']=str(target);atomic_json(CACHE/'index.json',index)
            elif not source.exists():raise ValueError('Interrupted move could not be verified')
            atomic_json(journal,{})
        moved=0;errors=0
        for ident,record in index.get('tracks',{}).items():
            source=Path(record['path']).resolve()
            if not source.is_relative_to(STAGING.resolve()):continue
            if not download.idle():publish('waiting_for_idle',moved=moved,errors=errors);return
            try:
                if not matches(source,record):raise ValueError('Source verification failed')
                target=destination(ROOT,record,ident);target.parent.mkdir(parents=True,exist_ok=True)
                atomic_json(journal,{'id':ident,'source':str(source),'target':str(target)})
                move_no_replace(source,target)
                record['path']=str(target);atomic_json(CACHE/'index.json',index);atomic_json(journal,{})
                moved+=1;publish('organising',moved=moved,errors=errors)
            except (ValueError,OSError):
                if not source.exists():raise # Leave recovery intent intact.
                atomic_json(journal,{});errors+=1
        if not errors:index['last_organised']=index['last_completed'];atomic_json(CACHE/'index.json',index)
        publish('complete' if not errors else 'needs_attention',moved=moved,errors=errors)
if __name__=='__main__':
    try:main()
    except Exception as e:publish('error',message=type(e).__name__)
