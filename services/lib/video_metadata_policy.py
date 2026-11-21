"""Never use a source label or an unverified forecast as a music-video query."""
import re
import time

PLACEHOLDERS={'','-','—','–','unknown','unknown artist','unknown title','unknown track',
    'aux','a.aux','a aux','aaux','auxiliary','analog','analogue','line in','line-in',
    'line input','cd','tape','phono','vinyl','no metadata','not available','unavailable','none'}

def valid(value):
    text=str(value or '').strip().casefold()
    return (text not in PLACEHOLDERS and not re.fullmatch(r'(?:track|disc|cd|tape|phono)\s*\d+',text)
        and not re.fullmatch(r'(?:a[.\s]?)?aux(?:iliary)?\s*\d*',text))

def allowed(payload):
    if not isinstance(payload,dict):return False
    if payload.get('metadata_available') is False or payload.get('predicted') is True:return False
    if not valid(payload.get('title')) or not valid(payload.get('artist')):return False
    expiry=payload.get('metadata_expires_at')
    if isinstance(expiry,(float,int)) and expiry<=time.time():return False
    return True

def sanitize(payload):
    result=dict(payload)
    ok=allowed(result);result['music_video_allowed']=ok
    if not ok:result.update(canvas_url='',music_video_url='')
    return result
