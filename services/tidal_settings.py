#!/usr/bin/env python3
"""Small local BS5c settings endpoint; no playback/library restarts required."""
import json,socket,time,threading
from pathlib import Path
from urllib.parse import urlparse
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from lib.ipod_sync import atomic_json
CONFIG=Path('/etc/beosound5c/tidal-download.json')
SETTINGS=Path('/media/local/cache/tidal-download/settings.json')
WRITE_LOCK=threading.Lock()

def valid_count(value):
    if type(value) is not int or not 50<=value<=10000 or value%50:raise ValueError('Choose 50–10,000 tracks in steps of 50')
    return value

def current():
    base=json.loads(CONFIG.read_text())
    try:override=json.loads(SETTINGS.read_text());base['tracks']=valid_count(override['tracks'])
    except (FileNotFoundError,ValueError,KeyError,TypeError):pass
    return {'tracks':base.get('tracks',250),'albums':base.get('albums',20),'step':50,'minimum':50,'maximum':10000}

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def reply(self,status,data):
        body=json.dumps(data).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.send_header('Access-Control-Allow-Origin','*');self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body)
    def do_GET(self):
        if self.path!='/settings':self.reply(404,{});return
        try:self.reply(200,current())
        except Exception:self.reply(503,{'error':'Settings unavailable'})
    def do_OPTIONS(self):
        self.send_response(204);self.send_header('Access-Control-Allow-Origin','*');self.send_header('Access-Control-Allow-Methods','GET, POST');self.send_header('Access-Control-Allow-Headers','Content-Type');self.end_headers()
    def do_POST(self):
        self.connection.settimeout(5)
        if self.path!='/settings':self.reply(404,{});return
        origin=self.headers.get('Origin');local=self.client_address[0] in ('127.0.0.1','::1',self.connection.getsockname()[0])
        host=self.headers.get('Host','').split(':')[0]
        if not local or (origin and (urlparse(origin).scheme!='http' or urlparse(origin).hostname!=host)):
            self.reply(403,{'error':'Edit from the BS5c display'});return
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=512 or self.headers.get('Content-Type','').split(';')[0]!='application/json':raise ValueError('Invalid request')
            payload=json.loads(self.rfile.read(length))
            if set(payload)!={'tracks'}:raise ValueError('Only track count is editable')
            count=valid_count(payload['tracks'])
            with WRITE_LOCK:atomic_json(SETTINGS,{'tracks':count,'updated':time.time()})
            self.reply(200,current())
        except (ValueError,TypeError,KeyError):self.reply(400,{'error':'Choose 50–10,000 tracks in steps of 50'})
        except OSError:self.reply(503,{'error':'Settings could not be saved'})

if __name__=='__main__':ThreadingHTTPServer(('0.0.0.0',8789),Handler).serve_forever()
