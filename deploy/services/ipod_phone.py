#!/usr/bin/env python3
"""LAN-only, token-scoped phone iPod exports; no playback or arbitrary file API."""
import hashlib,hmac,ipaddress,json,os,re,threading,time,urllib.parse,urllib.request
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from lib.ipod_sync import destination,key,atomic_json
from lib.tidal_cache import cached_source
CONFIG=Path('/etc/beosound5c/ipod-phone.json')
SYNC_CONFIG=Path('/etc/beosound5c/ipod-sync.json')
MUSIC=Path('/media/local/music')
EXTENSIONS={'.flac','.mp3','.m4a','.aac','.ogg','.opus','.wav','.aiff','.wma'}
LOCK=threading.Lock();PLAN={'state':'idle'};FILES={}

def read(path,default):
    try:return json.loads(Path(path).read_text())
    except FileNotFoundError:return default

def lan(address):
    try:
        ip=ipaddress.ip_address(address)
        return ip.is_loopback or any(ip in ipaddress.ip_network(n) for n in ['10.0.0.0/8','172.16.0.0/12','192.168.0.0/16'])
    except ValueError:return False

def local_call(path,body=None):
    request=urllib.request.Request('http://127.0.0.1:8788'+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=120) as r:return json.load(r)

def ma(command,args):
    base=os.environ['MASS_WS_URL'].replace('ws://','http://').replace('wss://','https://').removesuffix('/ws')
    request=urllib.request.Request(base+'/api',data=json.dumps({'command':command,'args':args}).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+os.environ['MASS_TOKEN']})
    with urllib.request.urlopen(request,timeout=30) as r:return json.load(r)

def file_source(item,roots,index):
    for mapping in item.get('provider_mappings',[]):
        if mapping.get('provider_domain')!='filesystem_local' or not mapping.get('available',True):continue
        for root in roots:
            path=(root/str(mapping.get('item_id',''))).resolve()
            if path.is_relative_to(root) and path.is_file() and path.suffix.lower() in EXTENSIONS:return path
    source=cached_source(index,item)
    return source if source and source.suffix.lower() in EXTENSIONS else None

def checked(record):
    path=Path(record['source']).resolve()
    if not any(path.is_relative_to(root) for root in record['roots']) or not path.is_file():raise ValueError('Source unavailable')
    st=path.stat()
    if st.st_size!=record['size'] or st.st_mtime_ns!=record['mtime']:raise ValueError('Source changed; refresh selection')
    return path

def build_plan(token):
    global PLAN,FILES
    try:
        cfg=read(SYNC_CONFIG,{})
        selection=local_call('/library/ipod/catalogue?'+urllib.parse.urlencode({'limit':cfg.get('max_tracks',10000),'playlists':json.dumps(cfg.get('playlists',[]))}))
        roots=[MUSIC.resolve()]
        for provider in ma('config/providers',{'provider_domain':'filesystem_local','include_values':True}):
            path=(provider.get('values') or {}).get('path')
            if isinstance(path,dict):path=path.get('value')
            if path and Path(path).resolve().is_relative_to('/media/local'):roots.append(Path(path).resolve())
        index=read('/media/local/cache/tidal-download/index.json',{});files={};items=[];skipped=0
        for i,item in enumerate(selection['items']):
            with LOCK:PLAN.update(checked=i,total=len(selection['items']))
            source=file_source(item,roots,index)
            if source is None:
                try:source=file_source(ma('music/item_by_uri',{'uri':item['uri'],'allow_update_metadata':False}),roots,index)
                except urllib.error.HTTPError as e:
                    if e.code in (400,404):skipped+=1;continue
                    raise
            if source is None:skipped+=1;continue
            st=source.stat()
            if not 0<st.st_size<4*1024**3:skipped+=1;continue # FAT32 file limit.
            identity=hmac.new(token.encode(),(str(source)+'|'+str(st.st_size)+'|'+str(st.st_mtime_ns)).encode(),hashlib.sha256).hexdigest()
            ident=key(item);relative=destination(item,source.suffix)
            public={'id':identity,'key':ident,'path':relative,'item':item,'size':st.st_size,'mtime':st.st_mtime_ns}
            files[identity]=dict(public,source=str(source),roots=roots,sha256=None)
            items.append(public)
        if not items:raise ValueError('No local recordings available')
        with LOCK:
            FILES=files;PLAN=dict(state='ready',created=time.time(),items=items,groups=selection.get('groups',{}),skipped=skipped,device_id=cfg.get('phone_device_id') or cfg.get('device_id',''),reserve_bytes=max(1024**3,int(cfg.get('reserve_bytes',1024**3))))
    except Exception:
        with LOCK:PLAN={'state':'error','message':'Selection unavailable; retry when BS5c and Music Assistant are connected'}

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def reply(self,status,data):
        data=json.dumps(data,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('Access-Control-Allow-Origin','*');self.end_headers();self.wfile.write(data)
    def token(self):return read(CONFIG,{}) .get('token','')
    def authorised(self):
        token=self.token();supplied=self.headers.get('Authorization','').removeprefix('Bearer ')
        return len(token)>=16 and lan(self.client_address[0]) and hmac.compare_digest(supplied,token)
    def do_GET(self):
        if self.path=='/pairing':
            origin=self.headers.get('Origin');host=self.headers.get('Host','').split(':')[0]
            if host not in ('localhost','127.0.0.1','beosound5c.local',self.connection.getsockname()[0]) or self.client_address[0] not in ('127.0.0.1','::1',self.connection.getsockname()[0]) or (origin and (urllib.parse.urlparse(origin).scheme!='http' or urllib.parse.urlparse(origin).hostname!=host)):
                self.reply(403,{});return
            self.reply(200,{'token':self.token()});return
        if not self.authorised():self.reply(401,{});return
        if self.path=='/identity':
            self.reply(200,{'device_id':(read(SYNC_CONFIG,{}).get('phone_device_id') or read(SYNC_CONFIG,{}).get('device_id',''))});return
        if self.path=='/plan':
            with LOCK:plan=dict(PLAN)
            self.reply(200,plan);return
        match=re.fullmatch(r'/(metadata|file)/([a-f0-9]{64})',self.path)
        if not match:self.reply(404,{});return
        with LOCK:record=FILES.get(match[2])
        if not record:self.reply(404,{'error':'Refresh selection'});return
        try:
            source=checked(record)
            if match[1]=='metadata':
                if not record['sha256']:
                    digest=hashlib.sha256()
                    with source.open('rb') as f:
                        for chunk in iter(lambda:f.read(256*1024),b''):digest.update(chunk)
                    checked(record);record['sha256']=digest.hexdigest()
                self.reply(200,dict(size=record['size'],sha256=record['sha256']));return
            if not record['sha256']:self.reply(409,{'error':'Read metadata first'});return
            with source.open('rb') as f:
                self.send_response(200);self.send_header('Content-Type','application/octet-stream');self.send_header('Content-Length',str(record['size']));self.send_header('Cache-Control','no-store');self.end_headers()
                for chunk in iter(lambda:f.read(256*1024),b''):self.wfile.write(chunk)
        except (ValueError,OSError):
            if match[1]=='metadata':self.reply(409,{'error':'Source changed; refresh selection'})
            else:self.close_connection=True
    def do_POST(self):
        if not self.authorised():self.reply(401,{});return
        if self.path=='/pair':
            try:
                self.connection.settimeout(5);length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=256:raise ValueError()
                payload=json.loads(self.rfile.read(length));ident=payload['device_id']
                if set(payload)!={'device_id'} or not re.fullmatch(r'uuid:[A-Fa-f0-9-]{4,64}',ident):raise ValueError()
                with LOCK:
                    cfg=read(SYNC_CONFIG,{})
                    current=cfg.get('phone_device_id') or cfg.get('device_id')
                    if current and current!=ident:self.reply(409,{'error':'Another iPod is paired'});return
                    if not current:
                        cfg['phone_device_id']=ident;atomic_json(SYNC_CONFIG,cfg)
                self.reply(200,{'device_id':ident});return
            except (ValueError,KeyError,TypeError):self.reply(400,{});return
        if self.path=='/plan':
            with LOCK:
                if PLAN['state']=='ready' and time.time()-PLAN.get('created',0)<3600:self.reply(200,{'state':'ready'});return
                if PLAN['state']!='selecting':
                    PLAN.clear();PLAN.update(state='selecting',checked=0,total=0)
                    threading.Thread(target=build_plan,args=(self.token(),),daemon=True).start()
            self.reply(202,{'state':'selecting'});return
        if self.path!='/history':self.reply(404,{});return
        try:
            self.connection.settimeout(10);length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=256*1024:raise ValueError()
            body=json.loads(self.rfile.read(length))
            events=body['events']
            if not isinstance(events,list) or len(events)>100:raise ValueError()
            result=local_call('/library/ipod/history',{'events':events})
            if result.get('accepted',0)>0:
                with LOCK:
                    if PLAN['state']=='ready':PLAN.clear();PLAN.update(state='idle')
            self.reply(200,result)
        except (ValueError,KeyError,TypeError):self.reply(400,{})
        except Exception:self.reply(503,{'error':'History not acknowledged; existing music preserved'})

if __name__=='__main__':
    if len(read(CONFIG,{}).get('token',''))<16:raise SystemExit('Configure private iPod phone token first')
    ThreadingHTTPServer(('0.0.0.0',8790),Handler).serve_forever()
