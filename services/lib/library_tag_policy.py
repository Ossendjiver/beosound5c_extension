"""Conservative album-artist grouping without rewriting performer credits."""
import re,unicodedata

def key(value):return ''.join(c for c in unicodedata.normalize('NFKD',str(value)).casefold() if c.isalnum())
CANONICAL={
 'Ludwig van Beethoven':['Beethoven','L. van Beethoven','Ludwig Van Beethoven'],
 'Johann Sebastian Bach':['J. S. Bach','J.S. Bach','Johann Sebastien Bach'],
 'Wolfgang Amadeus Mozart':['Mozart','W. A. Mozart','W.A. Mozart'],
 'George Frideric Handel':['Georg Friedrich Händel','George Frederick Handel','George Freidrich Handel','Handel','Händel'],
 'Pyotr Ilyich Tchaikovsky':['Peter Ilyich Tchaikovsky','Peter Iljitsch Tschaikowsky','Piotr Ilyich Tchaikovsky','Tchaikovsky','Tschaikowsky'],
 'Arvo Pärt':['Arvo Part'], 'Carl Orff':[], 'Franz Schubert':[], 'Frédéric Chopin':['Frederic Chopin','Chopin'],
 'Johannes Brahms':['Brahms'], 'Claude Debussy':['Debussy'], 'Maurice Ravel':['Ravel'],
 'Antonio Vivaldi':['Vivaldi'], 'Joseph Haydn':['Franz Joseph Haydn','Haydn'],
 'Gustav Mahler':['Mahler'], 'Richard Wagner':['Wagner'], 'Felix Mendelssohn':['Felix Mendelssohn Bartholdy'],
 'Sergei Rachmaninoff':['Sergei Rachmaninov'], 'Dmitri Shostakovich':[], 'Igor Stravinsky':[],
 'Jean Sibelius':[], 'Camille Saint-Saëns':['Camille Saint-Saens'], 'Robert Schumann':[],
 'Antonín Dvořák':['Antonin Dvorak'], 'Edvard Grieg':[], 'Giuseppe Verdi':[], 'Giacomo Puccini':[],
 'Benjamin Britten':[], 'Edward Elgar':[], 'Ralph Vaughan Williams':[], 'Philip Glass':[], 'Steve Reich':[],
}
ALIASES={key(alias):name for name,aliases in CANONICAL.items() for alias in [name]+aliases}

def canonical(value):return ALIASES.get(key(value),str(value).strip())

def classical_composers(tags):
 genres=' '.join(tags.get('genre',[])).casefold()
 known=[]
 for value in tags.get('composer',[])+tags.get('artist',[])+tags.get('albumartist',[]):
  # Composer prefixes in established classical release credits are explicit.
  prefix=value.split(';',1)[0].strip()
  if key(prefix) in ALIASES:known.append(ALIASES[key(prefix)])
 classical=bool(known) or bool(re.search(r'\b(classical|opera|baroque|chamber|orchestral)\b',genres))
 if not classical:return []
 explicit=[]
 for value in tags.get('composer',[]):
  # Only semicolons, the standard multi-value separator. Never split commas/&.
  explicit.extend(canonical(v) for v in value.split(';') if key(v) not in {'', 'various', 'variousartists', 'unknown', 'unknowncomposer', 'traditional', 'anonymous', 'anon', 'na'})
 values=explicit or known
 return list(dict.fromkeys(values))

def provider_album_artist(details):
 artist=details.get('artist') or {}
 name=artist.get('name') if isinstance(artist,dict) else None
 return str(name).strip() if name and str(name).strip() else None

def folder_artist(folder,credits):
 # Membership check, not a rewrite/split of genuine band names.
 for credit in credits:
  if key(folder)==key(credit):return credit
  if any(key(folder)==key(part.strip()) for part in credit.split(',')):return folder
 return None

def register_easy_mp4_keys():
    """Expose native composer and retained performer credits in EasyMP4 views."""
    from mutagen.easymp4 import EasyMP4Tags
    EasyMP4Tags.RegisterTextKey('composer', '\xa9wrt')
    EasyMP4Tags.RegisterFreeformKey('performer', 'PERFORMER')
    EasyMP4Tags.RegisterFreeformKey('albumartists', 'ALBUMARTISTS')
    EasyMP4Tags.RegisterFreeformKey('musicbrainz_albumartistid', 'MusicBrainz Album Artist Id')
    EasyMP4Tags.RegisterFreeformKey('musicbrainz_artistid', 'MusicBrainz Artist Id')

def album_identity_updates(tags, target, original_tags, verified_artist_ids):
    """Keep a changed album-artist name aligned with its role-specific IDs."""
    updates = {}
    if tags.get('albumartists') and tags['albumartists'] != [target]:
        updates['albumartists'] = [target]
    for field in ('musicbrainz_albumartistid', 'musicbrainzalbumartistid'):
        old_ids = tags.get(field)
        if not old_ids:
            continue
        ids = verified_artist_ids.get(key(canonical(target)))
        if not ids:
            old_names = original_tags.get('albumartist', [])
            if len(old_names) == 1 and key(canonical(old_names[0])) == key(canonical(target)) and len(old_ids) == 1:
                continue
            ids = None  # Unverified old role IDs must not identify the new name.
        if old_ids != ids:
            updates[field] = ids
    return updates
