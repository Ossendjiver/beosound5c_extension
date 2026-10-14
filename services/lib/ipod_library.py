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
    # Priority tiers are strict; only radios within tier 2 are interleaved.
    add([i for i in candidates if i.get('favorite')], 'Favourites')
    names=list(playlists) or list(service.cfg.get('trusted_playlists',['Trusted music','All favorited tracks']))
    for name in names:
        add([i for i in candidates if str(name).casefold() in str(i.get('playlist_tags','')).casefold()],str(name))
    context=service._context_for_room('ipod')
    seeds=[];seed_pools=[]
    for bucket,hour in [('morning',8),('afternoon',14),('evening',20)]:
        ctx=dict(context,time_bucket=bucket,hour=hour)
        seed_pools.append(deque(service.model.rank(candidates,ctx,3)))
    while any(seed_pools):
        for pool in seed_pools:
            if pool:
                item=pool.popleft()
                if not any(mix_policy.same_recording(item,seed) for seed in seeds):seeds.append(item)
    radios=[]
    for seed in seeds[:9]:
        radio=[seed]+await service._mix_recommend('ipod',35,None,seed,{'mode':'radio'})
        name='Radio — '+str(seed.get('title') or seed.get('name') or 'Music')
        groups[name]=[i['uri'] for i in radio if i.get('uri') in by_uri]
        radios.append(deque(radio))
    while any(radios):
        for radio in radios:
            if radio:add([radio.popleft()])
    exposure=recorded_exposure(service.model.db)
    def needs_trial(item):
        plays,skips=exposure.get(exposure_key(item),(0,0))
        try:plays=max(plays,int(item.get('play_count') or item.get('ma_play_count') or 0))
        except (ValueError,TypeError):pass
        return plays<1 and skips<2
    # Let the prediction model retain its dislikes and recent-skip exclusions.
    predicted=service.model.rank(candidates,context,len(candidates))
    add([i for i in predicted if needs_trial(i)],'Predicted listening — needs a trial')
    add([i for i in predicted if not needs_trial(i)][:50],'Predicted listening')
    recent=service.model.db.execute('SELECT uri,MAX(ts) latest FROM listens WHERE ts>? AND seconds>=30 GROUP BY uri ORDER BY latest DESC LIMIT 1500',(time.time()-180*86400,)).fetchall()
    add([by_uri[r['uri']] for r in recent if r['uri'] in by_uri], 'Recently listened')
    add(sorted(candidates,key=lambda i:(not bool(i.get('trusted')),not bool(i.get('favorite')),i.get('artist',''),i.get('title',''))))
    return {'items':ordered[:max(1,min(int(limit),10000))],'groups':groups,'candidate_count':len(candidates),'model':'local-pattern-and-song-radio','selection_policy':'priority-tiers-v2'}

def exposure_key(item):
    return json.dumps(mix_policy.identity(item),ensure_ascii=False)

def recorded_exposure(db):
    """Keep evaluation evidence after the rolling listening log is trimmed."""
    with db:
        db.execute('CREATE TABLE IF NOT EXISTS ipod_exposure (recording TEXT PRIMARY KEY, plays INTEGER NOT NULL, skips INTEGER NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS ipod_exposure_cursor (singleton INTEGER PRIMARY KEY CHECK(singleton=1), last_id INTEGER NOT NULL)')
        row=db.execute('SELECT last_id FROM ipod_exposure_cursor WHERE singleton=1').fetchone()
        cursor=row[0] if row else 0
        for row in db.execute('SELECT id,title,artist,uri,seconds,reward,end_reason FROM listens WHERE id>? ORDER BY id',(cursor,)).fetchall():
            skipped=row['end_reason']=='skip' or row['reward']==-2
            played=not skipped and (row['end_reason']=='complete' or (row['seconds']>=30 and row['reward']>0))
            if played or skipped:
                db.execute('INSERT INTO ipod_exposure VALUES (?,?,?) ON CONFLICT(recording) DO UPDATE SET plays=plays+excluded.plays,skips=skips+excluded.skips',(exposure_key(dict(row)),int(played),int(skipped)))
            cursor=row['id']
        db.execute('INSERT INTO ipod_exposure_cursor VALUES (1,?) ON CONFLICT(singleton) DO UPDATE SET last_id=excluded.last_id',(cursor,))
    return {r['recording']:(r['plays'],r['skips']) for r in db.execute('SELECT * FROM ipod_exposure')}

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
