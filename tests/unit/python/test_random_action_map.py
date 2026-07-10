"""Beo4 RANDOM key wiring — every shuffle-capable source maps "random".

masterlink.py decodes the Beo4 RANDOM key (0xC1) as action "random" and
the router forwards it to the active source's /command endpoint, where
``SourceBase._handle_command_route`` resolves it via ``action_map``.
These tests pin that each source with working shuffle support maps
"random" to its existing shuffle command, so the IR key actually
reaches the shuffle handling.
"""

import importlib

import pytest


def test_usb_maps_random_to_toggle_shuffle():
    svc = importlib.import_module("sources.usb.service")
    assert svc.USBService.action_map["random"] == "toggle_shuffle"


def test_spotify_maps_random_to_shuffle():
    svc = importlib.import_module("sources.spotify.service")
    assert svc.SpotifyService.action_map["random"] == "shuffle"


def test_cd_maps_random_to_toggle_shuffle():
    pytest.importorskip("pyudev", reason="sources.cd needs Linux udev bindings")
    svc = importlib.import_module("sources.cd")
    assert svc.CDService.action_map["random"] == "toggle_shuffle"
