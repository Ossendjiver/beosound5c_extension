"""Fake HA verifies the art-only command, conditional state and failure isolation."""
import asyncio
from unittest.mock import patch

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
from lib.frame_standby import frame_art_standby


@pytest.mark.asyncio
@pytest.mark.parametrize('state,art,expected', [
    ('on','off',True), ('playing','off',True), ('paused','off',True), ('idle','off',True),
    ('on','on',False), ('off','off',False), ('unavailable',None,False), ('unknown',None,False)])
async def test_only_active_frame_is_sent_to_art(state, art, expected):
    calls=[]
    async def handle(request):
        calls.append((request.method,request.path,await request.json() if request.method=='POST' else None))
        assert request.headers['Authorization']=='Bearer test-token'
        if request.method=='GET':
            return web.json_response({'entity_id':'media_player.the_frame','state':state,'attributes':{'art_mode_status':art}})
        return web.json_response([])
    app=web.Application();app.router.add_route('*','/{tail:.*}',handle)
    def config(*keys,default=None):
        return 'media_player.the_frame' if keys==('mass','youtube_search','frame_entity') else default
    async with TestServer(app) as server, ClientSession() as session:
        with patch('lib.frame_standby.cfg',side_effect=config), patch.dict('os.environ',{'HA_URL':str(server.make_url('/')),'HA_TOKEN':'test-token'}):
            await frame_art_standby(session)
    assert len(calls)==(2 if expected else 1)
    if expected:
        assert calls[-1]==('POST','/api/services/samsungtv_smart/set_art_mode',{'entity_id':'media_player.the_frame'})
    assert all('turn_off' not in path and 'turn_on' not in path for _,path,_ in calls)


@pytest.mark.asyncio
async def test_failed_state_read_does_not_send_any_tv_command():
    calls=[]
    async def fail(request):
        calls.append(request.method);return web.Response(status=503)
    app=web.Application();app.router.add_route('*','/{tail:.*}',fail)
    with patch('lib.frame_standby.cfg',return_value='media_player.the_frame'):
        async with TestServer(app) as server, ClientSession() as session:
            with patch.dict('os.environ',{'HA_URL':str(server.make_url('/')),'HA_TOKEN':'test-token'}):
                await frame_art_standby(session)
    assert calls==['GET']


@pytest.mark.asyncio
async def test_missing_auth_or_invalid_entity_never_contacts_ha():
    from unittest.mock import MagicMock
    session=MagicMock()
    for entity,token in [('media_player.the_frame',''),('http://wrong','token')]:
        with patch('lib.frame_standby.cfg',return_value=entity),patch.dict('os.environ',{'HA_TOKEN':token}):
            await frame_art_standby(session)
    session.get.assert_not_called();session.post.assert_not_called()
