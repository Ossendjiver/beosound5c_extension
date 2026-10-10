"""Specific and broad genre fallback for seed-based radio, independent of popularity."""
import re

FAMILIES = {
 'classical': {'classical','chamber music','orchestral','opera','baroque','romantic classical','modern classical','contemporary classical','solo piano','cello','string quartet'},
 'jazz': {'jazz','bebop','hard bop','cool jazz','modal jazz','free jazz','vocal jazz','jazz fusion','smooth jazz'},
 'electronic': {'electronic','electronica','house','deep house','tech house','progressive house','techno','ambient','downtempo','idm','drum and bass','dubstep','trance','garage','uk garage','folktronica','trip hop','electro'},
 'rock': {'rock','alternative rock','indie rock','classic rock','progressive rock','psychedelic rock','hard rock','punk','post punk','post rock'},
 'pop': {'pop','indie pop','synth pop','electropop','dream pop','art pop','dance pop','teen pop','europop','bubblegum pop'},
 'hip hop': {'hip hop','rap','trap','conscious hip hop','alternative hip hop','jazzy hip hop'},
 'soul': {'soul','neo soul','rnb','rhythm and blues','funk','motown','contemporary rnb','alternative rnb'},
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
    metadata=item.get('metadata') if isinstance(item.get('metadata'),dict) else {}
    values=[]
    for source in (item,metadata):
        for key in ('genres','genre','style'):
            value=source.get(key) or []
            values.extend(re.split(r'[,;]',value) if isinstance(value,str) else value if isinstance(value,list) else [])
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
    if dominant(a)&dominant(b):return 1.4
    return None

def conflicting(item,root):
    a,b=dominant(labels(item)),dominant(labels(root))
    if len(b)==1 and len(a)>1:
        # Mixed album-level tags need specific corroboration of the seed style.
        if not specific(labels(item))&specific(labels(root)):
            if b!={'classical'} or not classical_work(item):return True
    return bool(a and b and not a&b)
