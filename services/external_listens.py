"""Authenticated external listening inbox. Does not operate any player.

Only exact, unique existing MA library tracks are credited. An uncertain
mark_played response is never automatically retried (MA has no event-id API).
"""
import hmac
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import unicodedata
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def canonical(value):
    return ' '.join(re.findall(r'\w+', unicodedata.normalize('NFKC', str(value or '')).casefold()))


def validate(event):
    if not isinstance(event, dict) or event.get('type') != 'external_listen':
        raise ValueError('event type')
    if event.get('predicted') or event.get('confirmation_method') != 'time_separated_audio':
        raise ValueError('unverified')
    if type(event.get('observations')) is not int or event['observations'] < 2:
        raise ValueError('observations')
    for key in ('event_id', 'title', 'artist', 'room', 'source'):
        if not isinstance(event.get(key), str) or not 1 <= len(event[key]) <= 512:
            raise ValueError('identity')
    for key in ('first_observed_at', 'confirmed_at', 'ended_at', 'listened_seconds'):
        value = event.get(key)
        if type(value) not in (float, int) or not math.isfinite(value):
            raise ValueError('timing')
    if not event['first_observed_at'] <= event['confirmed_at'] <= event['ended_at']:
        raise ValueError('ordering')
    if not 0 <= event['listened_seconds'] <= min(86400, event['ended_at']-event['first_observed_at']+1):
        raise ValueError('duration')
    return event


def resolve(event, tracks):
    matches = {}
    for track in tracks:
        if canonical(track.get('name')) != canonical(event['title']):
            continue
        # Version must be explicit in the audio result to match a versioned item.
        if canonical(track.get('version')) != canonical(event.get('version')):
            continue
        artists = [canonical(a.get('name')) for a in track.get('artists', [])]
        if canonical(event['artist']) not in artists and canonical(event['artist']) != canonical(' & '.join(a.get('name','') for a in track.get('artists', []))):
            continue
        if track.get('provider') != 'library' or not track.get('uri'):
            continue
        duration = event.get('duration_seconds')
        if duration and track.get('duration') and abs(duration-track['duration']) > max(4,duration*.03):
            continue
        matches[track['uri']] = track
    return next(iter(matches.values())) if len(matches) == 1 else None


class Inbox:
    def __init__(self, path):
        self.path = str(path)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS listens (id TEXT PRIMARY KEY,payload TEXT NOT NULL,status TEXT NOT NULL,updated REAL NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,ma_uri TEXT)')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.execute('PRAGMA busy_timeout=5000')
        try:
            with db:yield db
        finally:db.close()

    def accept(self, event):
        validate(event)
        payload = json.dumps(event, sort_keys=True, separators=(',',':'))
        with self.db() as db:
            row=db.execute('SELECT payload FROM listens WHERE id=?',(event['event_id'],)).fetchone()
            if row:
                if row[0] != payload: raise ValueError('conflicting event id')
                return 'duplicate'
            if db.execute('SELECT COUNT(*) FROM listens').fetchone()[0] >= 100000:
                raise RuntimeError('inbox full')
            db.execute('INSERT INTO listens(id,payload,status,updated) VALUES(?,?,?,?)',
                       (event['event_id'],payload,'pending',time.time()))
        return 'accepted'

    def deliver_one(self, api):
        with self.db() as db:
            row=db.execute("SELECT id,payload FROM listens WHERE status='pending' OR (status='unresolved' AND attempts<24 AND updated<?) ORDER BY updated LIMIT 1",(time.time()-3600,)).fetchone()
        if not row:return False
        identifier,payload=row;event=json.loads(payload)
        try:
            found=api('music/search',dict(search_query=event['title']+' '+event['artist'],media_types=['track'],limit=25,library_only=True))
            # Never choose from a possibly truncated list.
            tracks=found.get('tracks',[]) if isinstance(found,dict) else []
            track=resolve(event,tracks) if len(tracks)<25 else None
        except Exception:
            track=None
        if not track:
            with self.db() as db:
                db.execute("UPDATE listens SET status='unresolved',updated=?,attempts=attempts+1 WHERE id=?",(time.time(),identifier))
            return True
        # Persist intent BEFORE calling the non-idempotent downstream API. Crash
        # or network ambiguity stays uncertain for manual reconciliation.
        with self.db() as db:
            db.execute("UPDATE listens SET status='uncertain',ma_uri=?,updated=? WHERE id=?",(track['uri'],time.time(),identifier))
        try:
            api('music/mark_played',dict(media_item=track,fully_played=False,
                seconds_played=int(event['listened_seconds']),is_playing=False,user_initiated=False))
        except Exception:
            return True
        with self.db() as db:
            db.execute("UPDATE listens SET status='ma_recorded',updated=? WHERE id=?",(time.time(),identifier))
        return True


def ma_api(command, args):
    endpoint=os.getenv('EXTERNAL_LISTENS_MA_URL','http://127.0.0.1:8095/api')
    token=os.environ['MASS_TOKEN']
    request=urllib.request.Request(endpoint,data=json.dumps(dict(command=command,args=args)).encode(),
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=8) as response:
        result=json.loads(response.read(2_000_000))
    if isinstance(result,dict) and ('error_code' in result or result.get('error')):
        raise RuntimeError('MA rejected request')
    return result.get('result',result) if isinstance(result,dict) else result


def serve():
    token=os.environ.get('EXTERNAL_LISTENS_TOKEN','')
    if len(token)<32:raise RuntimeError('Configure a strong external-listens bearer token')
    root=Path(os.getenv('STATE_DIRECTORY',Path.home()/'.local/share/beosound5c-external-listens'))
    root.mkdir(parents=True,exist_ok=True)
    inbox=Inbox(root/'listens.sqlite3')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass # Never log payloads or tokens.
        def do_POST(self):
            self.connection.settimeout(10)
            status=200;result={}
            try:
                if self.path != '/library/external/listens':status=404;raise ValueError()
                if not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token):
                    status=401;raise ValueError()
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=32768:status=413;raise ValueError()
                event=json.loads(self.rfile.read(size))
                result={'event_id':event.get('event_id'),'status':inbox.accept(event)}
            except (ValueError,TypeError,KeyError):
                status=status if status!=200 else 400;result={'error':'invalid request'}
            except Exception:
                status=503;result={'error':'inbox unavailable'}
            body=json.dumps(result).encode();self.send_response(status)
            self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)))
            self.end_headers();self.wfile.write(body)
    def worker():
        while True:
            try:work=inbox.deliver_one(ma_api)
            except Exception:work=False
            time.sleep(1 if work else 5)
    threading.Thread(target=worker,daemon=True).start()
    server=ThreadingHTTPServer((os.getenv('EXTERNAL_LISTENS_BIND','0.0.0.0'),int(os.getenv('EXTERNAL_LISTENS_PORT','8798'))),Handler)
    server.daemon_threads=True
    server.serve_forever()


if __name__=='__main__':serve()
