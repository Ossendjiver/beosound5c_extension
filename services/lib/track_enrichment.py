"""Conservative performer recovery and evidence-labelled SoundCloud mix genres."""
import re
import unicodedata
from collections import defaultdict
from . import radio_genres


def normal(value):
    value = unicodedata.normalize('NFKD', str(value or '')).casefold()
    return ' '.join(re.findall(r'\w+', ''.join(c for c in value if not unicodedata.combining(c))))


def names(value):
    if isinstance(value, dict): value = [value]
    if isinstance(value, str): value = [value]
    if not isinstance(value, (list, tuple, set)): return []
    return list(dict.fromkeys(str(v.get('name') or '') if isinstance(v, dict) else str(v)
                             for v in value if v and (not isinstance(v, dict) or v.get('name'))))


def performer_metadata(item):
    meta = item.get('metadata') if isinstance(item.get('metadata'), dict) else {}
    performers = names(item.get('performers') or meta.get('performers'))
    artist = names(item.get('artist')) or names(item.get('artists')) or performers
    result = {}
    if artist:
        result['artist'] = ', '.join(artist)
        result['artist_source'] = item.get('artist_source') or ('performer-credit' if not item.get('artist') and not names(item.get('artists')) else 'provider-artist')
    if performers: result['performers'] = performers
    # Composer/album artist/folder names are deliberately not performer fallbacks.
    return result


def soundcloud(item):
    return (str(item.get('uri') or item.get('url') or '').split('://')[0].split('--')[0] == 'soundcloud'
            or item.get('genre_source') == 'soundcloud-tags'
            or any(m.get('provider_domain') == 'soundcloud' for m in item.get('provider_mappings') or [] if isinstance(m, dict)))


def genre_metadata(item):
    meta = item.get('metadata') if isinstance(item.get('metadata'), dict) else {}
    fields = [item.get(k) or meta.get(k) for k in ('genres', 'genre', 'style', 'soundcloud_style_tags')]
    raw = []
    for field in fields:
        if isinstance(field, str):
            raw.extend(re.findall(r'"([^"]+)"', field))
            raw.extend(re.split(r'[,;]', re.sub(r'"[^"]+"', '', field)))
        elif isinstance(field, (list, tuple, set)): raw.extend(field)
    known = set().union(*radio_genres.FAMILIES.values())
    genres = sorted({radio_genres.normalize(v) for v in raw if isinstance(v, str)} & known)
    return {'genres': genres, 'genre_source': 'soundcloud-tags', 'soundcloud_style_tags': raw}


class ArtistGenres(dict):
    recordings = None


def artist_genres(items):
    """One vote per artist: favourites/play counts never amplify genre evidence."""
    index = defaultdict(set)
    recordings=defaultdict(dict)
    for item in items:
        tags = radio_genres.labels(item) & set().union(*radio_genres.FAMILIES.values())
        if not tags or item.get('inferred_genres'): continue
        credits = names(item.get('artists')) or names(item.get('artist'))
        for artist in credits:
            key=normal(artist);index[key].update(tags)
            title=normal(item.get('name') or item.get('title'))
            if title:recordings[key][title]=tags
    result=ArtistGenres(index);result.recordings=dict(recordings)
    return result


def uploader_consensus(item,index,result):
    # A weak fallback for poorly tagged long mixes, not a measured mix genre.
    artist=normal(', '.join(names(item.get('artist')) or names(item.get('artists'))))
    references=(getattr(index,'recordings',None) or {}).get(artist,{})
    references={name:tags for name,tags in references.items() if name!=normal(item.get('name') or item.get('title'))}
    if len(references)<3:return result
    votes=defaultdict(list)
    for name,tags in references.items():
        for tag in radio_genres.specific(tags):votes[tag].append(name)
    eligible={tag:names for tag,names in votes.items() if len(names)>=3 and len(names)/len(references)>=.75}
    if not eligible or len(radio_genres.dominant(set(eligible)))!=1:return result
    result['inferred_genres']=sorted(eligible)
    result['genre_evidence']={'source':'uploader-library-recordings',
        'confidence':min(len(names)/len(references) for names in eligible.values()),
        'artist':artist,'matched_recordings':len(references),'recordings':sorted(references)}
    return result


def mix_metadata(item, index):
    if not soundcloud(item): return {}
    result = genre_metadata(item)
    try: long_mix = float(item.get('duration') or 0) >= 1200
    except (ValueError, TypeError): long_mix = False
    if result['genres'] or not long_mix: return result
    # Exact quoted artist tags, plus exact names in the remaining style text.
    meta=item.get('metadata') if isinstance(item.get('metadata'),dict) else {}
    style=item.get('style') or meta.get('style') or item.get('soundcloud_style_tags') or []
    text=style if isinstance(style,str) else ' '.join(str(v) for v in style)
    normalized = ' '+normal(text)+' '
    evidence = {artist: tags for artist, tags in index.items()
                if artist and artist != normal(', '.join(names(item.get('artist')) or names(item.get('artists')))) and ' '+artist+' ' in normalized}
    if len(evidence) < 3: return uploader_consensus(item,index,result)
    votes = defaultdict(list)
    for artist, tags in evidence.items():
        for tag in radio_genres.specific(tags) | radio_genres.dominant(tags): votes[tag].append(artist)
    eligible = {tag: artists for tag, artists in votes.items()
                if len(artists) >= 3 and len(artists)/len(evidence) >= .75}
    if not eligible: return uploader_consensus(item,index,result)
    specific = set(eligible)-set(radio_genres.FAMILIES)
    if not specific and len(eligible)>1:
        ordered=sorted(eligible,key=lambda tag:len(eligible[tag]),reverse=True)
        if (len(eligible[ordered[0]])-len(eligible[ordered[1]]))/len(evidence)<.2:return uploader_consensus(item,index,result)
        eligible={ordered[0]:eligible[ordered[0]]}
    chosen = sorted(specific or set(eligible))
    result['inferred_genres'] = chosen
    result['genre_evidence'] = {'source': 'soundcloud-style-library-consensus',
        'confidence': min(len(eligible[tag])/len(evidence) for tag in chosen),
        'artists': sorted(evidence), 'matched_artists': len(evidence)}
    return result


def cached_performers(tree):
    """Recover explicit leaf credits from artist/album exports by exact URI only."""
    credits = defaultdict(dict)
    def walk(node):
        if isinstance(node, list):
            for child in node: walk(child)
        elif isinstance(node, dict):
            uri = str(node.get('uri') or node.get('url') or '')
            if '://track/' in uri and not isinstance(node.get('tracks'), list):
                metadata = performer_metadata(node)
                if metadata.get('artist', '').strip():
                    credits[uri][normal(metadata['artist'])] = metadata
            for key in ('tracks', 'items', 'children'): walk(node.get(key))
    walk(tree)
    # Conflicting credits need an authoritative repair, not a guessed performer.
    return {uri: dict(next(iter(values.values())), artist_source='cached-exact-uri')
            for uri, values in credits.items() if len(values) == 1}
