"""Token store wrapper for TIDAL OAuth credentials."""

import os

from lib.token_store import TokenStore

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_store = TokenStore("tidal_tokens.json", dev_dir=SCRIPT_DIR)


def load_tokens():
    return _store.load()


def save_tokens(token_type, access_token, refresh_token, expiry_time):
    return _store.save_merge({
        "token_type": token_type,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expiry_time": expiry_time,
    })


def delete_tokens():
    return _store.delete()


def refresh_lock():
    return _store.refresh_lock()
