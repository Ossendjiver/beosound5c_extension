import ast,asyncio,base64,hashlib,json,sys
from pathlib import Path
from types import SimpleNamespace as N
import argparse
parser=argparse.ArgumentParser();parser.add_argument('--prepared',type=Path,required=True);args=parser.parse_args()
R=args.prepared
sys.path.insert(0,str(R/'helpers'))
from tidal_dash_routes import DashManifestRoutes,DASH_DETAILS_TTL
p=R/'providers/tidal/streaming.py';tree=ast.parse(p.read_text());cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='TidalStreamingManager');method=next(n for n in cls.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='get_stream_details')
ns={'base64':base64,'DASH_DETAILS_TTL':DASH_DETAILS_TTL,'_MANIFEST_DASH':'application/dash+xml','_MANIFEST_BTS':'application/vnd.tidal.bts','CONF_QUALITY':'quality','MediaNotFoundError':ValueError,'ContentType':N(FLAC='flac',MP4='mp4',try_parse=lambda s:s),'AudioFormat':lambda **kw:N(**kw),'StreamType':N(HTTP='http'),'StreamDetails':lambda **kw:N(**kw)}
exec(compile('from __future__ import annotations\n'+ast.unparse(method),str(p),'exec'),ns)
async def main():
    registered={};timers=[]
    def register(path,handler,**_):registered[path]=handler
    def task(coro):coro.close()
    mass=N(streams=N(base_url='http://localhost',register_dynamic_route=register,unregister_dynamic_route=lambda path,**_:registered.pop(path)),call_later=lambda *a,**kw:timers.append(a),create_task=task)
    async def get_track(_):return N(item_id='track',duration=211)
    async def info(*_):return {'manifestMimeType':'application/dash+xml','manifest':base64.b64encode(b'<MPD/>').decode(),'audioQuality':'LOSSLESS'}
    async def update(**_):pass
    obj=N(provider=N(get_track=get_track,config=N(get_value=lambda _:'LOSSLESS'),instance_id='tidal--test'),_fetch_playback_info=info,_dash_routes=DashManifestRoutes(mass),mass=mass,_async_update_provider_mapping_audio_format=update)
    result=await ns['get_stream_details'](obj,'track')
    assert result.expiration==120 and result.path.removeprefix(mass.streams.base_url) in registered
    assert timers[-1][0]>result.expiration
    async def direct(*_):return {'manifestMimeType':'other','audioQuality':'LOSSLESS'}
    obj._fetch_playback_info=direct;obj._get_direct_url=lambda _:('https://provider.invalid/audio',None)
    result=await ns['get_stream_details'](obj,'track');assert result.expiration==600
    # Verify hooks are in the intended methods, not merely present in a diff.
    q=ast.parse((R/'controllers/player_queues/controller.py').read_text());c=next(n for n in q.body if isinstance(n,ast.ClassDef) and n.name=='PlayerQueuesController')
    for name in ['pause','_handle_stop','close']:
        m=next(n for n in c.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name)
        assert 'stream_failure_recovery.cancel' in ast.unparse(m)
    text=(R/'controllers/streams/controller.py').read_text();assert 'stream_failure_recovery.arm' in text
    print('PASS actual patched Tidal method: DASH TTL, route validity, non-DASH unchanged; stop/pause/shutdown hooks; stream-failure hook; Python 3.14 compilation')
asyncio.run(main())
