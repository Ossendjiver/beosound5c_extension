"""Rockbox USB sync primitives. Never edit the iTunes database or unmanaged files."""
from __future__ import annotations
import hashlib,json,os,re,shutil,unicodedata
from pathlib import Path

def atomic_json(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp')
    with temp.open('w') as f:
        json.dump(value,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
    os.replace(temp,path)
    fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)

def safe_path(root, relative):
    root=Path(root).resolve();path=(root/str(relative).lstrip('/')).resolve()
    if not path.is_relative_to(root) or path==root:raise ValueError('Path outside iPod')
    return path

def slug(text):
    text=unicodedata.normalize('NFC',str(text));text=re.sub(r'[\x00-\x1f<>:"/\\|?*]','_',text).strip(' .')
    return text[:100] or 'Unknown'

def key(item):
    return hashlib.sha256(str(item['uri']).encode()).hexdigest()[:20]

def destination(item, suffix):
    return 'HomeMedia/Music/'+slug(item.get('artist') or 'Unknown')+'/'+slug(item.get('title') or item.get('name'))+'-'+key(item)+suffix.lower()

def history(mount, manifest, identity):
    """Read modern Rockbox timestamp:elapsed_ms:length_ms:path logs, including rotated logs.
    Manifest identity avoids fuzzy matching and preserves cross-provider library URI.
    Original logs are retained. Legacy exports are only used when no modern log exists.
    """
    mount=Path(mount);by_path={str(v['path']):v['item'] for v in manifest.values()}
    logs=sorted((mount/'.rockbox').glob('playback*.log'));events={};unmatched=0
    for log in logs:
        for line in log.read_text(errors='replace').splitlines():
            if not line.strip() or line[0] in '#!@':continue
            try:
                stamp,elapsed,length,path=line.strip().split(':',3)
                stamp=int(stamp);elapsed=int(elapsed)/1000;length=int(length)/1000
                relative=str(safe_path(mount,path).relative_to(mount))
                if stamp<=0 or length<=0 or not 0<=elapsed<=length:continue
            except (ValueError,TypeError):continue
            item=by_path.get(relative)
            if not item:unmatched+=1;continue
            # Resumes/rotated logs with the same recording/start merge to their greatest elapsed time.
            event_id='ipod_'+hashlib.sha256((identity+'|'+item['uri']+'|'+str(stamp)).encode()).hexdigest()
            event=dict(item,event_id=event_id,timestamp_ms=stamp*1000,seconds=elapsed,duration=length,
                       reason='complete' if elapsed>=length*.5 else 'skip',room='ipod',media_type='track')
            if elapsed>=.5 and elapsed>events.get(event_id,{}).get('seconds',0):events[event_id]=event
    if not logs:
        # Legacy scrobbler exports use title + artist; reject ambiguous recordings.
        def norm(s):return re.sub(r'\W+',' ',str(s).casefold()).strip()
        for log in [mount/'.scrobbler.log']:
            if not log.exists():continue
            for line in log.read_text(errors='replace').splitlines():
                if line.startswith('#'):continue
                parts=line.split('\t')
                if len(parts)<7:continue
                matches=[v['item'] for v in manifest.values() if norm(v['item'].get('artist'))==norm(parts[0]) and norm(v['item'].get('title'))==norm(parts[2])]
                if len(matches)!=1:unmatched+=1;continue
                try:duration=float(parts[4]);stamp=int(parts[6]);assert stamp>0 and duration>0 and parts[5] in ('L','S')
                except (ValueError,AssertionError):continue
                item=matches[0];event_id='ipod_'+hashlib.sha256((identity+'|'+item['uri']+'|'+str(stamp)).encode()).hexdigest()
                events[event_id]=dict(item,event_id=event_id,timestamp_ms=stamp*1000,seconds=duration if parts[5]=='L' else 0,
                    duration=duration,reason='complete' if parts[5]=='L' else 'skip',room='ipod',media_type='track')
    return sorted(events.values(),key=lambda e:e['timestamp_ms']),unmatched

def copy_track(source, mount, relative, reserve):
    source=Path(source);dest=safe_path(mount,relative)
    if not source.is_file():raise FileNotFoundError(source)
    if source.stat().st_size+reserve>shutil.disk_usage(mount).free:return False
    dest.parent.mkdir(parents=True,exist_ok=True);temp=dest.with_name(dest.name+'.part')
    try:
        with source.open('rb') as src,temp.open('wb') as out:
            shutil.copyfileobj(src,out,1024*1024);out.flush();os.fsync(out.fileno())
        if temp.stat().st_size!=source.stat().st_size:raise IOError('Incomplete copy')
        os.replace(temp,dest)
    finally:
        if temp.exists():temp.unlink()
    return True

def prune(mount, old, desired):
    """Only remove files recorded in our previous manifest, after history was acknowledged."""
    for ident,record in old.items():
        if ident in desired:continue
        relative=record.get('path','')
        if not relative.startswith('HomeMedia/Music/'):raise ValueError('Refuse unmanaged deletion')
        path=safe_path(mount,relative)
        if path.is_file():path.unlink()
