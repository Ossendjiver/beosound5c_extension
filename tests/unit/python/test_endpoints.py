"""Tests for lib.endpoints — port constants and URL builders.

These constants are load-bearing: every inter-service HTTP call reads
from here.  An accidental port change would silently break cross-
service routing (none of the services would fail to start — they'd
just start talking to each other on the wrong ports).

The test pins every published constant so any change is a conscious
edit to both the constant and the test.
"""

from __future__ import annotations

from lib import endpoints as E


class TestPorts:
    """Service-port constants are the source of truth for systemd units
    and deploy.sh — they must not drift silently."""

    def test_player_port(self):
        assert E.PLAYER_PORT == 8766

    def test_input_port(self):
        assert E.INPUT_PORT == 8767

    def test_router_port(self):
        assert E.ROUTER_PORT == 8770

    def test_spotify_port(self):
        assert E.SPOTIFY_PORT == 8771

    def test_radio_port(self):
        assert E.RADIO_PORT == 8779


class TestUrlBuilders:
    def test_player_url(self):
        assert E.player_url("/player/foo") == "http://localhost:8766/player/foo"

    def test_router_url(self):
        assert E.router_url("/router/status") == "http://localhost:8770/router/status"

    def test_input_url(self):
        assert E.input_url("/webhook") == "http://localhost:8767/webhook"

    def test_source_url_with_custom_port(self):
        assert E.source_url(8778, "/command") == "http://localhost:8778/command"

    def test_spotify_canvas_url_interpolates_track_id(self):
        url = E.spotify_canvas_url("abc123")
        assert url == "http://localhost:8771/canvas?track_id=abc123"


class TestConstants:
    """Every published URL constant — these are imported by name
    across at least 9 files.  The test doubles as a catalogue."""

    def test_input_constants(self):
        assert E.INPUT_WEBHOOK == "http://localhost:8767/webhook"
        assert E.INPUT_LED_PULSE == "http://localhost:8767/led?mode=pulse"

    def test_player_constants(self):
        assert E.PLAYER_STATE == "http://localhost:8766/player/state"
        assert E.PLAYER_MEDIA == "http://localhost:8766/player/media"
        assert E.PLAYER_STOP == "http://localhost:8766/player/stop"
        assert E.PLAYER_ANNOUNCE == "http://localhost:8766/player/announce"
        assert E.PLAYER_TOGGLE == "http://localhost:8766/player/toggle"
        assert E.PLAYER_NEXT == "http://localhost:8766/player/next"
        assert E.PLAYER_PREV == "http://localhost:8766/player/prev"
        assert E.PLAYER_JOIN == "http://localhost:8766/player/join"
        assert E.PLAYER_UNJOIN == "http://localhost:8766/player/unjoin"
        assert E.PLAYER_TRACK_URI == "http://localhost:8766/player/track_uri"
        assert E.PLAYER_PLAY_FROM_QUEUE == "http://localhost:8766/player/play_from_queue"

    def test_router_constants(self):
        assert E.ROUTER_EVENT == "http://localhost:8770/router/event"
        assert E.ROUTER_SOURCE == "http://localhost:8770/router/source"
        assert E.ROUTER_MEDIA == "http://localhost:8770/router/media"
        assert E.ROUTER_BROADCAST == "http://localhost:8770/router/broadcast"
        assert E.ROUTER_RESYNC == "http://localhost:8770/router/resync"
        assert E.ROUTER_VOLUME_REPORT == "http://localhost:8770/router/volume/report"
        assert E.ROUTER_PLAYBACK_OVERRIDE == "http://localhost:8770/router/playback_override"
        assert E.ROUTER_OUTPUT_ON == "http://localhost:8770/router/output/on"
        assert E.ROUTER_OUTPUT_OFF == "http://localhost:8770/router/output/off"

    def test_source_command_constants(self):
        assert E.SPOTIFY_COMMAND == "http://localhost:8771/command"
        assert E.RADIO_COMMAND == "http://localhost:8779/command"
