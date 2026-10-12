"""Offline selection and durable listening receipts for the iPod sync worker."""
import datetime as dt,json,math,re,time
from collections import deque
from pathlib import Path
from aiohttp import web
from . import mix_policy

async def catalogue(service, limit=10000, playlists=()):
    candidates=[i for i in service._load_library() if i.get('media_type')=='track' and i.get('uri')]
    by_uri={i['uri']:i for i in candidates};ordered=[];seen=set();groups={}
    def add(items, name=None):
        uris=[]
        for item in items:
            if item.get('uri') not in by_uri:continue
            uris.append(item['uri'])
            identity=mix_policy.identity(item)
            # Exact recording/normalised title dedup, not artist-wide exclusion.
            ident=(identity[0],identity[1])
            if item['uri'] not in seen and ident not in seen:
                ordered.append(item);seen.add(item['uri']);seen.add(ident)
        if name:groups[name]=uris
    add([i for i in candidates if i.get('favorite')], 'Favourites')
    names=list(playlists) or list(service.cfg.get('trusted_playlists',['Trusted music','All favorited tracks']))
    for name in names:
        add([i for i in candidates if str(name).casefold() in str(i.get('playlist_tags','')).casefold()],str(name))
    recent=service.model.db.execute('SELECT uri,MAX(ts) latest FROM listens WHERE ts>? AND seconds>=30 GROUP BY uri ORDER BY latest DESC LIMIT 1500',(time.time()-180*86400,)).fetchall()
    add([by_uri[r['uri']] for r in recent if r['uri'] in by_uri], 'Recently listened')
    context=service._context_for_room('ipod')
    seeds=[]
    for bucket,hour in [('morning',8),('afternoon',14),('evening',20)]:
        ctx=dict(context,time_bucket=bucket,hour=hour)
        for item in service.model.rank(candidates,ctx,3):
            if not any(mix_policy.same_recording(item,seed) for seed in seeds):seeds.append(item)
    for seed in seeds[:6]:
        radio=await service._mix_recommend('ipod',35,None,seed,{'mode':'radio'})
        add([seed]+radio,'Radio — '+str(seed.get('title') or seed.get('name') or 'Music'))
    add(service.model.rank(candidates,context,50),'Predicted listening')
    # Capacity filler follows the evidence-led selections, rather than displacing them.
    add(sorted(candidates,key=lambda i:(not bool(i.get('trusted')),not bool(i.get('favorite')),i.get('artist',''),i.get('title',''))))
    # Interleave priorities so a large favourites collection cannot consume the
    # entire device before recent/predicted radio selections are considered.
    preferred=[];picked=set();eligible={i['uri'] for i in ordered};pools=[deque(uris) for uris in groups.values()]
    while any(pools):
        for pool in pools:
            while pool:
                uri=pool.popleft()
                if uri not in picked and uri in eligible:
                    preferred.append(by_uri[uri]);picked.add(uri);break
    preferred.extend(i for i in ordered if i['uri'] not in picked)
    return {'items':preferred[:max(1,min(int(limit),10000))],'groups':groups,'candidate_count':len(candidates),'model':'local-pattern-and-song-radio'}

def record_history(service, events):
    if not isinstance(events,list) or len(events)>100:raise ValueError('At most 100 listening events per batch')
    service.model.db.execute('CREATE TABLE IF NOT EXISTS ipod_listen_receipts(event_id TEXT PRIMARY KEY, ts REAL NOT NULL)')
    validated=[]
    for e in events:
        stamp=float(e['timestamp_ms'])/1000;seconds=float(e['seconds']);duration=float(e['duration'])
        if not re.fullmatch(r'ipod_[a-f0-9]{64}',str(e.get('event_id',''))):raise ValueError('Invalid event identity')
        if not all(math.isfinite(v) for v in [stamp,seconds,duration]) or not 0<=seconds<=duration or duration<=0:raise ValueError('Invalid elapsed time')
        if not 0<stamp<=time.time()+300 or not e.get('uri') or not e.get('title') or e.get('media_type')!='track':raise ValueError('Invalid recording')
        if e.get('reason') not in ('skip','complete'):raise ValueError('Invalid outcome')
        validated.append((e,stamp,seconds))
    accepted=0
    for e,stamp,seconds in validated:
        if service.model.db.execute('SELECT 1 FROM ipod_listen_receipts WHERE event_id=?',(e['event_id'],)).fetchone():continue
        # Completion is familiarity evidence, not equivalent to an explicit favourite or selection.
        item=dict(e,end_reason=e['reason'],selection_origin='unknown')
        when=dt.datetime.fromtimestamp(stamp)
        context=dict(service._context_for_room('ipod'),hour=when.hour,weekday=when.weekday(),
            time_bucket=('morning' if 5<=when.hour<12 else 'afternoon' if 12<=when.hour<17 else 'evening' if 17<=when.hour<23 else 'night'),
            weekpart='weekend' if when.weekday()>=5 else 'weekday')
        if service.model.record_listen(item,seconds,context,event_id=e['event_id'],event_ts=stamp): accepted+=1
    return {'ok':True,'accepted':accepted,'duplicate':len(events)-accepted}

def local_only(request):
    if request.remote not in ('127.0.0.1','::1'):raise web.HTTPForbidden(text='iPod sync API is local only')
