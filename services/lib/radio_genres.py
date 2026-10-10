"""Specific and broad genre fallback for seed-based radio, independent of popularity."""
import re

FAMILIES = {
 'classical': {'classical','chamber music','orchestral','opera','baroque','romantic classical','modern classical','contemporary classical','solo piano','cello','string quartet'},
 'jazz': {'spiritual jazz','jazz funk','latin jazz','nu jazz','jazz','bebop','hard bop','cool jazz','modal jazz','free jazz','vocal jazz','jazz fusion','smooth jazz'},
 'electronic': {'drone','acid house','acid techno','dub techno','minimal techno','detroit techno','ambient techno','microhouse','breakbeat','jungle','liquid funk','chillwave','nu disco','synthwave','new age','leftfield','future jazz','broken beat','balearic','electronic','electronica','house','deep house','tech house','progressive house','techno','ambient','downtempo','idm','drum and bass','dubstep','trance','garage','uk garage','folktronica','trip hop','electro'},
 'rock': {'shoegaze','new wave','grunge','noise rock','garage rock','rock','alternative rock','indie rock','classic rock','progressive rock','psychedelic rock','hard rock','punk','post punk','post rock'},
 'pop': {'pop','indie pop','synth pop','electropop','dream pop','art pop','dance pop','teen pop','europop','bubblegum pop'},
 'hip hop': {'hip hop','rap','trap','conscious hip hop','alternative hip hop','jazzy hip hop'},
 'soul': {'disco','boogie','p funk','soul','neo soul','rnb','rhythm and blues','funk','motown','contemporary rnb','alternative rnb'},
 'folk': {'folk','folk rock','indie folk','singer songwriter','acoustic','folktronica'},
 'metal': {'metal','heavy metal','death metal','black metal','doom metal','progressive metal'},
 'country': {'country','americana','bluegrass','alt country'},
 'reggae': {'reggae','dub','dancehall','ska'},
}
ALIASES={'r and b':'rnb','r b':'rnb','r&b':'rnb','hiphop':'hip hop','classical music':'classical','dnb':'drum and bass','hip hop rap':'rap','funk soul':'soul','r&b soul':'rnb','r b soul':'rnb','rnb soul':'rnb','rhythm & blues':'rnb'}

def normalize(value):
    value=str(value).strip().casefold().replace('r&b','rnb')
    value=re.sub(r'[-_/]+',' ',value)
    value=re.sub(r'\s+',' ',value)
    return ALIASES.get(value,value)

def classical_work(item):
    name=str(item.get('name') or item.get('title') or '')
    return bool(re.search(r'\b(?:BWV\s*\d+|Op\.?\s*\d+|K\.\s*\d+|(?:Concerto|Sonata|Symphony)\s+(?:No\.?\s*\d+|for\s|in\s))',name,re.I))


def labels(item):
    from . import genre_style
    metadata=item.get('metadata') if isinstance(item.get('metadata'),dict) else {}
    values=[]
    for source in (item,metadata):
        for key in ('genres','genre','style'):
            value=source.get(key) or []
            values.extend(re.split(r'[,;]',value) if isinstance(value,str) else value if isinstance(value,list) else [])
    evidence=item.get('genre_evidence') or metadata.get('genre_evidence') or {}
    if isinstance(evidence,dict) and evidence.get('source') in ('soundcloud-style-library-consensus','uploader-library-recordings') and isinstance(evidence.get('confidence'),(int,float)) and .75<=evidence['confidence']<=1 and isinstance(evidence.get('matched_artists') if evidence.get('source')=='soundcloud-style-library-consensus' else evidence.get('matched_recordings'),int) and (evidence.get('matched_artists',0) if evidence.get('source')=='soundcloud-style-library-consensus' else evidence.get('matched_recordings',0))>=3:
        inferred=item.get('inferred_genres') or metadata.get('inferred_genres') or []
        if isinstance(inferred,list):values.extend(inferred)
    if genre_style.accepted(evidence):
        inferred=item.get('inferred_genres') or metadata.get('inferred_genres') or []
        if isinstance(inferred,list):values.extend(inferred)
    result={normalize(v) for v in values if isinstance(v,str) and v.strip()}
    if 'conscious' in result and ('hip hop' in result or 'rap' in result):result.add('conscious hip hop')
    name=str(item.get('name') or item.get('title') or '')
    # Explicit catalogue/work identifiers provide repertoire evidence when tags are absent.
    if classical_work(item):
        result.add('classical')
        if re.search(r'\bcello\b',name,re.I) and re.search(r'\bpiano\b',name,re.I):result.add('chamber music')
    return result

def families(tags):
    return {family for family,children in FAMILIES.items() if tags & children}

def specific(tags):
    return tags & (set().union(*FAMILIES.values())-FAMILIES.keys())

def dominant(tags):
    strong=specific(tags)
    counts={family:len(strong & children) for family,children in FAMILIES.items()}
    maximum=max(counts.values(),default=0)
    return {family for family,n in counts.items() if n==maximum and n} if maximum else families(tags)

def fallback(item,root):
    a,b=labels(item),labels(root)
    shared=specific(a)&specific(b)
    if shared:
        coverage=len(shared)/max(1,len(specific(b)))
        return 1.1+.2*(1-coverage)
    if dominant(a)&dominant(b):
        detail=style_distance(a,b)
        if detail==3:return None
        return 1.32 if detail==1 else 1.38 if detail==2 else 1.4
    return None

def conflicting(item,root):
    a,b=dominant(labels(item)),dominant(labels(root))
    if len(b)==1 and len(a)>1:
        # Mixed album-level tags need specific corroboration of the seed style.
        if not specific(labels(item))&specific(labels(root)):
            if b!={'classical'} or not classical_work(item):return True
    if a & b and style_distance(labels(item),labels(root))==3:return True
    return bool(a and b and not a&b)

# Specific styles are closer evidence than a catch-all Electronic/Rock label.
STYLE_GROUPS = {
    'club': {'house','deep house','acid house','tech house','progressive house','microhouse','techno','minimal techno','detroit techno','acid techno','electro'},
    'atmospheric': {'ambient','ambient techno','dub techno','new age','drone'},
    'downbeat': {'downtempo','trip hop','balearic','chillwave','broken beat','future jazz','nu jazz','folktronica'},
    'bass': {'drum and bass','jungle','liquid funk','dubstep','uk garage','garage','breakbeat'},
    'soulful': {'soul','neo soul','rnb','alternative rnb','contemporary rnb','funk','jazz funk','motown','boogie','disco','nu disco'},
    'guitar': {'indie rock','alternative rock','classic rock','garage rock','grunge','hard rock'},
    'textural': {'shoegaze','dream pop','post rock','psychedelic rock','noise rock'},
    'acoustic': {'folk','indie folk','acoustic','singer songwriter','americana','alt country'},
}
STYLE_BRIDGES = {frozenset(x) for x in [('club','bass'),('atmospheric','downbeat'),('downbeat','soulful'),('guitar','textural'),('guitar','acoustic')]}


def style_distance(a, b):
    """Unknown detail remains unknown; broad metadata cannot claim a precise match."""
    sa,sb=specific(a),specific(b)
    if sa & sb:return 0
    ga={g for g,tags in STYLE_GROUPS.items() if a & tags}
    gb={g for g,tags in STYLE_GROUPS.items() if b & tags}
    if not ga or not gb:return None
    if ga & gb:return 1
    if any(frozenset((x,y)) in STYLE_BRIDGES for x in ga for y in gb):return 2
    return 3
