#!/usr/bin/env python3
"""Dry-run or apply a once-only weighted count snapshot to a local MA database.

Snapshot: JSON list of [MA playlist object, ordered MA track list] pairs.
This is intentionally separate from the read-only daily familiarity checker.
Missing library tracks must be imported through MA first, with provider sync-back
explicitly guarded if provider favourites should remain unchanged.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services'))
from lib.reference_plays import prepare,apply


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database',type=Path,required=True)
    p.add_argument('--snapshot',type=Path,required=True)
    p.add_argument('--apply',action='store_true')
    p.add_argument('--backup',type=Path)
    args=p.parse_args()
    db=sqlite3.connect(f'file:{args.database.resolve()}?mode={"rw" if args.apply else "ro"}',uri=True,timeout=30)
    if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('Database integrity failed')
    plan=prepare(db,json.loads(args.snapshot.read_text()))
    print(json.dumps({'planned_playlists':len(plan),'planned_plays':sum(p['plays'] for p in plan),
                      'planned_tracks':len({i for p in plan for i,n in p['entries']})}),flush=True)
    if args.apply:
        if not args.backup:raise ValueError('--backup is required for apply')
        args.backup.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        fd=os.open(args.backup,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
        with sqlite3.connect(args.backup) as target:
            db.backup(target)
            if target.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('Backup integrity failed')
        print(json.dumps(apply(db,plan)),flush=True)
    db.close()


if __name__=='__main__':main()
