#!/usr/bin/env python3
"""Metadata-first provider profiling. No queues, player commands or MA DB edits."""
import argparse
import fcntl
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'services'))
from lib import music_features, music_mood, provider_profiles as profiles
from analyze_music_audio import atomic_write
from profile_music_library import api_client, catalogue

AGENT = 'BS5cProviderProfiler/1.0 (https://github.com/Ossendjiver/beosound5c_extension)'
MAX_JSON = 2*1024*1024
MAX_AUDIO = 8*1024*1024


class MetadataRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if origin(newurl) != origin(req.full_url):
            raise ValueError('Cross-origin metadata redirect refused')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Online:
    """Host-restricted, rate-limited requests and cached negative results."""
    def __init__(self, cache, token='', clock=time.monotonic, sleep=time.sleep):
        self.cache, self.token, self.clock, self.sleep = cache, token, clock, sleep
        self.last = {}; self.blocked = {}
        self.sizes = {u:len(json.dumps(v).encode()) for u,v in cache.items()}
        self.cache_size = sum(self.sizes.values())
    def store(self,url,value):
        size=len(json.dumps(value).encode())
        self.cache_size += size-self.sizes.get(url,0)
        self.cache[url]=value;self.sizes[url]=size
        # Bound the resident request cache; durable per-track profiles remain intact.
        while self.cache_size > 48*1024*1024 and len(self.cache)>1:
            oldest=next(iter(self.cache))
            self.cache.pop(oldest);self.cache_size-=self.sizes.pop(oldest)
    def get(self, url):
        host = urllib.parse.urlsplit(url).hostname
        if urllib.parse.urlsplit(url).scheme != 'https' or host not in ('musicbrainz.org', 'acousticbrainz.org', 'api.discogs.com'):
            raise ValueError('Unsupported metadata host')
        now = time.time()
        if self.blocked.get(host, 0) > now:raise RuntimeError('Metadata host temporarily unavailable')
        cached = self.cache.get(url, {})
        if cached.get('expires', 0) > now: return cached.get('data')
        # >=1 second between starts at MusicBrainz; conservative on all services.
        remaining = 1.1-(self.clock()-self.last.get(host, -1e9))
        if remaining > 0: self.sleep(remaining)
        self.last[host] = self.clock()
        headers = {'User-Agent': AGENT, 'Accept': 'application/json'}
        if host == 'api.discogs.com' and self.token:headers['Authorization']='Discogs token='+self.token
        try:
            with urllib.request.build_opener(MetadataRedirect()).open(urllib.request.Request(url, headers=headers), timeout=12) as response:
                raw = response.read(MAX_JSON+1)
                if len(raw) > MAX_JSON:raise ValueError('Metadata response too large')
                data = json.loads(raw)
            self.store(url, {'expires': now+30*86400, 'data': data})
            return data
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 410):
                self.store(url, {'expires': now+7*86400, 'data': None})
                return None
            self.blocked[host] = now+900
            raise RuntimeError('Metadata HTTP '+str(exc.code)) from None
        except OSError:
            self.blocked[host] = now+900
            raise RuntimeError('Metadata connection unavailable') from None


def tag_metadata(document):
    labels = []
    for field in ('genres', 'tags'):
        for entry in document.get(field) or []:
            if isinstance(entry, dict) and entry.get('name') and (field=='genres' or entry.get('count', 0)>0):
                labels.append(str(entry['name'])[:100])
    return {'genres': sorted(set(labels))} if labels else {}


def recording(item, online):
    ids = profiles.external_ids(item)
    if ids.get('mbid'):
        record = online.get('https://musicbrainz.org/ws/2/recording/'+ids['mbid']+'?inc=artist-credits+tags+genres+isrcs&fmt=json')
        return record if record and profiles.recording_match(item, record) else None
    if ids.get('isrc'):
        data = online.get('https://musicbrainz.org/ws/2/isrc/'+ids['isrc']+'?inc=artist-credits&fmt=json') or {}
        # Even an ISRC can be reused across masters; retain duration/version checks.
        matched = profiles.choose_recording(item, data.get('recordings') or [])
        if not matched:return None
        full = online.get('https://musicbrainz.org/ws/2/recording/'+matched['id']+'?inc=artist-credits+tags+genres+isrcs&fmt=json')
        return full if full and profiles.recording_match(item,full) else None
    name, artist = profiles.title(item), profiles.artists(item)[0]
    if not name or not artist or not item.get('duration'):return None
    # Lucene special characters are escaped inside quoted exact title/artist fields.
    def quoted(text):return '"'+str(text).replace('\\','\\\\').replace('"','\\"')+'"'
    query='recording:'+quoted(name)+' AND artist:'+quoted(artist)
    data=online.get('https://musicbrainz.org/ws/2/recording/?'+urllib.parse.urlencode({'query':query,'fmt':'json','limit':10})) or {}
    record=profiles.choose_recording(item,[r for r in data.get('recordings') or [] if int(r.get('score',0))>=95])
    if not record:return None
    full=online.get('https://musicbrainz.org/ws/2/recording/'+record['id']+'?inc=artist-credits+tags+genres+isrcs&fmt=json')
    return full if full and profiles.recording_match(item,full) else None


def discogs_metadata(item, online):
    """Discogs release styles only after an exact track/version/artist match."""
    ids=[]
    for link in (item.get('metadata') or {}).get('links') or []:
        url=link.get('url','') if isinstance(link,dict) else str(link)
        match=re.match(r'https?://(?:www\.)?discogs\.com/(?:[a-z]{2}/)?release/(\d+)(?:[-/?#]|$)',url)
        if match:ids.append(match[1])
    album=item.get('album') or {}
    album=album.get('name') if isinstance(album,dict) else str(album)
    if not ids and online.token and album:
        data=online.get('https://api.discogs.com/database/search?'+urllib.parse.urlencode({'type':'release','artist':profiles.artists(item)[0],'release_title':album,'track':profiles.title(item),'per_page':5})) or {}
        ids=[str(r['id']) for r in data.get('results') or [] if r.get('id')][:3]
    for identifier in ids[:3]:
        release=online.get('https://api.discogs.com/releases/'+identifier) or {}
        names=[re.sub(r' \(\d+\)$','',a.get('name','')) for a in release.get('artists') or []]
        if not names or profiles.normal(names[0])!=profiles.normal(profiles.artists(item)[0]):continue
        if album and profiles.normal(album)!=profiles.normal(release.get('title')):continue
        matches=[t for t in release.get('tracklist') or [] if profiles.normal(t.get('title'))==profiles.normal(profiles.title(item))]
        if len(matches)!=1:continue
        duration = str(matches[0].get('duration') or '')
        if duration:
            try:seconds = sum(float(v)*60**i for i,v in enumerate(reversed(duration.split(':'))))
            except ValueError:continue
            if not profiles.length_matches(item.get('duration'),seconds):continue
        # A release style is contextual metadata, never a measured mood score.
        labels=[str(v)[:100] for v in (release.get('genres') or [])+(release.get('styles') or [])]
        return ({'genres':labels},'https://www.discogs.com/release/'+identifier)
    return {},None


def online_metadata(item, online):
    metadata=music_features.metadata(item);sources=['ma-provider'];match=None
    if profiles.useful(metadata):return metadata,sources,match
    try:record=recording(item,online)
    except (OSError, ValueError, RuntimeError):record=None;sources.append('musicbrainz:unavailable')
    if record:
        match=record['id'];sources.append('musicbrainz:'+match)
        metadata=music_features.merge(metadata,tag_metadata(record))
        if not profiles.useful(metadata):
            try:document=online.get('https://acousticbrainz.org/api/v1/'+match+'/high-level') or {}
            except (OSError,ValueError,RuntimeError):document={};sources.append('acousticbrainz:unavailable')
            acoustic=profiles.acoustic_metadata(item,document,match)
            if acoustic:
                metadata=music_features.merge(metadata,acoustic);sources.append('acousticbrainz:'+match)
            # Archived measured tempo is useful even when mood classifiers are weak.
            try:low=online.get('https://acousticbrainz.org/api/v1/'+match+'/low-level?features=rhythm.bpm') or {}
            except (OSError,ValueError,RuntimeError):low={}
            if profiles.length_matches(item.get('duration'),(low.get('metadata',{}).get('audio_properties') or {}).get('length')):
                bpm=music_features.number((low.get('rhythm') or {}).get('bpm'),30,300)
                if bpm is not None:metadata=music_features.merge(metadata,{'audio_features':{'bpm':bpm}})
    if not profiles.useful(metadata):
        try:extra,url=discogs_metadata(item,online)
        except (OSError,ValueError,RuntimeError):extra,url={},None;sources.append('discogs:unavailable')
        metadata=music_features.merge(metadata,extra)
        if url:sources.append(url)
    return metadata,sources,match


def origin(url):
    value=urllib.parse.urlsplit(url)
    return value.scheme,value.hostname,value.port or (443 if value.scheme=='https' else 80)


def safe_audio_url(url, base):
    parsed=urllib.parse.urlsplit(url)
    if parsed.username or parsed.password:raise ValueError('Credentials in preview URL')
    if origin(url)==origin(base):return
    if parsed.scheme!='https' or not parsed.hostname:raise ValueError('Invalid external preview URL')
    addresses=socket.getaddrinfo(parsed.hostname,parsed.port or 443,type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError('Private external preview destination')


class PreviewRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, base):self.base=base
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe_audio_url(newurl,self.base)
        redirected=super().redirect_request(req,fp,code,msg,headers,newurl)
        # MA bearer credentials never leave the configured MA origin.
        if redirected and origin(newurl)!=origin(self.base):redirected.remove_header('Authorization')
        return redirected


def fetch_preview(url, base, token):
    url=urllib.parse.urljoin(base.rstrip('/')+'/',url);safe_audio_url(url,base)
    headers={'User-Agent':AGENT}
    if token and origin(url)==origin(base):headers['Authorization']='Bearer '+token
    opener=urllib.request.build_opener(PreviewRedirect(base))
    with opener.open(urllib.request.Request(url,headers=headers),timeout=25) as response:
        data=response.read(MAX_AUDIO+1)
    if len(data)>MAX_AUDIO or len(data)<100:raise ValueError('Invalid or oversized preview')
    return data


def decode_preview(data):
    import numpy as np
    raw=subprocess.run(['ffmpeg','-nostdin','-v','error','-threads','1','-protocol_whitelist','pipe',
        '-i','pipe:0','-t','30','-ac','1','-ar','16000','-f','f32le','pipe:1'],input=data,
        capture_output=True,timeout=45,check=True).stdout
    audio=np.frombuffer(raw,dtype='<f4').copy()
    if not 15*16000<=len(audio)<=30*16000 or not np.isfinite(audio).all():raise ValueError('Invalid decoded preview')
    return [audio]


def players_idle(command):
    """No sampling while playing/paused; unknown state is conservatively busy."""
    values=command('players/all',{})
    if not isinstance(values,list):return False
    for player in values:
        state=player.get('playback_state',player.get('state'))
        if state not in ('idle','off','unavailable','stopped') and player.get('available',True):return False
    return True


def update(entries,payload,command,online,analyzer,local_aliases,*,max_items=500,max_samples=40,max_seconds=3600,now=None,preview=fetch_preview,decode=decode_preview,checkpoint=None,base='',token=''):
    now=time.time() if now is None else now;started=time.monotonic()
    tracks=payload.setdefault('tracks',{});assert payload.get('version')==1 and isinstance(tracks,dict)
    counts={'metadata':0,'sampled':0,'local_reused':0,'unchanged':0,'failed':0,'deferred':0,'busy':0,'sample_attempts':0}
    entries=sorted(entries,key=lambda e:(tracks.get(e['uri'],{}).get('checked_at',0),not e.get('favorite')))
    attempted=0
    for entry in entries:
        uri=entry['uri'];previous=tracks.get(uri,{})
        previous['aliases']=entry['aliases'] if previous else []
        model_current = previous.get('source') != 'ma_preview' or previous.get('analysis_fingerprint') == analyzer.fingerprint
        if previous.get('next_check',0)>now and previous.get('input_fingerprint')==profiles.fingerprint(entry) and model_current:
            counts['unchanged']+=1;continue
        if attempted>=max_items or time.monotonic()-started>=max_seconds:counts['deferred']+=1;continue
        attempted+=1
        record=dict(previous,aliases=entry['aliases'],checked_at=now,input_fingerprint=profiles.fingerprint(entry))
        try:
            provider,identifier=uri.split('://track/',1)
            detail=command('music/tracks/get',{'item_id':identifier,'provider_instance_id_or_domain':provider,'allow_update_metadata':False,'recursive':False})
            if not isinstance(detail,dict) or detail.get('media_type')!='track':raise ValueError('Invalid provider track')
            # Provider may resolve aliases; accept no unexpected track identifier.
            if str(detail.get('item_id'))!=identifier:raise ValueError('Provider track identity mismatch')
            metadata,sources,mbid=online_metadata(detail,online)
            record.update(metadata=music_features.merge(previous.get('metadata',{}),metadata),sources=sources,matched_recording=mbid,next_check=now+30*86400,source='online_metadata')
            if profiles.useful(metadata):
                counts['metadata']+=1;record['status']='metadata_ready'
            elif any(alias in local_aliases for alias in entry['aliases']):
                counts['local_reused']+=1;record.update(status='local_audio_available',source='local_audio')
            elif counts['sample_attempts']>=max_samples:
                record.update(status='awaiting_sample',next_check=now+86400);counts['deferred']+=1
            elif not players_idle(command):
                record.update(status='sampling_deferred_busy',next_check=now+86400);counts['busy']+=1
            else:
                counts['sample_attempts']+=1
                sample_url=command('music/tracks/preview',{'provider_instance_id_or_domain':provider,'item_id':identifier})
                if not isinstance(sample_url,str):raise ValueError('Invalid preview response')
                features=analyzer.analyze(decode(preview(sample_url,base,token)))
                features.update(analysis='provider-preview-four-patches-v1')
                record.update(metadata=music_features.merge(metadata,{'audio_features':features}),source='ma_preview',status='sample_ready',analysis_fingerprint=analyzer.fingerprint)
                counts['sampled']+=1
            record.pop('error',None)
        except (OSError,ValueError,RuntimeError,TypeError,AttributeError,subprocess.SubprocessError) as exc:
            # Retain useful previous metadata/descriptors, and never persist signed URLs.
            record.update(error=type(exc).__name__,next_check=now+86400,status='retry_pending')
            if previous.get('metadata',{}).get('audio_features'):
                record['source']=previous.get('source','ma_preview')
            counts['failed']+=1
        tracks[uri]=record
        if checkpoint and attempted%25==0:checkpoint(payload)
    return counts


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path('/media/local/cache/provider_profiles.json'))
    parser.add_argument('--library',type=Path,default=Path('/media/local/cache/mass_playlists.json'))
    parser.add_argument('--audio-profile',type=Path,default=Path('/media/local/cache/audio_features.json'))
    parser.add_argument('--models',type=Path,default=Path('/media/local/cache/audio-analysis/models'))
    parser.add_argument('--max-items',type=int,default=500);parser.add_argument('--max-samples',type=int,default=40);parser.add_argument('--max-seconds',type=int,default=3600)
    args=parser.parse_args()
    if not 1<=args.max_items<=10000 or not 0<=args.max_samples<=1000 or not 1<=args.max_seconds<=86400:raise ValueError('Invalid limits')
    os.umask(0o077);args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.with_suffix('.lock').open('w') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return 0
        configured=os.getenv('MASS_WS_URL') or os.getenv('BS5C_MASS_WS_URL') or 'http://localhost:8095'
        base=configured.replace('wss://','https://').replace('ws://','http://').removesuffix('/ws');token=os.getenv('MASS_TOKEN','')
        command=api_client(base,token);canonical=catalogue(command)
        cached=profiles.cached_items(json.loads(args.library.read_text())) if args.library.exists() else []
        entries=profiles.inventory(canonical,cached)
        payload=json.loads(args.output.read_text()) if args.output.exists() else {'version':1,'tracks':{}}
        if payload.get('version')!=1 or not isinstance(payload.get('tracks'),dict):raise ValueError('Invalid provider cache')
        request_path=args.output.with_name('provider_metadata_requests.json')
        requests=json.loads(request_path.read_text()) if request_path.exists() else {}
        online=Online(requests,os.getenv('BS5C_DISCOGS_TOKEN',''))
        local=json.loads(args.audio_profile.read_text()) if args.audio_profile.exists() else {'version':1,'tracks':{}}
        aliases={u for u in local['tracks']}
        for record in local['tracks'].values():aliases.update(record.get('aliases',[]))
        from music_audio_onnx import Analyzer
        counts=update(entries,payload,command,online,Analyzer(args.models),aliases,max_items=args.max_items,max_samples=args.max_samples,max_seconds=args.max_seconds,
            checkpoint=lambda value:(atomic_write(args.output,value),atomic_write(request_path,requests)),base=base,token=token)
        payload['generated_at']=time.time();atomic_write(args.output,payload);atomic_write(request_path,requests)
        status={'checked_at':time.time(),'catalogue_entries':len(entries),**counts,'ready':sum(profiles.useful(r.get('metadata',{})) or r.get('status')=='local_audio_available' for r in payload['tracks'].values())}
        atomic_write(args.output.with_suffix('.status.json'),status);print(json.dumps(status));return 0


if __name__=='__main__':raise SystemExit(main())
