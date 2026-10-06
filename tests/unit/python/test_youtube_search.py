from __future__ import annotations
import asyncio
from unittest.mock import AsyncMock, patch
import pytest
from lib.youtube_search import YouTubeSearch, enabled, search_flag
from sources.mass.service import MassSource


def make_bridge(config=None):
    return YouTubeSearch(config or {}, 'http://192.168.4.103:8089', 'http://ha:8123', 'test-token')


def test_switches_are_independent_and_false_strings_are_not_enabled():
    bridge=make_bridge({'music_enabled':'false','videos_enabled':'true'})
    assert bridge.flags()==(False, True)
    assert bridge.flags(True, False)==(True, False)
    assert not enabled('false')
    assert search_flag(None) is None
    with pytest.raises(ValueError): search_flag('maybe')


@pytest.mark.asyncio
async def test_search_groups_deduplicate_and_route_music_and_video_differently():
    bridge=make_bridge()
    raw={'video_id':'aqz-KE-bpKQ','title':'Blender','channel':'Channel','artwork':'https://img.example/a','duration':635}
    bridge._bridge_json=AsyncMock(return_value={'items':[raw,raw,dict(raw,video_id='invalid')]})
    music=await bridge.search('Blender','music',8)
    video=await bridge.search('Blender','video',8)
    assert len(music['tracks'])==len(video['tracks'])==1
    assert music['tracks'][0]['url']=='http://192.168.4.103:8089/audio/aqz-KE-bpKQ'
    assert video['tracks'][0]['url']=='youtube://video/aqz-KE-bpKQ'
    assert video['tracks'][0]['youtube_video'] is True
    assert music['tracks'][0]['youtube_video'] is False


@pytest.mark.asyncio
async def test_disabled_sources_make_no_youtube_requests_and_optional_failure_keeps_ma_results():
    with patch.object(MassSource,'_load_local_cache',return_value=False): source=MassSource()
    source._search_all_mass_providers=AsyncMock(return_value={'groups':[{'name':'Tracks','tracks':[{'name':'local'}]}],'scope':'all_mass_providers'})
    bridge=make_bridge();bridge.search=AsyncMock(side_effect=RuntimeError('unavailable'))
    source._youtube_search=lambda:bridge
    result=await source._search_enabled_sources('Blender',music=False,videos=False)
    bridge.search.assert_not_awaited();assert result['total']==1
    result=await source._search_enabled_sources('Blender',music=True,videos=False)
    assert result['total']==1 and result['state']=='ready'
    assert result['warnings']==['YouTube music search unavailable']


@pytest.mark.asyncio
async def test_frame_route_matches_home_media_and_never_sends_power_off_or_bs3_commands():
    bridge=make_bridge({'frame_entity':'media_player.the_frame','playback_entity':'media_player.youtube_on_frame_65'})
    calls=[]
    async def ha(method,path,data=None):
        calls.append((method,path,data));return {'state':'on','attributes':{'art_mode_status':'on'}} if method=='GET' else []
    bridge._ha=ha
    with patch('lib.youtube_search.asyncio.sleep',new=AsyncMock()): result=await bridge.play_video('youtube://video/aqz-KE-bpKQ')
    assert result['state']=='video_sent'
    assert [c[1] for c in calls]==['states/media_player.the_frame','services/media_player/turn_on','services/media_player/play_media','services/media_player/play_media']
    assert calls[-2][2]=={'entity_id':'media_player.the_frame','media_content_id':'tUb3Xq7Lm9.Tube','media_content_type':'app'}
    assert calls[-1][2]=={'entity_id':'media_player.youtube_on_frame_65','media_content_id':'aqz-KE-bpKQ','media_content_type':'video','enqueue':'play'}
    assert all('bs3' not in str(c) and 'turn_off' not in str(c) for c in calls)


@pytest.mark.asyncio
async def test_video_command_bypasses_mass_queue_and_automatic_source_power():
    with patch.object(MassSource,'_load_local_cache',return_value=False): source=MassSource()
    bridge=make_bridge();bridge.play_video=AsyncMock(return_value={'state':'video_sent'})
    source._youtube_search=lambda:bridge
    source.send_command=AsyncMock();source.register=AsyncMock();source._apply_playback_target_from_data=AsyncMock()
    result=await source.handle_command('play_item',{'url':'youtube://video/aqz-KE-bpKQ','player_id':'link'})
    assert result['state']=='video_sent';source.send_command.assert_not_awaited();source.register.assert_not_awaited();source._apply_playback_target_from_data.assert_not_called()
    result=await source.handle_command('play_next',{'url':'youtube://video/aqz-KE-bpKQ'})
    assert result['reason']=='video_action_unsupported'
    assert bridge.play_video.await_count==1


@pytest.mark.asyncio
async def test_bad_frame_mapping_fails_before_any_home_assistant_request():
    bridge=make_bridge({'frame_entity':'media_player.the_frame','playback_entity':'media_player.the_frame'})
    bridge._ha=AsyncMock()
    with pytest.raises(ValueError):await bridge.play_video('youtube://video/aqz-KE-bpKQ')
    bridge._ha.assert_not_awaited()


@pytest.mark.asyncio
async def test_audio_preserves_stable_identity_and_supplies_metadata_without_auto_import():
    bridge=make_bridge();bridge._bridge_json=AsyncMock(return_value={'title':'Real title','artwork':'https://img.example/a'})
    raw={'provider':'builtin','media_type':'track','duration':635,'name':'id','sort_name':'id','metadata':{}}
    command=AsyncMock(return_value=raw)
    item=await bridge.audio_item('http://192.168.4.103:8089/audio/aqz-KE-bpKQ',command)
    assert item['name']=='Real title' and 'sort_name' not in item
    assert raw['name']=='id' and raw['metadata']=={}
    command.assert_awaited_once_with('music/item_by_uri',uri='http://192.168.4.103:8089/audio/aqz-KE-bpKQ')
    assert await bridge.audio_item('tidal://track/1',command) is None


@pytest.mark.asyncio
async def test_http_search_and_voice_forward_independent_flags_without_network_or_mic():
    from aiohttp import web, ClientSession
    with patch.object(MassSource,'_load_local_cache',return_value=False):source=MassSource()
    source._search_enabled_sources=AsyncMock(return_value={'state':'empty','groups':[]})
    source._pause_for_voice_capture=AsyncMock(return_value='')
    source._resume_after_voice_capture=AsyncMock()
    source._transcribe_microphone=AsyncMock(return_value='Blender')
    app=web.Application();source.add_routes(app);runner=web.AppRunner(app);await runner.setup()
    site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
    port=site._server.sockets[0].getsockname()[1]
    try:
        async with ClientSession() as session:
            response=await session.get(f'http://127.0.0.1:{port}/search?q=Blender&youtube_music=1&youtube_videos=0')
            assert response.status==200
            source._search_enabled_sources.assert_awaited_with('Blender',8,True,False)
            response=await session.get(f'http://127.0.0.1:{port}/search?q=Blender&youtube_music=maybe')
            assert response.status==400
            response=await session.post(f'http://127.0.0.1:{port}/voice_search',json={'youtube_music':False,'youtube_videos':True})
            assert response.status==200
            source._search_enabled_sources.assert_awaited_with('Blender',music=False,videos=True)
    finally:
        await runner.cleanup()

@pytest.mark.asyncio
async def test_channel_podcast_subscription_deduplicates_and_uses_stable_feed():
    bridge=YouTubeSearch({},'http://192.168.4.103:8089')
    bridge._bridge_json=AsyncMock(return_value={'channel_id':'UCSMOQeBJ2RAnuFungnQOxLg'})
    command=AsyncMock(side_effect=[[],{}])
    result=await bridge.save_channel_podcast('https://www.youtube.com/@BlenderOfficial',command)
    feed='http://192.168.4.103:8089/podcast/UCSMOQeBJ2RAnuFungnQOxLg.xml'
    assert result['state']=='podcast_saved'
    command.assert_any_await('config/providers/save',provider_domain='podcastfeed',values={'feed_url':feed})
    command=AsyncMock(return_value=[{'values':{'feed_url':{'value':feed}}}])
    assert (await bridge.save_channel_podcast('https://www.youtube.com/@BlenderOfficial',command))['state']=='podcast_exists'
    assert command.await_count==1
    bridge._bridge_json=AsyncMock(return_value={'channel_id':'../../bad'})
    with pytest.raises(ValueError):await bridge.save_channel_podcast('url',command)
