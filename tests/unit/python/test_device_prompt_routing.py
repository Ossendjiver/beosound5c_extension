import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import library
class Reply:
    status=200
    def __init__(self,body):self.body=body
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass
    async def json(self,**kw):return self.body
class Http:
    def __init__(self):self.calls=[]
    def get(self,url):return Reply({'transfer_targets':[{'id':'wiim_uuid:target','name':'Link'}]})
    def post(self,url,**kw):
        self.calls.append((url,kw));return Reply({'queue_id':'wiim_uuid:target','status':'ok'})
def test_music_prompt_selects_bs3_link_and_scopes_every_queue_write():
    s=SimpleNamespace(session=Http(),cfg={},recommend_music=AsyncMock(return_value=[{'uri':'library://song'}]),mixes=SimpleNamespace(begin=AsyncMock()))
    asyncio.run(library.LibraryService._play_music_local(s,True))
    assert s.session.calls[0][1]['json']=={'entity_id':'media_player.bs3','source':'LINK'}
    assert s.session.calls[1][1]['json']['queue_id']=='wiim_uuid:target'
    assert s.session.calls[1][1]['json']['target_player_id']=='wiim_uuid:target'
def test_news_prompt_uses_only_lounge_script_not_router_selected_player():
    s=SimpleNamespace(session=Http())
    asyncio.run(library.LibraryService._play_news_local(s,True))
    assert s.session.calls==[('http://127.0.0.1:8776/action',{'json':{'action':'play_news_lounge'}})]
