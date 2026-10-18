import json
import sqlite3
from types import SimpleNamespace
import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer,TestClient
import pytest
from lib.background_tasks import BackgroundTaskSet
from lib.playback_runtime import Runtime

@pytest.mark.asyncio
async def test_runtime_auth_closed_routes_and_real_http_contract(tmp_path,monkeypatch):
    calls=[]
    async def handle(request):
        if request.path=='/api':
            data=await request.json();calls.append(data)
            if data['command']=='players/all':return web.json_response([dict(player_id='cast',available=True,active_source='cast')])
            if data['command']=='player_queues/all':return web.json_response([dict(queue_id='cast',state='playing',current_item={'name':'Song','url':'private-token'},items=2)])
            return web.json_response([])
        entity=request.match_info.get('entity','')
        if entity:return web.json_response(dict(entity_id=entity,state='off',attributes={'private':'do not expose'}))
        return web.json_response({})
    app=web.Application();app.router.add_post('/api',handle);app.router.add_get('/api/states/{entity}',handle)
    async with TestServer(app) as server,aiohttp.ClientSession() as session:
        path=tmp_path/'routes.json';path.write_text(json.dumps({'rooms':{'kitchen':{'primary':{'player_id':'cast'}}}}))
        monkeypatch.setenv('BS5C_PLAYBACK_ROUTES',str(path));monkeypatch.setenv('BS5C_COORDINATOR_KEY','x'*48)
        monkeypatch.setenv('MASS_WS_URL',str(server.make_url('/ws')));monkeypatch.setenv('MASS_TOKEN','test-only')
        service=SimpleNamespace(model=SimpleNamespace(db=sqlite3.connect(':memory:')),_background=BackgroundTaskSet('test'))
        runtime=Runtime(service,session,str(server.make_url('/api')).rstrip('/'),{})
        api=web.Application(client_max_size=65536);api.router.add_get('/state',runtime.state);api.router.add_route('*','/jobs',runtime.jobs)
        async with TestClient(TestServer(api)) as client:
            assert (await client.get('/state')).status==401
            headers={'X-HomeMedia-Key':'x'*48}
            result=await client.get('/state',headers=headers);assert result.status==200
            value=await result.json();text=json.dumps(value)
            assert 'private-token' not in text and 'do not expose' not in text and 'Authorization' not in text
            assert value['rooms'][0]['room']=='kitchen'
            receipt=await client.post('/jobs',headers=headers,json={'request_id':'request-012345678901234','operation':'leave','target':{'room':'kitchen'}})
            assert receipt.status==202
            value=await receipt.json();assert value['status']=='queued'
            assert not any(c['command'].startswith('players/cmd/') for c in calls)
            duplicate=await client.post('/jobs',headers=headers,json=value['request']);assert (await duplicate.json())['job_id']==value['job_id']
            invalid=await client.post('/jobs',headers=headers,json={'request_id':value['job_id'],'operation':'leave','target':{'room':'kitchen','entity':'arbitrary'}})
            assert invalid.status==400
            status=await client.get('/jobs',headers=headers,params={'job_id':value['job_id']});assert (await status.json())['status']=='queued'
            assert (await client.get('/state',headers=headers,params={'host':'evil'})).status==400
