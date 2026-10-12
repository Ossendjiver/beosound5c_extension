"""Add-only Tidal offline cache policy. Never handles encryption keys."""
import base64,ipaddress,json,math,re,socket
from urllib.parse import urlparse

class ProtectedAudio(ValueError): pass

def direct_flac(info):
    if str(info.get('audioQuality','')).upper() not in ('LOSSLESS','HI_RES_LOSSLESS'):raise ValueError('Lossless asset unavailable')
    if info.get('manifestMimeType')!='application/vnd.tidal.bts':raise ProtectedAudio('Only unencrypted direct FLAC is supported')
    data=json.loads(base64.b64decode(info['manifest'],validate=True))
    if not isinstance(data.get('encryptionType'),str) or data['encryptionType'].upper() not in ('NONE','UNENCRYPTED'):raise ProtectedAudio('Encrypted or unspecified protection')
    if any(data.get(k) for k in ('keyId','keyIds','encryptionKey','securityToken','licenseUrl')):raise ProtectedAudio('Protected asset')
    if 'flac' not in str(data.get('codecs','')).lower() or data.get('mimeType') not in ('audio/flac','audio/x-flac'):raise ValueError('Not FLAC')
    urls=data.get('urls');url=urls[0] if isinstance(urls,list) and urls else ''
    validate_url(url,resolve=False)
    return url

def validate_url(url,resolve=True):
    p=urlparse(url);host=(p.hostname or '').lower()
    allowed=('tidal.com','tidalhifi.com','akamaized.net','akamaihd.net','cloudfront.net','fastly.net')
    if p.scheme!='https' or p.username or p.password or p.port not in (None,443) or not any(host==d or host.endswith('.'+d) for d in allowed):raise ValueError('Unexpected audio host')
    if resolve:
        addresses=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):raise ValueError('Non-public audio address')

def tidal_mapping(item):
    return next((m for m in item.get('provider_mappings',[]) if m.get('provider_domain')=='tidal' and m.get('available',True) and re.fullmatch(r'\d+',str(m.get('item_id','')))),None)

def finite(v):
    try:v=float(v);return v if math.isfinite(v) and v>0 else 0
    except (ValueError,TypeError):return 0

def score(item,evidence):
    aliases=[item.get('uri','')]+[f"{p}://track/{m.get('item_id')}" for m in item.get('provider_mappings',[]) for p in (m.get('provider_domain'),m.get('provider_instance')) if p]
    return max([finite(item.get('play_count'))]+[finite(evidence.get(a)) for a in aliases])+(.01 if item.get('favorite') else 0)

def select(tracks,evidence,track_limit=1000,album_limit=20):
    eligible=[t for t in tracks if tidal_mapping(t) and explicit(t)]
    eligible.sort(key=lambda t:(-score(t,evidence),str(t.get('name','')),str(t.get('uri',''))))
    chosen=[];seen=set();albums={}
    for t in eligible:
        m=tidal_mapping(t);tid=str(m['item_id'])
        if tid not in seen and len(chosen)<track_limit:chosen.append(t);seen.add(tid)
        a=t.get('album') or {}
        if isinstance(a,dict) and a.get('item_id') and a.get('provider'):
            ident=(str(a['provider']),str(a['item_id']))
            entry=albums.setdefault(ident,{'item':a,'score':0});entry['score']+=score(t,evidence)
    ordered=sorted(albums.values(),key=lambda a:(-a['score'],str(a['item'].get('name',''))))
    return chosen,[a['item'] for a in ordered[:album_limit]]


def cached_source(index,item):
    aliases={item.get('uri','')}
    mapping=tidal_mapping(item)
    if mapping:aliases.add('tidal://track/'+str(mapping['item_id']))
    for record in index.get('tracks',{}).values():
        if aliases.intersection(record.get('uris',[])):
            from pathlib import Path
            path=Path(record['path']).resolve()
            root=Path('/media/local/music/HomeMedia-Tidal').resolve()
            if path.is_relative_to(root) and path.is_file() and path.stat().st_size==record.get('size'):return path
    return None

def explicit(item):
    return (item.get('metadata') or {}).get('explicit') is True and not re.search(r'\b(clean|censored|non[- ]explicit)\b',str(item.get('version','')),re.I)

def maximum_asset(info):
    """Resolve only unprotected lossless assets; download segments ourselves."""
    import xml.etree.ElementTree as ET
    if info.get('manifestMimeType')!='application/dash+xml':
        return {'kind':'direct','urls':[direct_flac(info)],'bits':int(info.get('bitDepth') or 16),'sample_rate':int(info.get('sampleRate') or 44100)}
    if str(info.get('audioQuality','')).upper() not in ('LOSSLESS','HI_RES_LOSSLESS'):raise ValueError('Lossless unavailable')
    raw=base64.b64decode(info['manifest'],validate=True)
    if len(raw)>1024**2 or b'<!doctype' in raw.lower() or b'<!entity' in raw.lower():raise ValueError('Unsafe manifest')
    root=ET.fromstring(raw)
    tag=lambda n:n.tag.split('}')[-1]
    if any(tag(n)=='ContentProtection' for n in root.iter()):raise ProtectedAudio('Protected DASH')
    if root.get('type','static')!='static':raise ValueError('Dynamic manifest unsupported')
    reps=[n for n in root.iter() if tag(n)=='Representation' and 'flac' in n.get('codecs','').lower()]
    if not reps:raise ValueError('No FLAC representation')
    rep=max(reps,key=lambda n:(int(n.get('audioSamplingRate','0')),int(n.get('bandwidth','0'))))
    parents={c:p for p in root.iter() for c in p};node=rep;template=None
    while node is not None:
        template=next((n for n in node if tag(n)=='SegmentTemplate'),None)
        if template is not None:break
        node=parents.get(node)
    if template is None:raise ValueError('Unsupported DASH segment layout')
    timeline=next((n for n in template if tag(n)=='SegmentTimeline'),None)
    if timeline is None:raise ValueError('Missing finite DASH timeline')
    scale=int(template.get('timescale','1'));number=int(template.get('startNumber','1'));elapsed=0
    if scale<=0 or number<0:raise ValueError('Invalid segment time')
    duration=re.fullmatch(r'PT(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?',root.get('mediaPresentationDuration',''))
    end=scale*sum(float(v or 0)*factor for v,factor in zip(duration.groups(),(3600,60,1))) if duration else None
    def expand(value,number,elapsed):
        if not value:raise ValueError('Missing segment URL')
        value=value.replace('$RepresentationID$',rep.get('id','')).replace('$Bandwidth$',rep.get('bandwidth',''))
        value=re.sub(r'\$(Number|Time)(?:%0(\d+)d)?\$',lambda m:format(number if m[1]=='Number' else elapsed,'0'+str(int(m[2]))+'d') if m[2] else str(number if m[1]=='Number' else elapsed),value)
        if '$' in value:raise ValueError('Unsupported URL template')
        validate_url(value,resolve=False);return value
    urls=[expand(template.get('initialization'),number,elapsed)]
    for seg in timeline:
        if tag(seg)!='S':raise ValueError('Unexpected segment timeline')
        d=int(seg.get('d','0'));repeat=int(seg.get('r','0'));elapsed=int(seg.get('t',elapsed))
        if d<=0 or repeat < -1:raise ValueError('Invalid segment repeat')
        if repeat==-1:
            if not end or end<=elapsed:raise ValueError('Unbounded timeline')
            repeat=math.ceil((end-elapsed)/d)-1
        if len(urls)+repeat+1>20000:raise ValueError('Too many segments')
        for _ in range(repeat+1):
            urls.append(expand(template.get('media'),number,elapsed));number+=1;elapsed+=d
    if len(urls)<2:raise ValueError('Empty manifest')
    return {'kind':'dash','urls':urls,'bits':int(info.get('bitDepth') or 24),'sample_rate':int(rep.get('audioSamplingRate') or info.get('sampleRate') or 0)}
