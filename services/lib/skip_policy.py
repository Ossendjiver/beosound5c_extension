"""Recording-aware cooldowns for explicit skips; never infer a skip from a pause."""
import re
import unicodedata
from difflib import SequenceMatcher

DAY = 86400

def clean(value):
    return ' '.join(re.findall(r'\w+',unicodedata.normalize('NFKD',str(value or '')).casefold()))

def aliases(item):
    item=dict(item)
    artists=item.get('artists') or item.get('artist') or ''
    if isinstance(artists,list):
        names=[a.get('name','') if isinstance(a,dict) else str(a) for a in artists]
    else:names=re.split(r'\s*(?:/|,|\s&\s|\sfeat\.?\s|\sfeaturing\s)\s*',str(artists).split(" · ")[0])
    title=str(item.get('name') or item.get('title') or '')
    # Provider uploads often put the actual performer in "Artist - Title".
    parts=re.split(r'\s[-–—]\s',title,maxsplit=1)
    titles=[title]
    if len(parts)==2:
        names.append(parts[0]);titles.append(parts[1])
    return {(clean(a),clean(re.sub(r'\([^)]*\)|\[[^]]*\]','',t)))
            for a in names for t in titles if clean(a) and clean(t)}

def same(a,b):
    a,b=dict(a),dict(b)
    if a.get('uri') and a.get('uri')==b.get('uri'):return True
    return any(aa==ab and (ta==tb or SequenceMatcher(None,ta,tb).ratio()>=.88)
               for aa,ta in aliases(a) for ab,tb in aliases(b))

def recent(item,history,now):
    skips=[dict(row) for row in history if row['origin']!='legacy' and
           (dict(row).get('end_reason')=='skip' or float(row['reward'])==-2) and
           0 <= now-float(row['ts']) <= 7*DAY and same(item,row)]
    if not skips:return False,0.0
    age=now-max(float(r['ts']) for r in skips)
    cooldown=6*3600 if len(skips)==1 else 2*DAY if len(skips)==2 else 7*DAY
    penalty=min(60.0,sum(12*2**(-(now-float(r['ts']))/(2*DAY)) for r in skips))
    return age<cooldown,penalty
