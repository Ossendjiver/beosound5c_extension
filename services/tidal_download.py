#!/usr/bin/env python3
"""Personal Tidal cache: idle-only, authenticated unencrypted FLAC, add-only SSD."""
import fcntl,hashlib,json,os,shutil,subprocess,time,urllib.request,urllib.error,urllib.parse
from pathlib import Path
from lib.ipod_sync import atomic_json
from lib.tidal_rate_limit import RateLimit,Cooldown
from lib.library_tag_policy import provider_album_artist,classical_composers
from lib.tidal_cache import direct_flac,tidal_mapping,select,validate_url,ProtectedAudio,maximum_asset,explicit
ROOT=Path('/media/local/music/HomeMedia-Tidal')
CACHE=Path('/media/local/cache/tidal-download')
STATUS=Path('/home/thomas/beosound5c/web/json/tidal_download_status.json')
CONFIG=Path('/etc/beosound5c/tidal-download.json')
RATE=RateLimit(CACHE/'rate_limit.json')

class Busy(Exception): pass
class AuthNeeded(Exception): pass
class DiskFull(Exception): pass
class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        validate_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def read(path,default):
    try:return json.loads(path.read_text())
    except FileNotFoundError:return default

def publish(state,**values):
    old=read(STATUS,{})
    atomic_json(STATUS,dict(old,state=state,updated=time.time(),**values));STATUS.chmod(0o644)

def request(url,body=None,token=None):
    tidal=urllib.parse.urlsplit(url).hostname=='api.tidal.com'
    if tidal:RATE.before_request()
    headers={'Content-Type':'application/json'}
    if token:headers['Authorization']='Bearer '+token
    r=urllib.request.Request(url,data=None if body is None else json.dumps(body).encode(),headers=headers)
    # No redirected requests containing account credentials.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args):return None
    try:
        with urllib.request.build_opener(NoRedirect()).open(r,timeout=30) as response:return json.load(response)
    except urllib.error.HTTPError as e:
        if e.code==429 and tidal:RATE.limited(e.headers)
        if e.code==401:raise AuthNeeded() from None
        raise

def command(name,args):
    base=os.environ['MASS_WS_URL'].replace('ws://','http://').replace('wss://','https://').removesuffix('/ws')
    return request(base+'/api',{'command':name,'args':args},os.environ['MASS_TOKEN'])

def idle():
    players=command('players/all',{})
    return isinstance(players,list) and bool(players) and all(not p.get('available',True) or p.get('playback_state',p.get('state')) in ('idle','off','stopped') for p in players)

def require_idle():
    if not idle():raise Busy()

def auth():
    # MA's settings encryption protects stored credentials, not media DRM. No new
    # refresh-token owner: MA retains sole responsibility for renewing its login.
    code='''import json
from pathlib import Path
from cryptography.fernet import Fernet
from music_assistant.constants import ENCRYPT_SUFFIX
x=json.loads(Path('/data/settings.json').read_text());f=Fernet(x['encryption_key'].encode())
for p in x['providers'].values():
 if p.get('domain')=='tidal' and p.get('enabled',True):
  d=dict(p.get('setup_data',{}))
  for k,v in d.items():
   if isinstance(v,str) and ENCRYPT_SUFFIX in v:d[k]=f.decrypt(v.replace(ENCRYPT_SUFFIX,'').encode()).decode()
  raw=d.get('auth_token','')
  try:raw=json.loads(raw)
  except (ValueError,TypeError):pass
  if isinstance(raw,dict):d.update(raw)
  print(json.dumps({'token':d.get('access_token') or (raw if isinstance(raw,str) else None),'country':d.get('country_code') or d.get('countryCode')}));break
'''
    p=subprocess.run(['docker','exec','music-assistant-server','python','-c',code],capture_output=True,text=True,timeout=30)
    if p.returncode or not p.stdout.strip():raise AuthNeeded()
    data=json.loads(p.stdout)
    if not data.get('token'):raise AuthNeeded()
    if not data.get('country'):
        data['country']=request('https://api.tidal.com/v1/sessions',token=data['token']).get('countryCode')
    if not data.get('country'):raise AuthNeeded()
    return data

def all_tracks():
    out=[]
    for offset in range(0,100000,500):
        require_idle()
        page=command('music/tracks/library_items',{'limit':500,'offset':offset,'order_by':'play_count_desc'})
        if not isinstance(page,list):raise ValueError('Invalid MA track response')
        out.extend(page)
        if len(page)<500:return out
    raise ValueError('Library pagination exceeded safety limit')

def with_album_artist(item,details,credentials):
    """Prefer Tidal's explicit primary album artist over unordered MA credits."""
    album_id=str((details.get('album') or {}).get('id') or '')
    if not album_id:raise ValueError('Missing Tidal album identity')
    cache=read(CACHE/'album-artists.json',{})
    name=cache.get(album_id)
    if not name:
        album=request('https://api.tidal.com/v1/albums/'+album_id+'?countryCode='+credentials['country'],token=credentials['token'])
        name=provider_album_artist(album)
        if not name:raise ValueError('Missing primary album artist')
        cache[album_id]=name;atomic_json(CACHE/'album-artists.json',cache)
    composers=classical_composers({'artist':[a.get('name','') for a in item.get('artists',[]) if isinstance(a,dict)],'albumartist':[name],'genre':list((item.get('metadata') or {}).get('genres') or [])})
    if len(composers)==1:
        return dict(item,album_artist=composers[0],album_artist_source='classical_composer',composer=composers[0],performer=name if name!=composers[0] else '')
    return dict(item,album_artist=name,album_artist_source='tidal_primary_album_artist')

def transfer(asset,dest,reserve,item):
    temp=dest.with_suffix('.part');tagged=dest.with_suffix('.tagged.part')
    try:
        require_idle();size=0;last_check=0
        with temp.open('wb') as f:
            for url in asset['urls']:
                validate_url(url)
                with urllib.request.build_opener(SafeRedirect()).open(url,timeout=15) as response:
                    length=int(response.headers.get('Content-Length') or 0);segment_bytes=0
                    if length>1024**3:raise ValueError('Asset too large')
                    while True:
                        if time.monotonic()-last_check>3:require_idle();last_check=time.monotonic()
                        chunk=response.read(256*1024)
                        if not chunk:break
                        if size==0 and asset['kind']=='direct' and chunk[:4]!=b'fLaC':raise ValueError('Not a plain FLAC')
                        if size+len(chunk)>1024**3:raise ValueError('Asset too large')
                        if shutil.disk_usage(ROOT).free-len(chunk)<reserve+size+len(chunk):raise DiskFull()
                        f.write(chunk);size+=len(chunk);segment_bytes+=len(chunk)
                    if not segment_bytes or (length and segment_bytes!=length):raise ValueError('Incomplete segment')
            f.flush();os.fsync(f.fileno())
        probe=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration:stream=codec_name,sample_rate,bits_per_raw_sample','-of','json',str(temp)],capture_output=True,text=True,timeout=30)
        if probe.returncode:raise ValueError('Invalid audio')
        metadata=json.loads(probe.stdout);audio=metadata['streams'][0]
        rate=int(audio['sample_rate']);bits=int(audio.get('bits_per_raw_sample') or 0)
        if audio['codec_name']!='flac' or float(metadata['format']['duration'])<=0 or rate<asset['sample_rate'] or bits<asset['bits']:raise ValueError('Lossless quality mismatch')
        require_idle()
        check=subprocess.run(['ffmpeg','-nostdin','-v','error','-threads','1','-i',str(temp),'-f','null','-'],capture_output=True,timeout=180)
        if check.returncode:raise ValueError('FLAC decode failed')
        require_idle();album=item.get('album') or {}
        artist=', '.join(a.get('name','') for a in item.get('artists',[]) if isinstance(a,dict))
        artists='; '.join(a.get('name','') for a in item.get('artists',[]) if isinstance(a,dict) and a.get('name'))
        tags={'title':item.get('name',''),'artist':artist,'artists':artists,'album_artist':item.get('album_artist',''),'composer':item.get('composer',''),'performer':item.get('performer',''),'album':album.get('name','') if isinstance(album,dict) else str(album),'track':str(item.get('track_number') or ''),'disc':str(item.get('disc_number') or '')}
        args=['ffmpeg','-nostdin','-v','error','-threads','1','-i',str(temp),'-map','0:a:0','-c:a','copy']
        for name,value in tags.items():
            if value:args+=['-metadata',name+'='+value]
        subprocess.run(args+['-f','flac',str(tagged)],capture_output=True,check=True,timeout=60)
        with tagged.open('rb') as f:
            os.fsync(f.fileno());digest=hashlib.sha256()
            for chunk in iter(lambda:f.read(1024*1024),b''):digest.update(chunk)
        require_idle()
        if dest.exists():raise ValueError('Refusing overwrite')
        size=tagged.stat().st_size;os.replace(tagged,dest)
        return {'path':str(dest),'size':size,'sha256':digest.hexdigest(),'sample_rate':rate,'bit_depth':bits,'quality_policy':2}
    finally:
        for unfinished in (temp,tagged):
            if unfinished.exists():unfinished.unlink()

def cached_record_valid(record):
    if not record or record.get('quality_policy')!=2:return False
    try:
        path=Path(record['path'])
        return path.is_file() and path.stat().st_size==record.get('size')
    except (KeyError,OSError):return False

def main():
    if subprocess.check_output(['findmnt','-n','-o','TARGET','/media/local'],text=True).strip()!='/media/local':raise ValueError('SSD missing')
    config=read(CONFIG,{'enabled':True,'tracks':250,'albums':20,'reserve_bytes':10*1024**3})
    settings=read(CACHE/'settings.json',{})
    count=settings.get('tracks')
    if type(count) is int and 50<=count<=10000 and count%50==0:config['tracks']=count
    if not config.get('enabled'):publish('disabled');return
    CACHE.mkdir(parents=True,exist_ok=True);ROOT.mkdir(parents=True,exist_ok=True)
    with (CACHE/'worker.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        state=read(CACHE/'index.json',{'version':1,'tracks':{}})
        selection_key=[int(config.get('tracks',250)),int(config.get('albums',20))]
        if state.get('quality_policy')==2 and state.get('selection_key')==selection_key and time.time()-state.get('last_completed',0)<86400:return
        RATE.ready()
        require_idle();publish('selecting',retry_at=None,message='Ranking personal listening history')
        evidence=request('http://127.0.0.1:8788/library/tidal-download/ranking')
        tracks,albums=select(all_tracks(),evidence,int(config.get('tracks',250)),int(config.get('albums',20)))
        selected=list(tracks)
        for album in albums:
            require_idle()
            rows=command('music/albums/album_tracks',{'item_id':album['item_id'],'provider_instance_id_or_domain':album['provider']})
            if not isinstance(rows,list):raise ValueError('Invalid album response')
            selected.extend(t for t in rows if tidal_mapping(t) and explicit(t))
        plan={str(tidal_mapping(t)['item_id']):t for t in selected}
        atomic_json(CACHE/'selection.json',{'updated':time.time(),'tracks':len(tracks),'albums':albums,'items':list(plan)})
        plan_key={'selection':selection_key,'ids':sorted(plan)}
        if state.get('pending_plan')!=plan_key or not state.get('pending_ids'):
            state['pending_plan']=plan_key
            state['pending_ids']=sorted(plan,key=lambda tid:cached_record_valid(state['tracks'].get(tid)))
            atomic_json(CACHE/'index.json',state)
        counters={'downloaded':0,'retained':0,'protected':0,'errors':0,'non_explicit':0};credentials=auth() if plan else None
        started=time.monotonic()
        for tid in list(state['pending_ids']):
            item=plan[tid]
            completed=False
            if time.monotonic()-started>4*3600:publish('deferred',message='Run limit reached; will resume',**counters);return
            require_idle();old=state['tracks'].get(tid)
            publish('downloading',total=len(plan),**counters,message='Checking maximum quality for '+str(item.get('name','track')))
            try:
                if cached_record_valid(old) and time.time()-old.get('quality_checked_at',old.get('downloaded_at',0))<86400:
                    old['uris']=sorted(set(old.get('uris',[]))|{item['uri']})
                    counters['retained']+=1;completed=True;continue
                details=request('https://api.tidal.com/v1/tracks/'+tid+'?countryCode='+credentials['country'],token=credentials['token'])
                if details.get('explicit') is not True:
                    counters['non_explicit']+=1;completed=True;continue
                item=with_album_artist(item,details,credentials)
                params={'playbackmode':'STREAM','assetpresentation':'FULL','audioquality':'HI_RES_LOSSLESS','immersiveaudio':'false','countryCode':credentials['country']}
                info=request('https://api.tidal.com/v1/tracks/'+tid+'/playbackinfopostpaywall?'+urllib.parse.urlencode(params),token=credentials['token'])
                asset=maximum_asset(info)
                if old and old.get('quality_policy')==2 and old.get('sample_rate',0)>=asset['sample_rate'] and old.get('bit_depth',0)>=asset['bits'] and Path(old['path']).is_file() and Path(old['path']).stat().st_size==old.get('size'):
                    aliases=set(old.get('uris',[]));aliases.add(item['uri']);old['uris']=sorted(aliases);counters['retained']+=1;old['quality_checked_at']=time.time();completed=True;continue
                dest=ROOT/(tid+'.'+str(asset['bits'])+'bit-'+str(asset['sample_rate'])+'.flac')
                if dest.exists():dest=ROOT/(tid+'.'+str(time.time_ns())+'.flac')
                record=transfer(asset,dest,int(config.get('reserve_bytes',10*1024**3)),item)
                if old:record['previous']=old.get('previous',[])+[{k:v for k,v in old.items() if k!='previous'}]
                state['tracks'][tid]=dict(record,item=item,uris=[item['uri'],f'tidal://track/{tid}'],downloaded_at=time.time(),quality_checked_at=time.time());counters['downloaded']+=1
                completed=True
            except ProtectedAudio:counters['protected']+=1;completed=True
            except (Busy,AuthNeeded,DiskFull,Cooldown):raise
            except urllib.error.HTTPError as e:
                if e.code==429:RATE.limited(e.headers)
                counters['errors']+=1;completed=True
            except (ValueError,OSError,subprocess.SubprocessError):counters['errors']+=1;completed=True
            finally:
                if completed:
                    state['pending_ids'].remove(tid)
                    atomic_json(CACHE/'index.json',state)
                publish('downloading',total=len(plan),remaining=len(state['pending_ids']),cached=len(state['tracks']),**counters)
            time.sleep(.5)
        RATE.completed()
        state['last_completed']=time.time();state['quality_policy']=2;state['selection_key']=selection_key;atomic_json(CACHE/'index.json',state)
        publish('complete',last_completed=state['last_completed'],cached=len(state['tracks']),total=len(plan),**counters,message='Add-only cache updated; next refresh in 24 hours')

if __name__=='__main__':
    try:main()
    except Cooldown as e:publish('rate_limited',retry_at=e.retry_at,message='Tidal cooldown; automatic retry after '+time.strftime('%H:%M',time.localtime(e.retry_at)))
    except Busy:publish('waiting_for_idle',message='Waiting until all available players are idle')
    except AuthNeeded:publish('waiting_for_login',message='Waiting for Music Assistant to renew Tidal login')
    except DiskFull:publish('low_space',message='SSD free-space reserve reached; cached files retained')
    except Exception as e:publish('error',message='Download check failed ('+type(e).__name__+'); retry next check')
