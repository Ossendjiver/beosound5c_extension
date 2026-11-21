"""Scoped, idempotent hooks for the installed Home Media/Showing/router code."""
from pathlib import Path
import sys

def replace_once(text,before,after):
    if after in text:return text
    if text.count(before)!=1:raise ValueError('Incompatible live source; no files changed')
    return text.replace(before,after,1)

def patches(root):
    library=root/'services/library.py';router=root/'services/router.py';source=root/'services/input.py'
    result={}
    text=library.read_text(encoding='utf-8')
    text=replace_once(text,'from lib import music_familiarity','from lib import music_familiarity\nfrom lib import ma_history_sync')
    text=replace_once(text,'self._background.spawn(self._familiarity_loop(), name="library_familiarity_sync")',
        'self._background.spawn(ma_history_sync.run_hourly(self), name="library_familiarity_sync")')
    text=replace_once(text,'evidence, status = await music_familiarity.collect(command, self.model.get_kv("music_familiarity", {}))',
        'evidence, status = await ma_history_sync.collect(command, self.model.get_kv("music_familiarity", {}), self.model, music_familiarity)')
    result[library]=text
    text=source.read_text(encoding='utf-8')
    text=replace_once(text,"    artist = attrs.get('media_artist') or attrs.get('media_series_title') or app_name or '—'",
        "    metadata_available = bool(str(attrs.get('media_title') or '').strip() and str(attrs.get('media_artist') or '').strip())\n    artist = attrs.get('media_artist') or attrs.get('media_series_title') or app_name or '—'")
    text=replace_once(text,"        'supported_features': attrs.get('supported_features', 0),",
        "        'supported_features': attrs.get('supported_features', 0),\n        'metadata_available': metadata_available,")
    text=replace_once(text,"        'title': showing_media.get('title') or '—',",
        "        'metadata_available': showing_media.get('metadata_available', False),\n        'title': showing_media.get('title') or '—',")
    result[source]=text
    text=router.read_text(encoding='utf-8')
    # Unique module import anchor, safe alongside the router's other imports.
    text=replace_once(text,'import asyncio','import asyncio\nfrom lib import video_metadata_policy')
    text=replace_once(text,'        mv_artist = payload.get("artist", "").strip()',
        '        payload = video_metadata_policy.sanitize(payload)\n        if not payload["music_video_allowed"]:\n            self._music_video_generation += 1\n            self._music_video_pending_key = ""\n        mv_artist = payload.get("artist", "").strip()')
    text=replace_once(text,'        if (self._music_video_client\n                and mv_artist and mv_title',
        '        if (self._music_video_client\n                and payload.get("music_video_allowed")\n                and mv_artist and mv_title')
    text=replace_once(text,'        current["music_video_url"] = url',
        '        if not video_metadata_policy.allowed(current):\n            _clear_pending()\n            return\n        current["music_video_url"] = url')
    text=replace_once(text,'        current["canvas_url"] = canvas_url',
        '        if not video_metadata_policy.allowed(current):\n            return\n        current["canvas_url"] = canvas_url')
    result[router]=text
    for path,text in result.items():compile(text,str(path),'exec')
    return result

if __name__=='__main__':
    root=Path(sys.argv[1]);updates=patches(root)
    for path,text in updates.items():
        if path.read_text(encoding='utf-8')!=text:path.write_text(text,encoding='utf-8')
    print('Installed hourly history and metadata video guards; other source preserved')
