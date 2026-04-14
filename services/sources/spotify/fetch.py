#!/usr/bin/env python3
"""
Fetch all Spotify playlists for the authenticated user.
Auto-detects digit playlists by name pattern (e.g., "5: Dinner" -> digit 5).
Run via cron or beo-source-spotify service to keep playlists updated.

Token source: auth.get_access_token() (PKCE token store or env vars).
Can also receive --access-token from the beo-source-spotify service.
"""

import hashlib
import json
import os
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..', '..'))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'services'))

from spotify_auth import get_access_token
from lib.digit_playlists import detect_digit_playlist, build_digit_mapping

DIGIT_PLAYLISTS_FILE = os.path.join(PROJECT_ROOT, 'web', 'json', 'digit_playlists.json')
DEFAULT_OUTPUT_FILE = os.path.join(PROJECT_ROOT, 'web', 'json', 'spotify_playlists.json')


def log(msg):
    """Log with timestamp to stdout (captured by systemd journal or parent process)."""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] {msg}")


def fetch_playlist_tracks(token, playlist_id):
    """Fetch all tracks for a playlist (handles pagination + 429 retry)."""
    headers = {'Authorization': f'Bearer {token}'}
    tracks = []
    url = f'https://api.spotify.com/v1/playlists/{playlist_id}/tracks?limit=100'

    while url:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())

            raw_items = data.get('items', [])
            skipped_local = 0
            skipped_no_url = 0
            for item in raw_items:
                track = item.get('track')
                if not track:
                    continue
                if track.get('is_local'):
                    skipped_local += 1
                    continue
                ext_url = track.get('external_urls', {}).get('spotify')
                if not ext_url:
                    skipped_no_url += 1
                    continue
                tracks.append({
                    'name': track['name'],
                    'artist': ', '.join([a['name'] for a in track.get('artists', []) if a.get('name')]),
                    'album': track.get('album', {}).get('name', ''),
                    'id': track['id'],
                    'uri': track.get('uri', ''),
                    'url': ext_url,
                    'image': track['album']['images'][0]['url'] if track.get('album', {}).get('images') else None
                })

            if skipped_local or skipped_no_url:
                log(f"  Skipped {skipped_local} local files, {skipped_no_url} without URL "
                    f"(page had {len(raw_items)} items, kept {len(tracks)} tracks)")

            url = data.get('next')
        except urllib.error.HTTPError as e:
            if e.code == 429:
                retry_after = int(e.headers.get('Retry-After', 2))
                time.sleep(retry_after)
                continue  # retry same URL
            log(f"  Error fetching tracks: {e}")
            break
        except Exception as e:
            log(f"  Error fetching tracks: {e}")
            break

    return tracks


def fetch_liked_songs(token):
    """Fetch all liked (saved) tracks and return a synthetic playlist dict."""
    headers = {'Authorization': f'Bearer {token}'}
    tracks = []
    url = 'https://api.spotify.com/v1/me/tracks?limit=50'

    while url:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())

            for item in data.get('items', []):
                track = item.get('track')
                if not track:
                    continue
                if track.get('is_local'):
                    continue
                ext_url = track.get('external_urls', {}).get('spotify')
                if not ext_url:
                    continue
                tracks.append({
                    'name': track['name'],
                    'artist': ', '.join([a['name'] for a in track.get('artists', []) if a.get('name')]),
                    'album': track.get('album', {}).get('name', ''),
                    'id': track['id'],
                    'uri': track.get('uri', ''),
                    'url': ext_url,
                    'image': track['album']['images'][0]['url'] if track.get('album', {}).get('images') else None
                })

            url = data.get('next')
        except urllib.error.HTTPError as e:
            if e.code == 429:
                retry_after = int(e.headers.get('Retry-After', 2))
                time.sleep(retry_after)
                continue
            log(f"  Error fetching liked songs: {e}")
            break
        except Exception as e:
            log(f"  Error fetching liked songs: {e}")
            break

    if not tracks:
        return None

    # Build a change-detection key from track count + first/last track IDs
    first_id = tracks[0]['id'] if tracks else ''
    last_id = tracks[-1]['id'] if tracks else ''
    hash_input = f"{len(tracks)}:{first_id}:{last_id}"
    snapshot_id = hashlib.sha256(hash_input.encode()).hexdigest()[:16]

    # Use first track's album image as playlist image
    image = tracks[0].get('image') if tracks else None

    log(f"  Liked Songs: {len(tracks)} tracks")
    return {
        'id': 'liked-songs',
        'name': 'Liked Songs',
        'uri': 'spotify:collection:tracks',
        'url': 'https://open.spotify.com/collection/tracks',
        'image': image,
        'owner': '',
        'public': False,
        'snapshot_id': snapshot_id,
        'tracks': tracks
    }


def fetch_user_playlists(token):
    """Fetch all playlists for the authenticated user (handles pagination + 429 retry)."""
    headers = {'Authorization': f'Bearer {token}'}
    playlists = []
    url = 'https://api.spotify.com/v1/me/playlists?limit=50'

    while url:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())

            for pl in data.get('items', []):
                if not pl:
                    continue
                api_track_count = pl.get('tracks', {}).get('total', '?')
                log(f"  {pl['name']} (owner: {pl.get('owner', {}).get('id', '?')}, "
                    f"tracks: {api_track_count})")
                playlists.append({
                    'id': pl['id'],
                    'name': pl['name'],
                    'uri': pl.get('uri', ''),
                    'url': pl.get('external_urls', {}).get('spotify', ''),
                    'image': pl['images'][0]['url'] if pl.get('images') else None,
                    'owner': pl.get('owner', {}).get('id', ''),
                    'public': pl.get('public', False),
                    'snapshot_id': pl.get('snapshot_id', '')
                })

            url = data.get('next')  # Pagination
        except urllib.error.HTTPError as e:
            if e.code == 429:
                retry_after = int(e.headers.get('Retry-After', 2))
                time.sleep(retry_after)
                continue
            log(f"Error fetching playlists: {e}")
            break
        except Exception as e:
            log(f"Error fetching playlists: {e}")
            break

    return playlists




# Fields every track dict must have. Any cached playlist whose first
# track is missing one of these is dropped from the incremental-sync
# cache so the playlist is re-fetched with the current schema —
# snapshot-id matching alone would otherwise preserve the old shape
# indefinitely.
REQUIRED_TRACK_FIELDS = ('album',)


def _load_cache(output_file):
    """Load the playlist cache from ``output_file`` for incremental sync.

    Returns ``(cache, stale_schema_count)`` where ``cache`` is a dict
    keyed by playlist id with ``{snapshot_id, tracks}`` values, and
    ``stale_schema_count`` is the number of cache entries dropped
    because their tracks were missing fields in REQUIRED_TRACK_FIELDS.
    """
    with open(output_file, 'r') as f:
        cached_playlists = json.load(f)
    cache = {}
    stale_schema = 0
    for cp in cached_playlists:
        tracks = cp.get('tracks', [])
        if tracks and any(f not in tracks[0] for f in REQUIRED_TRACK_FIELDS):
            stale_schema += 1
            continue
        cache[cp['id']] = {
            'snapshot_id': cp.get('snapshot_id', ''),
            'tracks': tracks,
        }
    return cache, stale_schema


def main():
    force = '--force' in sys.argv

    # Parse --output <path> argument
    output_file = DEFAULT_OUTPUT_FILE
    if '--output' in sys.argv:
        idx = sys.argv.index('--output')
        if idx + 1 < len(sys.argv):
            output_file = sys.argv[idx + 1]

    # Parse --access-token <token> argument (passed by beo-source-spotify service)
    access_token = None
    if '--access-token' in sys.argv:
        idx = sys.argv.index('--access-token')
        if idx + 1 < len(sys.argv):
            access_token = sys.argv[idx + 1]

    log("=== Spotify Playlist Fetch Starting ===")
    if force:
        log("Force mode: fetching all tracks regardless of snapshot")

    # Get access token
    try:
        if access_token:
            token = access_token
            log("Using provided access token")
        else:
            token = get_access_token()
            log("Got Spotify access token")
    except Exception as e:
        log(f"ERROR: Failed to get access token: {e}")
        return 1

    # Load cached data for incremental sync. See _load_cache below.
    cache = {}
    if not force and os.path.exists(output_file):
        try:
            cache, stale_schema = _load_cache(output_file)
            log(f"Loaded cache with {len(cache)} playlists"
                + (f" ({stale_schema} dropped for stale schema)"
                   if stale_schema else ""))
        except Exception as e:
            log(f"Could not load cache: {e}")

    # Fetch liked songs
    log("Fetching liked songs")
    liked_cached = cache.get('liked-songs')
    liked_playlist = fetch_liked_songs(token)
    liked_changed = True
    if liked_playlist and liked_cached:
        if liked_cached.get('snapshot_id') == liked_playlist.get('snapshot_id'):
            liked_playlist['tracks'] = liked_cached['tracks']
            liked_changed = False
            log("  Liked Songs (unchanged)")

    # Fetch all user's playlists
    log("Fetching playlists for authenticated user")
    all_playlists = fetch_user_playlists(token)
    log(f"Found {len(all_playlists)} playlists")

    # Split into cached (unchanged) and needs-fetch
    playlists_with_tracks = []
    to_fetch = []
    skipped = 0

    for pl in all_playlists:
        cached = cache.get(pl['id'])
        if cached and cached['snapshot_id'] and cached['snapshot_id'] == pl.get('snapshot_id', ''):
            pl['tracks'] = cached['tracks']
            playlists_with_tracks.append(pl)
            log(f"  {pl['name']} (unchanged)")
            skipped += 1
        else:
            to_fetch.append(pl)

    # Fetch tracks in parallel (4 workers, respects Spotify rate limits)
    fetched = 0
    if to_fetch:
        log(f"Fetching tracks for {len(to_fetch)} playlists in parallel...")
        with ThreadPoolExecutor(max_workers=4) as pool:
            future_to_pl = {
                pool.submit(fetch_playlist_tracks, token, pl['id']): pl
                for pl in to_fetch
            }
            for future in as_completed(future_to_pl):
                pl = future_to_pl[future]
                try:
                    tracks = future.result()
                    pl['tracks'] = tracks
                    playlists_with_tracks.append(pl)
                    log(f"  {pl['name']}: {len(tracks)} tracks")
                    fetched += 1
                except Exception as e:
                    log(f"  {pl['name']}: ERROR {e}")
                    pl['tracks'] = []
                    playlists_with_tracks.append(pl)
                    fetched += 1

    log(f"Fetched {fetched}, skipped {skipped} unchanged")

    # Filter out empty playlists (no tracks)
    before = len(playlists_with_tracks)
    playlists_with_tracks = [p for p in playlists_with_tracks if p.get('tracks')]
    if before != len(playlists_with_tracks):
        log(f"Filtered out {before - len(playlists_with_tracks)} empty playlists")

    # Sort by name
    playlists_with_tracks.sort(key=lambda p: p['name'].lower())

    # Insert Liked Songs as the first playlist
    if liked_playlist and liked_playlist.get('tracks'):
        playlists_with_tracks.insert(0, liked_playlist)
        if liked_changed:
            fetched += 1

    # Skip write if nothing changed (no tracks fetched, same playlist count)
    if fetched == 0 and len(playlists_with_tracks) == len(cache):
        log(f"No changes — skipping disk write")
        return 0

    # Save all playlists
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, 'w') as f:
        json.dump(playlists_with_tracks, f, indent=2)
    log(f"Saved {len(playlists_with_tracks)} playlists to {output_file}")

    # Build digit mapping: pinned names first, then fill alphabetically
    digit_mapping = build_digit_mapping(playlists_with_tracks)
    with open(DIGIT_PLAYLISTS_FILE, 'w') as f:
        json.dump(digit_mapping, f, indent=2)
    pinned = sum(1 for d in "0123456789"
                 if d in digit_mapping and detect_digit_playlist(digit_mapping[d]['name']) is not None)
    log(f"Saved digit playlists ({pinned} pinned, {len(digit_mapping) - pinned} auto-filled)")

    log("=== Done ===")
    return 0

if __name__ == '__main__':
    exit(main())
