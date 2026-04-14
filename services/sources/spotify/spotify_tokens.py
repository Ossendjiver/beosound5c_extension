"""Token store wrapper for Spotify PKCE credentials.

Persists ``client_id`` + ``refresh_token``.  Atomic write, partial-merge,
and refresh-lock semantics live in ``lib.token_store``.
"""

import os

from lib.token_store import TokenStore

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_store = TokenStore("spotify_tokens.json", dev_dir=SCRIPT_DIR)


def load_tokens():
    """Return the saved token dict, or None."""
    return _store.load()


def save_tokens(client_id, refresh_token):
    """Merge client_id + refresh_token into the store (preserves other fields)."""
    return _store.save_merge({
        "client_id": client_id,
        "refresh_token": refresh_token,
    })


def delete_tokens():
    return _store.delete()


def refresh_lock():
    """``with refresh_lock():`` — serialises concurrent refreshes."""
    return _store.refresh_lock()
