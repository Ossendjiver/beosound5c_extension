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


def save_tokens(client_id, refresh_token, scope=None):
    """Merge client_id + refresh_token (+ optional scope) into the store.

    Spotify returns ``scope`` in token-exchange and refresh responses;
    persisting it lets callers detect when a stored token was issued
    against a narrower scope set than the app currently asks for —
    a refresh won't re-grant scopes the user never approved, so missing
    scopes can only be fixed by a full re-auth.
    """
    update = {"client_id": client_id, "refresh_token": refresh_token}
    if scope is not None:
        update["scope"] = scope
    return _store.save_merge(update)


def delete_tokens():
    return _store.delete()


def refresh_lock():
    """``with refresh_lock():`` — serialises concurrent refreshes."""
    return _store.refresh_lock()
