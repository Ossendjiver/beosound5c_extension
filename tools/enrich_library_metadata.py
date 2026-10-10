#!/usr/bin/env python3
"""Read-only MA/local-file performer repair; atomic SSD sidecar, no database edits."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services'))
from lib import track_enrichment as enrichment, music_features, radio_genres, provider_profiles
from profile_music_library import api_client, catalogue
from analyze_music_audio import atomic_write


def file_credits(item, root, probe=subprocess.run):
    root=Path(root).resolve(strict=True)
    for mapping in item.get('provider_mappings') or []:
        if mapping.get('provider_domain')!='filesystem_local' or not mapping.get('available',True):continue
        path=(root/str(mapping.get('item_id') or '')).resolve()
        if not path.is_relative_to(root) or not path.is_file():continue
        result=probe(['ffprobe','-v','error','-show_entries','format_tags=artist,performer,composer',
                      '-of','json',str(path)],capture_output=True,text=True,timeout=8,check=True)
        if len(result.stdout)>65536:raise ValueError('Oversized file tags')
        tags={k.casefold():v for k,v in (json.loads(result.stdout).get('format',{}).get('tags') or {}).items()}
        artist=tags.get('performer') or tags.get('artist')
        # Classical ARTIST is frequently the composer; never advertise it as a recovered performer.
        if not tags.get('performer') and (enrichment.normal(artist)==enrichment.normal(tags.get('composer')) or radio_genres.classical_work(item) and not tags.get('composer')):
            continue
        if artist and str(artist).strip():
            return {'artist':str(artist).strip(),'artist_source':'embedded-performer' if tags.get('performer') else 'embedded-artist',
                    **({'performers':[str(tags['performer'])]} if tags.get('performer') else {})}
    return {}


def enrich(items, tree, root, previous=None, max_files=250, probe=subprocess.run):
    payload={'version':1,'tracks':dict((previous or {}).get('tracks') or {})}
    cached=enrichment.cached_performers(tree);index=enrichment.artist_genres(items)
    stats={'cached_performers':0,'embedded_performers':0,'unresolved_performers':0,'inferred_mixes':0,'file_errors':0}
    inspected=0
    for item in items:
        uri=item.get('uri')
        if not uri:continue
        metadata={}
        if not music_features.artist_name(item).strip():
            metadata=cached.get(uri,{})
            if metadata:stats['cached_performers']+=1
            elif inspected<max_files and any(m.get('provider_domain')=='filesystem_local' for m in item.get('provider_mappings') or []):
                inspected+=1
                try:metadata=file_credits(item,root,probe)
                except (OSError,ValueError,subprocess.SubprocessError):stats['file_errors']+=1
                if metadata:stats['embedded_performers']+=1
            if not metadata:stats['unresolved_performers']+=1
        mix=enrichment.mix_metadata(item,index)
        if mix:
            metadata.update(mix)
            if mix.get('inferred_genres'):stats['inferred_mixes']+=1
        if metadata:
            aliases={uri}
            for mapping in item.get('provider_mappings') or []:
                if mapping.get('provider_instance') and mapping.get('item_id'):
                    aliases.add(f"{mapping['provider_instance']}://track/{mapping['item_id']}")
            payload['tracks'][uri]={'metadata':metadata,'aliases':sorted(aliases),'source':'metadata-enrichment'}
        else:
            # Never retain old inferred genres after source tags cease supporting them.
            payload['tracks'].pop(uri,None)
    payload['updated_at']=time.time();payload['counts']=stats
    return payload


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('/media/local/music'))
    parser.add_argument('--library',type=Path,default=Path('/media/local/cache/mass_playlists.json'))
    parser.add_argument('--output',type=Path,default=Path('/media/local/cache/track_enrichment.json'))
    parser.add_argument('--provider-profiles',type=Path,default=Path('/media/local/cache/provider_profiles.json'))
    parser.add_argument('--max-files',type=int,default=250)
    args=parser.parse_args()
    if not 0<=args.max_files<=10000:raise ValueError('Invalid file limit')
    configured=os.getenv('MASS_WS_URL','http://localhost:8095')
    base=configured.replace('ws://','http://').replace('wss://','https://').removesuffix('/ws')
    items=catalogue(api_client(base,os.getenv('MASS_TOKEN','')))
    if args.provider_profiles.exists():
        profiles=provider_profiles.load(json.loads(args.provider_profiles.read_text()))
        items=[music_features.merge(i,profiles.get(i['uri'],({},''))[0]) for i in items]
    tree=json.loads(args.library.read_text()) if args.library.exists() else []
    previous=json.loads(args.output.read_text()) if args.output.exists() else None
    os.umask(0o077)
    payload=enrich(items,tree,args.root,previous,args.max_files)
    atomic_write(args.output,payload)
    print(json.dumps(payload['counts']))


if __name__=='__main__':main()
