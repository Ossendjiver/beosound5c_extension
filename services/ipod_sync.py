#!/usr/bin/env python3
"""Automatic Rockbox iPod Video docking sync. No speaker/queue/player commands."""
from __future__ import annotations
import fcntl,json,os,subprocess,time,urllib.request,urllib.parse,urllib.error
from pathlib import Path
from lib.ipod_sync import atomic_json,history,key,destination,copy_track,prune,safe_path
CACHE=Path(os.getenv('BS5C_IPOD_CACHE','/media/local/cache/ipod-sync'))
STATUS=Path(os.getenv('BS5C_IPOD_STATUS','/home/thomas/beosound5c/web/json/ipod_sync_status.json'))
CONFIG=Path(os.getenv('BS5C_IPOD_CONFIG','/etc/beosound5c/ipod-sync.json'))

def load(path,default):
    try:return json.loads(Path(path).read_text())
    except FileNotFoundError:return default

def run(args):
    p=subprocess.run(args,capture_output=True,text=True,timeout=60)
    if p.returncode:raise RuntimeError(args[0]+' failed')
    return p.stdout

def request(url,body=None,token=None):
    req=urllib.request.Request(url,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json',**({'Authorization':'Bearer '+token} if token else {})})
    with urllib.request.urlopen(req,timeout=90) as r:return json.load(r)

def publish(state,**fields):
    old=load(STATUS,{})
    atomic_json(STATUS,dict(old,state=state,updated=time.time(),**fields))
    STATUS.chmod(0o644)

def devices():
    disks=json.loads(run(['lsblk','-J','-p','-o','NAME,TYPE,TRAN,MODEL,SERIAL,FSTYPE,UUID,MOUNTPOINTS']))['blockdevices']
    found=[]
    for disk in disks:
        if disk.get('tran')!='usb' or 'ipod' not in str(disk.get('model','')).lower():continue
        props=dict(l.split('=',1) for l in run(['udevadm','info','--query=property','--name',disk['name']]).splitlines() if '=' in l)
        if props.get('ID_VENDOR_ID')!='05ac':continue
        for part in disk.get('children',[]):
            if part.get('fstype')!='vfat':continue
            ident=str(disk.get('serial') or part.get('uuid') or '')
            if ident:found.append((disk,part,ident))
    return found

def require_mount(mount, part):
    actual=run(['findmnt','-n','-o','SOURCE','--mountpoint',str(mount)]).strip()
    if actual!=part['name']:raise RuntimeError('iPod disconnected or mount changed; sync stopped')

def sync(disk,part,ident,config):
    # Read-only target verification first; never mount an unrelated USB disk.
    mounted=[p for p in part.get('mountpoints',[]) if p]
    if not mounted:
        run(['udisksctl','mount','-b',part['name'],'--no-user-interaction'])
        mounted=[run(['findmnt','-n','-o','TARGET','--source',part['name']]).strip()]
    mount=Path(mounted[0]);info=mount/'.rockbox/rockbox-info.txt'
    if not info.is_file() or 'Target: ipodvideo' not in info.read_text(errors='replace'):
        publish('waiting_for_rockbox',device='iPod Video',message='Install Rockbox for iPod Video before syncing');return
    require_mount(mount,part)
    if not config.get('device_id'):
        config['device_id']=ident;atomic_json(CONFIG,config)
    statepath=CACHE/(key({'uri':ident})+'.json');state=load(statepath,{'manifest':{},'receipts':[]})
    if not state['manifest']:
        state['manifest']=load(safe_path(mount,'HomeMedia/manifest.json'),{})
    old=state['manifest'];receipts=set(state.get('receipts',[]));events,unmatched=history(mount,old,ident)
    publish('importing',device='iPod Video',device_id=ident,unmatched=unmatched,message='Importing listening history')
    # Every batch is durably acknowledged before any music is removed or replaced.
    pending=[e for e in events if e['event_id'] not in receipts]
    for offset in range(0,len(pending),100):
        batch=pending[offset:offset+100]
        result=request('http://127.0.0.1:8788/library/ipod/history',{'events':batch})
        if not result.get('ok'):raise RuntimeError('Listening history not acknowledged')
        receipts.update(e['event_id'] for e in batch)
        state.update(receipts=sorted(receipts),imported_total=len(receipts));atomic_json(statepath,state)
    publish('selecting',imported=len(pending),imported_total=len(receipts),message='Selecting favourites, playlists and predicted radio tracks')
    plan=request('http://127.0.0.1:8788/library/ipod/catalogue?'+urllib.parse.urlencode({'limit':config.get('max_tracks',10000),'playlists':json.dumps(config.get('playlists',[]))}))
    base=os.environ['MASS_WS_URL'].replace('ws://','http://').replace('wss://','https://').removesuffix('/ws')
    token=os.environ['MASS_TOKEN'];selected={};skipped=0;budget=max(0,__import__('shutil').disk_usage(mount).free-int(config.get('reserve_bytes',1024**3)))
    budget+=sum(safe_path(mount,v['path']).stat().st_size for v in old.values() if safe_path(mount,v['path']).is_file())
    allowed=[Path(p).resolve() for p in config.get('local_roots',['/media/local'])]
    providers=request(base+'/api',{'command':'config/providers','args':{'provider_domain':'filesystem_local','include_values':True}},token)
    for provider in providers:
        path=(provider.get('values') or {}).get('path')
        if isinstance(path,dict):path=path.get('value')
        if path and Path(path).resolve().is_relative_to('/media'):
            allowed.append(Path(path).resolve())
    tidal_cache=load(Path("/media/local/cache/tidal-download/index.json"),{})
    # Resolve local files and exact identities in the authenticated offline cache.
    for item in plan['items']:
        if budget<=0:break
        try:
            full=request(base+'/api',{'command':'music/item_by_uri','args':{'uri':item['uri'],'allow_update_metadata':False}},token)
            source=None
            for mapping in full.get('provider_mappings',[]):
                if mapping.get('provider_domain')!='filesystem_local' or not mapping.get('available',True):continue
                relative=mapping['item_id']
                for root in allowed:
                    candidate=(root/relative).resolve()
                    if candidate.is_relative_to(root) and candidate.is_file():source=candidate;break
                if source:break
            if not source:
                source=__import__('lib.tidal_cache',fromlist=['cached_source']).cached_source(tidal_cache,full)
            if not source or source.suffix.lower() not in ('.flac','.mp3','.m4a','.aac','.ogg','.opus','.wav','.aiff','.wma'):
                skipped+=1;continue
            size=source.stat().st_size
            if size>budget:continue
            identkey=key(item);relative=destination(item,source.suffix)
            selected[identkey]={'path':relative,'item':item,'source':str(source),'size':size,'mtime':source.stat().st_mtime_ns};budget-=size
        except urllib.error.HTTPError as e:
            if e.code in (400,404):skipped+=1;continue
            raise
    if not selected:raise RuntimeError('No locally available selected music; existing iPod music preserved')
    require_mount(mount,part)
    prune(mount,old,selected)
    manifest={k:v for k,v in old.items() if k in selected}
    for i,(identkey,record) in enumerate(selected.items()):
        require_mount(mount,part)
        if i%25==0:publish('copying',copied=i,total=len(selected),provider_only_skipped=skipped,message='Updating music')
        dest=safe_path(mount,record['path']);prior=old.get(identkey,{})
        if not(dest.is_file() and dest.stat().st_size==record['size'] and prior.get('mtime')==record['mtime']):
            if not copy_track(record['source'],mount,record['path'],int(config.get('reserve_bytes',1024**3))):raise RuntimeError('iPod free-space reserve reached')
        manifest[identkey]={k:v for k,v in record.items() if k!='source'}
        if (i+1)%25==0:
            state.update(manifest=manifest,receipts=sorted(receipts));atomic_json(statepath,state)
            atomic_json(safe_path(mount,'HomeMedia/manifest.json'),manifest)
    state['manifest']={k:manifest[k] for k in selected};state['ejected_at']=None;atomic_json(statepath,state)
    # Save a device manifest so importing after reinstall also uses exact recording identities.
    require_mount(mount,part)
    atomic_json(safe_path(mount,'HomeMedia/manifest.json'),state['manifest'])
    playlists=plan.get('groups',{})
    playlists['Offline selection']=[v['item']['uri'] for v in selected.values()]
    by_uri={v['item']['uri']:v['path'] for v in selected.values()}
    for name,uris in playlists.items():
        require_mount(mount,part)
        playlist=safe_path(mount,'HomeMedia/'+__import__('lib.ipod_sync',fromlist=['slug']).slug(name)+'.m3u8')
        text='#EXTM3U\n'+'\n'.join('/'+by_uri[u] for u in uris if u in by_uri)+'\n'
        with playlist.open('w') as f:f.write(text);f.flush();os.fsync(f.fileno())
    publish('ejecting',message='Flushing and safely ejecting')
    require_mount(mount,part)
    os.sync();run(['udisksctl','unmount','-b',part['name'],'--no-user-interaction']);run(['udisksctl','power-off','-b',disk['name'],'--no-user-interaction'])
    state['ejected_at']=time.time();atomic_json(statepath,state)
    publish('complete',copied=len(selected),total=len(selected),last_sync=time.time(),message='Sync complete — safe to undock')

def main():
    CACHE.mkdir(parents=True,exist_ok=True)
    config=load(CONFIG,{'enabled':True,'playlists':[],'local_roots':['/media/local'],'max_tracks':10000,'reserve_bytes':1024**3})
    with open('/run/beo-ipod-sync.lock','w') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        if not config.get('enabled',True):publish('disabled',message='Automatic sync disabled');return
        found=devices()
        if not found:
            if load(STATUS,{}).get('state') not in ('waiting','complete','error'):publish('waiting',message='Waiting for Rockbox iPod Video')
            return
        if len(found)!=1:publish('error',message='Connect one iPod at a time');return
        disk,part,ident=found[0]
        prior_status=load(STATUS,{})
        if prior_status.get('state')=='error' and time.time()-prior_status.get('updated',0)<300:return
        paired=config.get('device_id')
        if paired and paired!=ident:publish('error',message='Another iPod detected; device pairing needs updating');return
        try:sync(disk,part,ident,config)
        except Exception as e:
            # Never eject after an incomplete history/copy transaction; retry on next timer tick.
            publish('error',message=str(e)[:150]);raise
if __name__=='__main__':main()
