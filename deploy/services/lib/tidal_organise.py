"""Non-overwriting moves from the Tidal staging folder into existing music folders."""
import ctypes,errno,hashlib,os,re,unicodedata
from pathlib import Path

def name(value):
    value=re.sub(r'[\x00-\x1f/\\:*?"<>|]','_',str(value)).strip().strip('.')
    return value[:150] or 'Unknown'

def normal(value):return ''.join(c for c in unicodedata.normalize('NFKC',str(value)).casefold() if c.isalnum())

def album_name(folder,artist):
    s=folder
    if normal(s).startswith(normal(artist)):
        s=re.sub('^'+re.escape(artist)+r'\s*[-_ ]*','',s,flags=re.I)
    s=re.split(r'\s*[\[(](?:19|20)\d{2}[\])]|\s*[\[(](?:FLAC|MP3|24[ .-]|16[ .-])|\s+FLAC\b',s,flags=re.I)[0]
    return normal(s)

def destination(root,record,ident):
    root=Path(root).resolve();item=record['item'];artists=item.get('artists') or []
    artist=str(item.get('artist') or next((a.get('name') for a in artists if isinstance(a,dict) and a.get('name')),''))
    album=item.get('album') or {};album=album.get('name','') if isinstance(album,dict) else str(album)
    if not artist or not album:raise ValueError('Missing artist/album metadata')
    # Exact filesystem mapping is stronger evidence than directory-name matching.
    mapped=[]
    for m in item.get('provider_mappings',[]):
        if m.get('provider_domain')!='filesystem_local':continue
        p=(root/str(m.get('item_id',''))).resolve()
        if p.is_relative_to(root) and p.is_file():mapped.append(p.parent)
    if len(set(mapped))==1:folder=mapped[0]
    else:
        folders=[p for p in root.iterdir() if p.is_dir() and not p.is_symlink() and normal(p.name)==normal(artist)]
        artist_folder=folders[0] if len(folders)==1 else root/name(artist)
        matches=[p for p in artist_folder.iterdir() if p.is_dir() and not p.is_symlink() and album_name(p.name,artist)==normal(album)] if artist_folder.exists() else []
        # Prefer the existing FLAC release when an album has multiple encodings.
        matches.sort(key=lambda p:('flac' not in p.name.casefold(),p.name.casefold()))
        folder=matches[0] if matches else artist_folder/name(album)
    track=int(item.get('track_number') or 0);disc=int(item.get('disc_number') or 1)
    prefix=(str(disc)+'-' if disc>1 else '')+(f'{track:02d}. ' if track else '')
    base=prefix+name(item.get('name') or item.get('title') or ident)
    path=folder/(base+'.flac')
    if path.exists():path=folder/(base+f" [Tidal {ident} {record.get('bit_depth',0)}bit {record.get('sample_rate',0)}Hz].flac")
    if not path.resolve().is_relative_to(root):raise ValueError('Unsafe destination')
    if path.exists():raise FileExistsError('Destination already exists')
    return path

def move_no_replace(source,target):
    # renameat2 is atomic and refuses overwriting even if a file appears mid-move.
    libc=ctypes.CDLL(None,use_errno=True)
    if not hasattr(libc,'renameat2'):raise OSError('Safe rename unavailable')
    if libc.renameat2(-100,os.fsencode(source),-100,os.fsencode(target),1):
        error=ctypes.get_errno();raise OSError(error,os.strerror(error))

def matches(path,record):
    p=Path(path)
    if not p.is_file() or p.stat().st_size!=record['size']:return False
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()==record['sha256']
