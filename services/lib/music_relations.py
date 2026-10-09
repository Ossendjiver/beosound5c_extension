"""Personal playlist overlap and directional listening evidence, never mood labels."""
import hashlib
import math
import re
import unicodedata
from collections import defaultdict

RETENTION = 180*86400

def normal(value):
    value=unicodedata.normalize('NFKD',str(value or '')).casefold()
    return ' '.join(re.findall(r'\w+',''.join(c for c in value if not unicodedata.combining(c))))

def key(item):
    item=dict(item)
    artist=item.get('artist') or item.get('artists') or ''
    if isinstance(artist,list):
        artist=artist[0] if artist else ''
        artist=artist.get('name','') if isinstance(artist,dict) else artist
    title=str(item.get('name') or item.get('title') or '')
    version=str(item.get('version') or '')
    if version and normal(version) not in normal(title):title+=' '+version
    artist=normal(str(artist).split(' · ')[0]);title=normal(title)
    # Keep version/remix words; fuzzy artist/title guesses are not recording IDs.
    return hashlib.sha256((artist+'\0'+title).encode()).hexdigest() if artist and title else ''


class Playlists:
    """An inverted index avoids generating millions of all-pairs edges."""
    def __init__(self, raw=(), items=()):
        self.aliases={i['uri']:key(i) for i in items if i.get('uri') and key(i)}
        self.memberships=defaultdict(set);self.sizes={};self._weights={}
        def walk(node, inherited_artist=''):
            if not isinstance(node,dict):return
            children=node.get('tracks') or []
            artist=node.get('artist') or inherited_artist
            is_playlist=node.get('media_type')=='playlist' or str(node.get('id','')).startswith('playlist:')
            if is_playlist and isinstance(children,list):
                name=normal(node.get('name'))
                if name in ('recently played','listening history','play history'):return
                members=set()
                for child in children:
                    if not isinstance(child,dict) or isinstance(child.get('tracks'),list):continue
                    if child.get('media_type','track') not in ('track','song','music'):continue
                    uri=child.get('uri') or child.get('url')
                    identity=self.aliases.get(uri) or key(dict(child,artist=child.get('artist') or artist))
                    if identity:
                        members.add(identity)
                        if uri:self.aliases.setdefault(uri,identity)
                if len(members)>1:
                    identifier=str(node.get('uri') or node.get('url') or node.get('id') or hashlib.sha256((name+'|'+','.join(sorted(members))).encode()).hexdigest())
                    # Duplicate exports of one playlist do not create extra evidence.
                    if identifier not in self.sizes:
                        self.sizes[identifier]=len(members)
                        for identity in members:self.memberships[identity].add(identifier)
            else:
                for child in children if isinstance(children,list) else []:walk(child,artist)
        for node in raw if isinstance(raw,list) else []:
            if isinstance(node,dict) and node.get('id')=='playlists':walk(node)
        self._weights={p:1/(size-1) for p,size in self.sizes.items()}

    def identity(self,item):
        return self.aliases.get(dict(item).get('uri')) or key(item)

    def overlap(self,a,b):
        left,right=self.identity(a),self.identity(b)
        if not left or not right or left==right:return 0.0
        x,y=self.memberships.get(left,set()),self.memberships.get(right,set())
        if not x or not y:return 0.0
        # Inverse playlist size plus membership normalization prevents large
        # "all favourites" lists and prolific playlist membership from dominating.
        return min(1.0,sum(self._weights[p] for p in x&y)/math.sqrt(len(x)*len(y)))


def evidence(previous,current,resolver=key):
    """Only positive deliberate destinations or explicitly reported skips learn."""
    if not previous:return None
    previous,current=dict(previous),dict(current)
    if not current.get('room') or previous.get('room')!=current['room']:return None
    a,b=resolver(previous),resolver(current)
    if not a or not b or a==b or float(previous.get('reward',0))<=0:return None
    gap=float(current['ts'])-float(previous['ts'])-float(current['seconds'])
    if not -60<=gap<=600:return None
    if current.get('end_reason')=='skip' and current.get('origin')!='legacy':
        return a,b,-1,1.0 if float(current['seconds'])<90 else .5
    if current.get('origin')=='manual' and float(current.get('reward',0))>=1:
        return a,b,1,1.0
    return None


def record(db,previous,current,resolver=key):
    edge=evidence(previous,current,resolver)
    if edge:
        a,b,sign,weight=edge
        db.execute('INSERT OR IGNORE INTO track_relations(source_id,target_id,source_key,target_key,room,ts,sign,weight) VALUES(?,?,?,?,?,?,?,?)',
                   (previous['id'],current['id'],a,b,current['room'],current['ts'],sign,weight))


def predecessor(rows,current,resolver=key):
    """Ignore only duplicate observer finishes, never a separate replay."""
    for previous in rows:
        if resolver(previous)==resolver(current):
            if abs(previous['ts']-current['ts'])<=20 and abs(previous['seconds']-current['seconds'])<=15:
                continue
            return None
        return previous
    return None


def backfill(db,resolver=key):
    """Old unknown/automatic destination histories cannot become positive edges."""
    previous={}
    for row in db.execute('SELECT * FROM listens ORDER BY ts,id'):
        room=row['room']
        rows=previous.get(room,[])
        record(db,predecessor(reversed(rows),row,resolver),row,resolver)
        previous[room]=(rows+[row])[-6:]


class Prior:
    def __init__(self,playlists,rows,room,now):
        self.playlists=playlists;self.pairs=defaultdict(lambda:defaultdict(lambda:[0.0,0.0]));self.totals=defaultdict(float)
        for row in rows:
            row=dict(row);age=now-float(row['ts'])
            if not 0<=age<=RETENTION:continue
            amount=float(row['weight'])*.5**(age/(60*86400))*(1 if row['room']==room else .5)
            position=0 if row['sign']==1 else 1
            self.pairs[row['source_key']][row['target_key']][position]+=amount
            if position==0:self.totals[row['source_key']]+=amount

    def components(self,root,item):
        a,b=self.playlists.identity(root),self.playlists.identity(item)
        if not a or not b or a==b:return 0.0,0.0,0.0
        positive,negative=self.pairs.get(a,{}).get(b,[0,0])
        playlist=.75*self.playlists.overlap(root,item)
        transition=1.25*positive/(self.totals.get(a,0)+3)
        # A directional aversion survives the general recent-skip cooldown.
        penalty=min(8.0,3*negative)*negative/(positive+negative+1)
        return playlist,transition,penalty

    def boost(self,root,item):
        playlist,transition,penalty=self.components(root,item)
        return playlist+transition-penalty
