"""Specific and broad genre fallback for seed-based radio, independent of popularity."""
import re

FAMILIES = {
 'classical': {'classical','chamber music','orchestral','opera','baroque','romantic classical','modern classical','contemporary classical','solo piano','cello','string quartet'},
 'jazz': {'jazz','bebop','hard bop','cool jazz','modal jazz','free jazz','vocal jazz','jazz fusion','smooth jazz'},
 'electronic': {'electronic','electronica','house','deep house','tech house','progressive house','techno','ambient','downtempo','idm','drum and bass','dubstep','trance','garage','uk garage'},
 'rock': {'rock','alternative rock','indie rock','classic rock','progressive rock','psychedelic rock','hard rock','punk','post punk','post rock'},
 'pop': {'pop','indie pop','synth pop','electropop','dream pop','art pop'},
 'hip hop': {'hip hop','rap','trap','conscious hip hop','alternative hip hop'},
 'soul': {'soul','neo soul','rnb','rhythm and blues','funk','motown'},
 'folk': {'folk','folk rock','indie folk','singer songwriter','acoustic'},
 'metal': {'metal','heavy metal','death metal','black metal','doom metal','progressive metal'},
 'country': {'country','americana','bluegrass','alt country'},
 'reggae': {'reggae','dub','dancehall','ska'},
}
ALIASES={'r and b':'rnb','r b':'rnb','r&b':'rnb','hiphop':'hip hop','classical music':'classical','dnb':'drum and bass'}

def normalize(value):
    value=re.sub(r'[-_/]+',' ',str(value).strip().casefold())
    value=re.sub(r'\s+',' ',value)
    return ALIASES.get(value,value)

def labels(item):
    metadata=item.get('metadata') if isinstance(item.get('metadata'),dict) else {}
    values=[]
    for source in (item,metadata):
        for key in ('genres','genre','style'):
            value=source.get(key) or []
            values.extend(re.split(r'[,;]',value) if isinstance(value,str) else value if isinstance(value,list) else [])
    result={normalize(v) for v in values if isinstance(v,str) and v.strip()}
    name=str(item.get('name') or item.get('title') or '')
    # Explicit catalogue/work identifiers provide repertoire evidence when tags are absent.
    if re.search(r'\b(?:BWV\s*\d+|Op\.?\s*\d+|K\.\s*\d+|Concerto|Sonata|Symphony)\b',name,re.I):
        result.add('classical')
        if re.search(r'\bcello\b',name,re.I) and re.search(r'\bpiano\b',name,re.I):result.add('chamber music')
    return result

def families(tags):
    return {family for family,children in FAMILIES.items() if tags & children}

def fallback(item,root):
    a,b=labels(item),labels(root)
    # Broad family labels alone are not evidence of the same specific style.
    if (a & b)-FAMILIES.keys():return 1.1
    if families(a)&families(b):return 1.4
    return None

def conflicting(item,root):
    a,b=families(labels(item)),families(labels(root))
    if b=={'classical'} and 'classical' in a and len(a)>1:
        # Mixed album-level tags must not make pop/soul albums classical radio.
        name=str(item.get('name') or item.get('title') or '')
        if not re.search(r'\b(?:BWV\s*\d+|Op\.?\s*\d+|K\.\s*\d+|Concerto|Sonata|Symphony)\b',name,re.I):return True
    return bool(a and b and not a&b)
