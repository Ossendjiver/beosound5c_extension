#!/usr/bin/env python3
"""Apply reference counts OFFLINE on the MA host as root; default is read-only planning.

Stop Music Assistant and its clients before applying. Snapshot is a JSON list of
[MA playlist object, ordered tracks] pairs. A fresh backup is mandatory.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services'))
from lib.reference_plays import prepare,apply


def require_offline(path):
    """Fail closed if another process holds the DB or any SQLite sidecar."""
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise RuntimeError('Apply must run as root on the Linux MA host, with MA stopped')
    targets = {path.resolve()}
    targets.update(Path(str(path.resolve())+suffix) for suffix in ('-wal','-shm','-journal'))
    identities = {(p.stat().st_dev,p.stat().st_ino) for p in targets if p.exists()}
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit() or int(proc.name)==os.getpid():continue
        try:
            for fd in (proc/'fd').iterdir():
                try:
                    stat=fd.stat()
                    if (stat.st_dev,stat.st_ino) in identities:
                        raise RuntimeError('Database is open by process '+proc.name+'; stop MA and clients first')
                except FileNotFoundError:pass
        except FileNotFoundError:pass
        except PermissionError as exc:
            raise RuntimeError('Cannot verify database holders; refusing apply') from exc


def check_integrity(db,fts=False):
    if db.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:
        raise RuntimeError('Full database integrity check failed')
    if fts:
        names=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND lower(sql) LIKE '%using fts5%'")]
        for name in names:
            quoted='"'+name.replace('"','""')+'"'
            try:
                db.execute('INSERT INTO '+quoted+'('+quoted+',rank) VALUES (\'integrity-check\',1)')
            finally:db.rollback()


def flush(path):
    fd=os.open(path,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database',type=Path,required=True)
    p.add_argument('--snapshot',type=Path,required=True)
    p.add_argument('--apply',action='store_true')
    p.add_argument('--backup',type=Path)
    args=p.parse_args()
    if args.apply:require_offline(args.database)
    with sqlite3.connect(f'file:{args.database.resolve()}?mode={"rw" if args.apply else "ro"}',uri=True,timeout=30) as db:
        check_integrity(db)
        plan=prepare(db,json.loads(args.snapshot.read_text()))
        print(json.dumps({'planned_playlists':len(plan),'planned_plays':sum(p['plays'] for p in plan),
                          'planned_tracks':len({i for p in plan for i,n in p['entries']})}),flush=True)
        if args.apply:
            if not args.backup:raise ValueError('--backup is required for apply')
            args.backup.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            fd=os.open(args.backup,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
            target=sqlite3.connect(args.backup)
            try:db.backup(target);check_integrity(target,fts=True)
            finally:target.close()
            flush(args.backup)
            check_integrity(db,fts=True)
            print(json.dumps(apply(db,plan)),flush=True)
            check_integrity(db,fts=True)
    db.close()
    if args.apply:flush(args.database)


if __name__=='__main__':main()
