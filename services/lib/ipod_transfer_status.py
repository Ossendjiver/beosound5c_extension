"""Sanitised status reports from the two paired LAN iPod updaters."""
import re
import time
from pathlib import Path
from .ipod_sync import atomic_json

STATUS=Path('/media/local/cache/ipod-external/status.json')
STATES={'connecting','importing','selecting','copying','complete','waiting_for_eject','error','cancelled'}
COUNTS={'copied','retained','did_not_fit','checked','total','history_uploaded','unmatched_history'}
ACTIVE=STATES-{'complete','waiting_for_eject','error','cancelled'}

def update(body,device_id):
    if not isinstance(body,dict) or set(body)-({'client','session','sequence','state','device_id','completed_at'}|COUNTS):raise ValueError()
    if body.get('client') not in ('ubuntu','app') or body.get('state') not in STATES:raise ValueError()
    if not isinstance(body.get('session'),str) or not re.fullmatch(r'[a-f0-9-]{36}',body['session']):raise ValueError()
    if not device_id or body.get('device_id')!=device_id:raise ValueError()
    for name in COUNTS|{'sequence'}:
        if name in body and (type(body[name]) is not int or not 0<=body[name]<=1000000):raise ValueError()
    if 'sequence' not in body:raise ValueError()
    stamp=body.get('completed_at')
    if stamp is not None and (type(stamp) not in (int,float) or not 0<stamp<=time.time()+60 or body['state']!='complete'):raise ValueError()
    try:
        import json
        data=json.loads(STATUS.read_text())
    except FileNotFoundError:data={'clients':{}}
    client=body['client'];old=data['clients'].get(client,{})
    retired=old.get('retired',[])
    if body['session'] in retired:return False
    if old.get('session')==body['session'] and body['sequence']<=old.get('sequence',-1):return False
    if old.get('session') and old['session']!=body['session']:retired=(retired+[old['session']])[-8:]
    record={k:v for k,v in body.items() if k in COUNTS|{'session','sequence','state'}}
    record.update(updated=time.time(),retired=retired,last_sync=old.get('last_sync'),history=old.get('history',[]))
    if record['state']=='complete':
        record['last_sync']=stamp or record['updated']
        entry={k:v for k,v in record.items() if k in COUNTS|{'session'}}
        entry['completed_at']=record['last_sync']
        record['history']=([v for v in record['history'] if v['session']!=record['session']]+[entry])[-20:]
    data['clients'][client]=record
    STATUS.parent.mkdir(parents=True,exist_ok=True)
    atomic_json(STATUS,data);STATUS.chmod(0o644)
    return True

def public(data,now=None):
    now=time.time() if now is None else now
    result={}
    for client,record in data.get('clients',{}).items():
        if client not in ('ubuntu','app'):continue
        row={k:v for k,v in record.items() if k in COUNTS|{'state','updated','last_sync'}}
        if row.get('state') in ACTIVE and now-row.get('updated',0)>900:row['state']='interrupted'
        result[client]=row
    return result
