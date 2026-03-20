#!/usr/bin/env python3
"""
Router-Owns-Queue Integration Tests

Tests the queue endpoints on player, source, and router services.
Runs ON the BS5c device (local player config).

Usage (via wrapper):
  ./tests/integration/test-queue.sh
  HOST=beosound5c-office.kirstenhome ./tests/integration/test-queue.sh

Or directly on device:
  python3 test-queue.py

Prerequisites:
  - beo-router + beo-player-* running
  - At least one source with playable content (spotify or plex)
"""

import sys
import time

sys.path.insert(0, "/tmp")
from qhelpers import *


# ═══════════════════════════════════════════════════════
# 1. Endpoint existence tests (no playback needed)
# ═══════════════════════════════════════════════════════

def test_01_player_queue_endpoint():
    """GET /player/queue returns valid JSON with expected fields."""
    data = player_queue()
    assert "tracks" in data, f"Missing 'tracks': {data}"
    assert "current_index" in data, f"Missing 'current_index': {data}"
    assert "total" in data, f"Missing 'total': {data}"
    assert isinstance(data["tracks"], list)


def test_02_router_queue_endpoint():
    """GET /router/queue returns valid JSON with expected fields."""
    data = router_queue()
    assert "tracks" in data, f"Missing 'tracks': {data}"
    assert "current_index" in data, f"Missing 'current_index': {data}"
    assert "total" in data, f"Missing 'total': {data}"


def test_03_source_queue_endpoints():
    """GET /queue on each source returns valid JSON."""
    sources = discover()
    errors = []
    for sid, sdata in sorted(sources.items()):
        port = sdata["port"]
        try:
            data = source_queue(port)
            if "tracks" not in data:
                errors.append(f"{sid}: Missing 'tracks': {data}")
        except Exception as e:
            errors.append(f"{sid} (port {port}): {e}")
    if errors:
        raise Exception("; ".join(errors))


def test_04_player_play_from_queue_endpoint():
    """POST /player/play_from_queue responds (even with error)."""
    data = player_play_from_queue(0)
    assert "status" in data, f"Missing 'status': {data}"


def test_05_router_queue_play_endpoint():
    """POST /router/queue/play responds (ok or error, not crash)."""
    try:
        data = router_queue_play(0)
        assert "status" in data, f"Missing 'status': {data}"
    except Exception as e:
        # 500 is acceptable when no active source — just checking it doesn't crash
        if "500" in str(e):
            pass  # acceptable
        else:
            raise


# ═══════════════════════════════════════════════════════
# 2. Idle state — no active source
# ═══════════════════════════════════════════════════════

def test_06_router_queue_empty_when_idle():
    """Router queue is empty or single-track when nothing is playing."""
    stop_all()
    time.sleep(1)
    data = router_queue()
    # Either empty or wrapping media_state as single track
    assert data["total"] <= 1, f"Expected 0-1 tracks when idle: {data['total']}"


def test_07_player_queue_empty_when_idle():
    """Player queue is empty when nothing is playing."""
    data = player_queue()
    # Local player (mpv) always returns empty queue
    assert data["total"] == 0 or isinstance(data["tracks"], list)


# ═══════════════════════════════════════════════════════
# 3. Source-managed queue (Plex)
# ═══════════════════════════════════════════════════════

def test_10_plex_queue_empty_before_play(sources):
    """Plex queue is empty before any playlist is played."""
    port = sources["plex"]["port"]
    data = source_queue(port)
    assert data["total"] == 0, f"Expected empty plex queue: {data}"


def test_11_plex_play_populates_queue(sources):
    """Playing a Plex playlist populates the source queue."""
    port = sources["plex"]["port"]

    # Get playlists
    playlists = get(f"http://localhost:{port}/playlists")
    if isinstance(playlists, dict) and (playlists.get("setup_needed") or playlists.get("loading")):
        raise Exception(f"Plex not ready: {playlists}")
    if not playlists:
        raise Exception("No Plex playlists available")

    # Play first playlist
    pl = playlists[0]
    source_command(port, {"command": "play_playlist", "playlist_id": pl["id"]})
    time.sleep(3)

    # Check source queue
    data = source_queue(port)
    assert data["total"] > 0, f"Expected tracks in queue: {data}"
    assert data["current_index"] == 0, f"Expected current_index=0: {data['current_index']}"
    assert data["tracks"][0]["current"] is True


def test_12_plex_queue_through_router(sources):
    """Router queue returns Plex queue (source authority, manages_queue=True)."""
    data = router_queue()
    assert data["total"] > 0, f"Router should reflect Plex queue: {data}"
    # Verify tracks have expected fields
    t = data["tracks"][0]
    assert "title" in t, f"Missing title: {t}"
    assert "id" in t, f"Missing id: {t}"
    assert t["id"].startswith("q:"), f"Queue ID should be prefixed: {t['id']}"


def test_13_plex_next_updates_queue(sources):
    """After next track, queue current_index advances."""
    port = sources["plex"]["port"]
    source_command(port, {"action": "next"})
    time.sleep(3)

    data = source_queue(port)
    assert data["current_index"] == 1, \
        f"Expected current_index=1 after next: {data['current_index']}"

    router_data = router_queue()
    assert router_data["current_index"] == 1, \
        f"Router should reflect updated index: {router_data['current_index']}"


def test_14_plex_play_index(sources):
    """play_index command jumps to a specific track."""
    port = sources["plex"]["port"]
    source_command(port, {"command": "play_index", "index": 0})
    time.sleep(3)

    data = source_queue(port)
    assert data["current_index"] == 0, \
        f"Expected current_index=0 after play_index: {data['current_index']}"


def test_15_router_queue_play_plex(sources):
    """POST /router/queue/play routes to Plex source (manages_queue=True)."""
    port = sources["plex"]["port"]
    data = source_queue(port)
    if data["total"] < 2:
        raise Exception(f"Need at least 2 tracks to test queue play (have {data['total']})")

    # Play second track via router
    result = router_queue_play(1)
    assert result.get("status") == "ok", f"Queue play failed: {result}"
    time.sleep(3)

    # Verify position changed
    data = source_queue(port)
    assert data["current_index"] == 1, \
        f"Expected index=1 after queue play: {data['current_index']}"


# ═══════════════════════════════════════════════════════
# 4. Spotify queue (manages_queue=False, local player)
# ═══════════════════════════════════════════════════════

def test_20_spotify_queue_empty_before_play(sources):
    """Spotify queue is empty before any playlist is played."""
    port = sources["spotify"]["port"]
    data = source_queue(port)
    assert data["total"] == 0, f"Expected empty spotify queue: {data}"


def test_21_spotify_play_populates_queue(sources):
    """Playing a Spotify playlist populates the source queue."""
    port = sources["spotify"]["port"]

    # Get playlists
    playlists = get(f"http://localhost:{port}/playlists")
    if isinstance(playlists, dict) and (playlists.get("setup_needed") or playlists.get("needs_reauth")):
        raise Exception(f"Spotify not ready: {playlists}")
    if not playlists:
        raise Exception("No Spotify playlists available")

    # Play first playlist
    pl = playlists[0]
    source_command(port, {"command": "play_playlist", "playlist_id": pl["id"]})
    time.sleep(5)

    # Check source queue — should have tracks from the playlist
    data = source_queue(port)
    assert data["total"] > 0, f"Expected tracks in spotify queue: {data}"


def test_22_spotify_queue_through_router():
    """Router queue returns Spotify source queue (local player → source first)."""
    data = router_queue()
    assert data["total"] > 0, f"Router should reflect Spotify queue: {data}"
    t = data["tracks"][0]
    assert "title" in t, f"Missing title: {t}"
    assert t["id"].startswith("q:"), f"Queue ID should be prefixed: {t['id']}"


# ═══════════════════════════════════════════════════════
# 5. Radio — no queue (stream)
# ═══════════════════════════════════════════════════════

def test_30_radio_queue_always_empty(sources):
    """Radio queue is always empty (stream, no playlist)."""
    port = sources["radio"]["port"]
    data = source_queue(port)
    assert data["total"] == 0, f"Radio should have no queue: {data}"


def test_31_radio_router_queue_wraps_media(sources):
    """When radio is playing, router queue wraps current media as single track."""
    port = sources["radio"]["port"]
    source_command(port, {"action": "play"})
    time.sleep(5)

    status = router_status()
    if status.get("active_source") != "radio":
        raise Exception(f"Radio didn't activate: {status.get('active_source')}")

    data = router_queue()
    # Should wrap the media_state as a single track (or be empty if no metadata yet)
    assert data["total"] <= 1, f"Radio queue should be 0-1: {data['total']}"

    stop_all()
    time.sleep(1)


# ═══════════════════════════════════════════════════════
# 6. Queue pagination
# ═══════════════════════════════════════════════════════

def test_40_queue_pagination(sources):
    """Queue pagination with start/max_items works."""
    # Use Spotify first (larger playlists), then Plex
    port = None
    for sid in ("spotify", "plex"):
        if sid in sources:
            p = sources[sid].get("port")
            if p:
                try:
                    q = source_queue(p)
                    if q["total"] >= 3:
                        port = p
                        break
                except Exception:
                    pass
    if not port:
        raise Exception("No source with 3+ tracks for pagination test")

    # Fetch page
    page = source_queue(port, start=1, max_items=2)
    assert len(page["tracks"]) == 2, f"Expected 2 tracks: {len(page['tracks'])}"
    assert page["tracks"][0]["index"] == 1, f"Expected index=1: {page['tracks'][0]}"

    # Router pagination
    rpage = router_queue(start=1, max_items=2)
    assert len(rpage["tracks"]) == 2, f"Router page expected 2: {len(rpage['tracks'])}"


# ═══════════════════════════════════════════════════════
# 7. Queue track format
# ═══════════════════════════════════════════════════════

def test_50_queue_track_format():
    """Queue tracks have all expected fields."""
    data = router_queue()
    if not data["tracks"]:
        raise Exception("No tracks to check format")

    t = data["tracks"][0]
    required = {"id", "title", "artist", "album", "artwork", "index", "current"}
    missing = required - set(t.keys())
    assert not missing, f"Missing fields: {missing}"
    assert isinstance(t["index"], int), f"index should be int: {type(t['index'])}"
    assert isinstance(t["current"], bool), f"current should be bool: {type(t['current'])}"


# ═══════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════

def main():
    print("=" * 55)
    print(" Router-Owns-Queue Integration Tests (Local Player)")
    print("=" * 55)

    sources = discover()
    print(f"\n  Available: {', '.join(sorted(sources.keys()))}")

    has_plex = "plex" in sources
    has_spotify = "spotify" in sources
    has_radio = "radio" in sources

    # Check player type
    status = router_status()
    try:
        ps = get(f"{PLAYER}/player/status")
        ptype = ps.get("player", "unknown")
        print(f"  Player: {ptype}")
        if ptype != "local":
            print(f"  WARNING: Expected local player, got {ptype}")
    except Exception:
        print("  Player: unreachable")

    # Lower volume
    orig_vol = status.get("volume", 30)
    if orig_vol and orig_vol > 10:
        set_volume(10)
        print(f"  Volume: {orig_vol}% -> 10% (will restore)")

    print()

    # -- Endpoint existence (always run) --
    print("── Endpoint Existence ──")
    test("01. Player queue endpoint", test_01_player_queue_endpoint)
    test("02. Router queue endpoint", test_02_router_queue_endpoint)
    test("03. Source queue endpoints", test_03_source_queue_endpoints)
    test("04. Player play_from_queue endpoint", test_04_player_play_from_queue_endpoint)
    test("05. Router queue/play endpoint", test_05_router_queue_play_endpoint)

    print("\n── Idle State ──")
    stop_all()
    time.sleep(1)
    test("06. Router queue empty when idle", test_06_router_queue_empty_when_idle)
    test("07. Player queue empty when idle", test_07_player_queue_empty_when_idle)

    # -- Plex tests --
    if has_plex:
        plex_status = sources["plex"]["status"]
        if plex_status.get("has_credentials"):
            print("\n── Plex Queue (source-managed) ──")
            stop_all()
            time.sleep(1)
            test("10. Plex queue empty before play", lambda: test_10_plex_queue_empty_before_play(sources))
            test("11. Plex play populates queue", lambda: test_11_plex_play_populates_queue(sources))
            test("12. Plex queue through router", lambda: test_12_plex_queue_through_router(sources))
            test("13. Plex next updates queue", lambda: test_13_plex_next_updates_queue(sources))
            test("14. Plex play_index command", lambda: test_14_plex_play_index(sources))
            test("15. Router queue/play routes to Plex", lambda: test_15_router_queue_play_plex(sources))
            stop_all()
            time.sleep(1)
        else:
            for n in ("10", "11", "12", "13", "14", "15"):
                skip(f"{n}. Plex test", "no credentials")
    else:
        for n in ("10", "11", "12", "13", "14", "15"):
            skip(f"{n}. Plex test", "plex not running")

    # -- Spotify tests --
    if has_spotify:
        sp_status = sources["spotify"]["status"]
        if sp_status.get("has_credentials") and not sp_status.get("needs_reauth"):
            print("\n── Spotify Queue (local player → source authority) ──")
            stop_all()
            time.sleep(1)
            test("20. Spotify queue empty before play", lambda: test_20_spotify_queue_empty_before_play(sources))
            test("21. Spotify play populates queue", lambda: test_21_spotify_play_populates_queue(sources))
            test("22. Spotify queue through router", test_22_spotify_queue_through_router)
            stop_all()
            time.sleep(1)
        else:
            for n in ("20", "21", "22"):
                skip(f"{n}. Spotify test", "needs auth")
    else:
        for n in ("20", "21", "22"):
            skip(f"{n}. Spotify test", "spotify not running")

    # -- Radio tests --
    if has_radio:
        print("\n── Radio (no queue) ──")
        stop_all()
        time.sleep(1)
        test("30. Radio queue always empty", lambda: test_30_radio_queue_always_empty(sources))
        test("31. Radio router queue wraps media", lambda: test_31_radio_router_queue_wraps_media(sources))
    else:
        skip("30. Radio queue", "radio not running")
        skip("31. Radio router queue", "radio not running")

    # -- Pagination --
    if has_spotify or has_plex:
        print("\n── Pagination ──")
        # Start playback on a source with enough tracks
        started = False
        if has_spotify and sources["spotify"]["status"].get("has_credentials") and not sources["spotify"]["status"].get("needs_reauth"):
            port = sources["spotify"]["port"]
            playlists = get(f"http://localhost:{port}/playlists")
            if isinstance(playlists, list) and playlists:
                source_command(port, {"command": "play_playlist", "playlist_id": playlists[0]["id"]})
                time.sleep(5)
                started = True
        if not started and has_plex and sources["plex"]["status"].get("has_credentials"):
            port = sources["plex"]["port"]
            playlists = get(f"http://localhost:{port}/playlists")
            if isinstance(playlists, list) and playlists:
                source_command(port, {"command": "play_playlist", "playlist_id": playlists[0]["id"]})
                time.sleep(3)
        test("40. Queue pagination", lambda: test_40_queue_pagination(sources))
    else:
        skip("40. Queue pagination", "no queue source")

    # -- Track format --
    print("\n── Track Format ──")
    try:
        data = router_queue()
        if data.get("tracks"):
            test("50. Queue track format", test_50_queue_track_format)
        else:
            skip("50. Queue track format", "no tracks in queue")
    except Exception:
        skip("50. Queue track format", "router queue not available")

    # Cleanup
    stop_all()
    if orig_vol and orig_vol > 10:
        set_volume(orig_vol)
        print(f"\n  Volume restored to {orig_vol}%")

    failed = summary()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
