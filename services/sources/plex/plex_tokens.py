"""Token store wrapper for Plex user credentials.

Persists ``auth_token`` + server/user metadata.  Plex tokens don't
expire, so no refresh flow is needed.
"""

import os

from lib.token_store import TokenStore

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_store = TokenStore("plex_tokens.json", dev_dir=SCRIPT_DIR)


def load_tokens():
    return _store.load()


def save_tokens(auth_token, server_url, server_name, user_name):
    return _store.save({
        "auth_token": auth_token,
        "server_url": server_url,
        "server_name": server_name,
        "user_name": user_name,
    })


def delete_tokens():
    return _store.delete()
