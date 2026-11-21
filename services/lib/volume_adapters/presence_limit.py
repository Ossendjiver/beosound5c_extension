"""Dining-only home-presence exemption. Changes a ceiling, never hardware."""
import asyncio
import os
import time


class PresenceLimit:
    def __init__(self,session,api_base,headers,configured_limit):
        self.session=session;self.api_base=api_base;self.headers=headers
        self.configured=configured_limit
        self.enabled=os.getenv('BS5C_DINING_HOME_EXEMPT','0')=='1'
        self.checked=-1e9;self.state=None

    async def limit(self,target):
        if not self.enabled or target not in {'media_player.dining','media_player.dining_mc','media_player.dining_3'}:
            return self.configured
        now=time.monotonic()
        if now-self.checked>=2:
            self.checked=now;self.state=None
            try:
                async with asyncio.timeout(1):
                    async with self.session.get(self.api_base+'/states/person.thomas',headers=self.headers()) as response:
                        if response.status==200:self.state=(await response.json()).get('state')
            except Exception:pass
        return 100 if self.state=='home' else self.configured
