"""Rank-weighted personal reference playlist counts, with a durable once-only ledger."""
import hashlib
import json
import sqlite3
import time
from collections import Counter


def weights(length):
    if length < 0: raise ValueError('Negative playlist length')
    if length < 2: return [1] * length
    denominator=length-1
    # Whole-play half-up interpolation: N+1 at the start, exactly 1 at the end.
    return [(2*((length+1)*denominator-length*i)+denominator)//(2*denominator)
            for i in range(length)]


def playlist_key(playlist):
    mappings=playlist.get('provider_mappings') or []
    identities=sorted({f"{m['provider_domain']}://playlist/{m['item_id']}" for m in mappings
                       if m.get('provider_domain') and m.get('item_id')})
    return identities[0] if identities else playlist['uri']


def resolve_track(db, track):
    if track.get('provider')=='library':
        row=db.execute('SELECT item_id FROM tracks WHERE item_id=?',(str(track['item_id']),)).fetchone()
        if row: return int(row[0])
    references={(str(track.get('provider') or ''),str(track.get('item_id') or ''))}
    for mapping in track.get('provider_mappings') or []:
        for p in (mapping.get('provider_instance'),mapping.get('provider_domain')):
            if p: references.add((str(p),str(mapping.get('item_id') or '')))
    ids=set()
    for provider,item_id in references:
        if provider and item_id:
            ids.update(int(r[0]) for r in db.execute(
                "SELECT item_id FROM provider_mappings WHERE media_type='track' AND provider_item_id=? AND (provider_instance=? OR provider_domain=?)",
                (item_id,provider,provider)))
    if len(ids)!=1: raise ValueError('Reference track missing or ambiguous in MA library')
    return ids.pop()


def prepare(db, playlists):
    result=[]
    for playlist,tracks in playlists:
        if not tracks: raise ValueError('Empty reference playlist; wait for MA resync')
        counts=Counter()
        for track,count in zip(tracks,weights(len(tracks))):
            if track.get('media_type')!='track': raise ValueError('Non-track reference item')
            counts[resolve_track(db,track)]+=count
        entries=sorted(counts.items())
        result.append({'key':playlist_key(playlist),'entries':entries,
                       'length':len(tracks),'plays':sum(counts.values()),
                       'digest':hashlib.sha256(json.dumps(entries,separators=(',',':')).encode()).hexdigest()})
    if len({p['key'] for p in result})!=len(result): raise ValueError('Duplicate reference playlist identity')
    return result


def apply(db, plan):
    """Counts and ledger commit together; repeated or concurrent imports are no-ops.

    Do not touch last_played, playlog, artists, or provider reporting.
    """
    db.execute('BEGIN IMMEDIATE')
    try:
        db.execute('CREATE TABLE IF NOT EXISTS homemedia_reference_play_imports (playlist_key TEXT PRIMARY KEY, digest TEXT NOT NULL, track_entries INTEGER NOT NULL, added_plays INTEGER NOT NULL, applied_at REAL NOT NULL)')
        applied=[];skipped=[];deltas=Counter()
        for playlist in plan:
            if db.execute('SELECT 1 FROM homemedia_reference_play_imports WHERE playlist_key=?',(playlist['key'],)).fetchone():
                skipped.append(playlist['key']);continue
            for item_id,count in playlist['entries']:
                if not isinstance(count,int) or count<1: raise ValueError('Invalid play delta')
                row=db.execute('SELECT play_count,last_played FROM tracks WHERE item_id=?',(item_id,)).fetchone()
                if row is None: raise ValueError('Reference track disappeared before import')
                db.execute('UPDATE tracks SET play_count=COALESCE(play_count,0)+? WHERE item_id=?',(count,item_id))
                check=db.execute('SELECT play_count,last_played FROM tracks WHERE item_id=?',(item_id,)).fetchone()
                if check[0]!=(row[0] or 0)+count or check[1]!=row[1]: raise ValueError('Count verification failed')
                deltas[item_id]+=count
            db.execute('INSERT INTO homemedia_reference_play_imports VALUES (?,?,?,?,?)',
                       (playlist['key'],playlist['digest'],playlist['length'],playlist['plays'],time.time()))
            applied.append(playlist['key'])
        db.commit()
        return {'applied_playlists':len(applied),'skipped_playlists':len(skipped),
                'updated_tracks':len(deltas),'added_plays':sum(deltas.values())}
    except BaseException:
        db.rollback();raise
