"""Token store wrapper for Apple Music user credentials."""

import os

from lib.token_store import TokenStore

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_store = TokenStore("apple_music_tokens.json", dev_dir=SCRIPT_DIR)


def load_tokens():
    return _store.load()


def save_tokens(user_token, storefront):
    return _store.save({
        "user_token": user_token,
        "storefront": storefront,
    })


def delete_tokens():
    return _store.delete()
