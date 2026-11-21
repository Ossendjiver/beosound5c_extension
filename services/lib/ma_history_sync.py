"""Hourly read-only MA recent-history evidence, without inventing play events."""
import asyncio
import logging
import time

INTERVAL=3600
log=logging.getLogger('beo-library.ma-history')

async def run_hourly(service):
    while True:
        started=time.monotonic()
        try:
            await asyncio.wait_for(service._refresh_familiarity(),timeout=120)
            log.info('Hourly MA history refresh completed')
        except asyncio.CancelledError:raise
        except Exception as error:log.warning('MA history refresh retained cache: %s',type(error).__name__)
        await asyncio.sleep(max(1,INTERVAL-(time.monotonic()-started)))

async def collect(command,previous,model,familiarity):
    evidence,status=await familiarity.collect(command,previous)
    now=time.time();deadline=time.monotonic()+30
    try:
        rows=await asyncio.wait_for(command('music/recently_played_items',
            {'limit':200,'media_types':['track'],'fully_played_only':False}),timeout=8)
        if not isinstance(rows,list):raise ValueError('Expected recent-item list')
    except asyncio.CancelledError:raise
    except Exception as error:
        status['ma_recent_error']=type(error).__name__;return evidence,status
    existing=model.get_kv('ma_recent_history_v1',{})
    previous_items={i['uri']:i for i in existing.get('items',[]) if isinstance(i,dict) and i.get('uri')}
    imported={};seen=set();unresolved=0
    for row in rows[:200]:
        if not isinstance(row,dict) or row.get('media_type')!='track':continue
        uri=str(row.get('uri') or '')
        if not uri or uri in seen:continue
        seen.add(uri)
        item=(evidence.get(uri) or {}).get('item') or previous_items.get(uri)
        if not item and time.monotonic()<deadline:
            try:
                detail=await asyncio.wait_for(command('music/item_by_uri',
                    {'uri':uri,'allow_update_metadata':False}),timeout=min(3,max(.1,deadline-time.monotonic())))
                item=familiarity.track(detail)
            except asyncio.CancelledError:raise
            except Exception:pass
        if not item:
            unresolved+=1;continue
        # A recent-items API result is a membership snapshot, not a unique
        # timestamped listen or proof of a deliberate choice/mood/room.
        familiarity.observe(evidence,item,'ma:recent',history=True,now=now)
        imported[uri]=dict(item)
    model.put_kv('ma_recent_history_v1',dict(observed_at=now,source='ma_recent_items',
        items=list(imported.values()),returned=len(rows),unresolved=unresolved,
        timestamps_available=False,interval_seconds=INTERVAL))
    status.update(ma_recent_items=len(imported),ma_recent_returned=len(rows),
        ma_recent_unresolved=unresolved,ma_recent_last_sync=now,refresh_interval_seconds=INTERVAL)
    return evidence,status
