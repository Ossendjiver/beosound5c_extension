"""Shared digit playlist utilities for BeoSound 5c sources.

Used by:
  - fetch.py scripts (detect_digit_playlist, build_digit_mapping)
  - service.py sources (DigitPlaylistMixin for cached lookups)
"""

import json
import logging
import os
import re

log = logging.getLogger(__name__)

DIGIT_SLOTS = "0123456789"

# Explicit digit→playlist pins written by the Config UI.  Lives next to
# radio_favourites.json in prod; falls back to a file beside the service
# in dev (same convention as radio's FAVOURITES_PATH_PROD/_DEV).
SPOTIFY_FAVOURITES_FILENAME = 'spotify_favourites.json'


def spotify_favourites_path(dev_dir):
    """Resolve the spotify favourites (digit pin) file path.

    Prod: ``$BS5C_CONFIG_DIR/spotify_favourites.json`` (default
    ``/etc/beosound5c``).  Dev (config dir absent): the file sits in
    ``dev_dir`` — callers pass the directory of their own script so
    service.py and fetch.py agree on the same dev file.
    """
    conf_dir = os.getenv('BS5C_CONFIG_DIR', '/etc/beosound5c')
    if os.path.isdir(conf_dir):
        return os.path.join(conf_dir, SPOTIFY_FAVOURITES_FILENAME)
    return os.path.join(dev_dir, SPOTIFY_FAVOURITES_FILENAME)


def load_digit_pins(path):
    """Load explicit digit pins: ``{slot: {"id": ..., "name": ...}}``.

    Returns ``{}`` when the file is missing or malformed — pins are an
    optional overlay on the automatic mapping, never a blocker.  Entries
    with a non-digit slot or without an ``id`` are dropped.
    """
    try:
        with open(path) as f:
            raw = json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        log.warning("Failed to load digit pins from %s: %s", path, e)
        return {}
    if not isinstance(raw, dict):
        log.warning("Malformed digit pins file %s (expected object)", path)
        return {}
    pins = {}
    for slot, entry in raw.items():
        if not (isinstance(slot, str) and len(slot) == 1 and slot in DIGIT_SLOTS):
            continue
        if isinstance(entry, dict) and entry.get('id'):
            pins[slot] = {'id': entry['id'], 'name': entry.get('name', '')}
    return pins


def detect_digit_playlist(name):
    """Check if playlist name starts with a digit pattern like '5:' or '5 -'.
    Returns the digit (0-9) or None."""
    match = re.match(r'^(\d)[\s]*[:\-]', name)
    if match:
        return match.group(1)
    return None


def build_digit_mapping(playlists, pins=None):
    """Build digit 0-9 mapping.  Per-slot precedence:

    1. Explicit pins from the Config UI (``spotify_favourites.json``),
       ``{slot: {"id": ...}}`` — skipped when the pinned id is no longer
       in ``playlists`` (deleted playlist falls through to 2/3).
    2. Name convention: playlists named like '5: Jazz' pin to their digit
       (first match wins).
    3. Remaining slots filled in input order (callers sort alphabetically).
    """
    by_id = {pl['id']: pl for pl in playlists}
    assigned = {}
    used_ids = set()

    # 1. Explicit pins.  The same playlist may be pinned to several slots
    # on purpose; used_ids only shields it from convention/auto-fill.
    for slot in DIGIT_SLOTS:
        pin = (pins or {}).get(slot)
        pid = pin.get('id') if isinstance(pin, dict) else pin
        if pid and pid in by_id:
            assigned[slot] = by_id[pid]
            used_ids.add(pid)

    # 2. Name convention for slots without an explicit pin.
    for pl in playlists:
        digit = detect_digit_playlist(pl['name'])
        if digit is not None and digit not in assigned and pl['id'] not in used_ids:
            assigned[digit] = pl
            used_ids.add(pl['id'])

    # 3. Alphabetical fill for whatever is left.
    remaining = iter(pl for pl in playlists if pl['id'] not in used_ids)

    mapping = {}
    for slot in DIGIT_SLOTS:
        if slot in assigned:
            pl = assigned[slot]
        else:
            pl = next(remaining, None)
            if not pl:
                continue
        entry = {
            'id': pl['id'],
            'name': pl['name'],
            'image': pl.get('image'),
        }
        if pl.get('url'):
            entry['url'] = pl['url']
        mapping[slot] = entry

    return mapping


class DigitPlaylistMixin:
    """Mixin for source services that use digit playlists.

    Caches the digit playlists file in memory instead of re-reading
    from disk on every button press. Call `_reload_digit_playlists()`
    after a fetch/refresh to update the cache.

    Subclass must set `DIGIT_PLAYLISTS_FILE` as a class or instance attribute.
    """

    _digit_cache = None  # {digit_str: {id, name, image, ...}}

    def _reload_digit_playlists(self):
        """Reload digit playlists from disk into cache."""
        try:
            with open(self.DIGIT_PLAYLISTS_FILE) as f:
                self._digit_cache = json.load(f)
        except FileNotFoundError:
            self._digit_cache = {}
        except Exception as e:
            log.warning("Failed to load digit playlists: %s", e)
            self._digit_cache = {}

    def _get_digit_playlist(self, digit):
        """Look up a digit playlist from the cached mapping."""
        if self._digit_cache is None:
            self._reload_digit_playlists()
        info = self._digit_cache.get(str(digit))
        if info and info.get('id'):
            return info
        return None

    def _get_digit_names(self):
        """Return {digit: name} dict for status responses."""
        if self._digit_cache is None:
            self._reload_digit_playlists()
        return {
            d: info['name']
            for d, info in (self._digit_cache or {}).items()
            if info and info.get('name')
        }
