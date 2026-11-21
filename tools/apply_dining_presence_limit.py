"""Patch the measured-volume HA adapter without replacing local customizations.

Requires the deployed read_active_volume/_set_measured_level implementation;
refuses incompatible versions and is idempotent. Back up the file first.
"""
from pathlib import Path
import sys

def patched(text):
    if 'self._presence_limit = PresenceLimit(' in text:return text
    edits=[
        ('from .base import VolumeAdapter','from .base import VolumeAdapter\nfrom .presence_limit import PresenceLimit'),
        ('        self._ha_token = os.getenv("HA_TOKEN", "").strip()',
         '        self._ha_token = os.getenv("HA_TOKEN", "").strip()\n        self._presence_limit = PresenceLimit(session, self._ha_api_base, self._headers, max_volume)'),
        ('        return level * 100 if level is not None else None',
         '        if level is not None:\n            self._max_volume = await self._presence_limit.limit(target)\n        return level * 100 if level is not None else None'),
        ('        limit = max(level, self._max_volume / 100)',
         '        limit = max(level, (await self._presence_limit.limit(target)) / 100)')]
    for before,after in edits:
        if text.count(before)!=1:raise ValueError('Incompatible HA volume adapter; no files changed')
        text=text.replace(before,after,1)
    compile(text,'hass.py','exec')
    return text

if __name__=='__main__':
    path=Path(sys.argv[1]);source=path.read_text();updated=patched(source)
    if source!=updated:path.write_text(updated)
    print('Dining presence policy installed; other adapter code preserved')
