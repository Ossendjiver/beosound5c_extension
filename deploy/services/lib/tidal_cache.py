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

def select(tracks,evidence,track_limit=250,album_limit=20):
    eligible=[t for t in tracks if tidal_mapping(t)]
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
