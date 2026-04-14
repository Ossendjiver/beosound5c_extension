#!/usr/bin/env python3
"""
Action Timestamp — Common Tests (player-agnostic)

Tests the core timestamp mechanism using direct HTTP calls.
Works with any player type (local, Sonos, BlueSound).

Requires: beo-router + beo-player-* + at least 2 sources
"""

import sys
import time

# Helpers are copied alongside this script to /tmp
sys.path.insert(0, "/tmp")
from helpers import *


def test_01_activate_stamps_timestamp():
    """Activating a source via router event stamps a fresh action_ts."""
    stop_all()
    ts_before = router_status()["latest_action_ts"]

    router_event("radio")
    time.sleep(4)

    status = router_status()
    assert status["latest_action_ts"] > ts_before, \
        f"action_ts not updated: {status['latest_action_ts']} <= {ts_before}"
    assert status["active_source"] == "radio", \
        f"Expected active=radio, got {status['active_source']}"
    stop_all()


def test_02_newer_source_wins():
    """When two sources are activated in sequence, the newer one wins."""
    stop_all()

    router_event(src_a)
    time.sleep(2)
    s1 = router_status()
    assert s1["active_source"] == src_a

    router_event(src_b)
    time.sleep(2)
    s2 = router_status()
    assert s2["active_source"] == src_b, \
        f"Expected {src_b}, got {s2['active_source']}"
    assert s2["latest_action_ts"] > s1["latest_action_ts"]
    stop_all()


def test_03_stale_register_rejected():
    """register("playing") with an old action_ts is rejected."""
    stop_all()

    router_event(src_b)
    time.sleep(2)
    current_ts = router_status()["latest_action_ts"]

    # Stale register from src_a
    stale_ts = current_ts - 10.0
    router_source(src_a, "playing", action_ts=stale_ts,
                  name=src_a.upper(),
                  command_url=f"http://localhost:{SOURCE_PORTS[src_a]}/command",
                  player="local")

    assert router_status()["active_source"] == src_b, \
        f"Stale register stole active"
    stop_all()


def test_04_stale_player_play_rejected():
    """player_play() with an old action_ts is rejected."""
    stop_all()

    router_event(src_a)
    time.sleep(2)
    current_ts = router_status()["latest_action_ts"]

    result = player_play(action_ts=current_ts - 10.0,
                         url="http://example.com/stale.mp3")
    assert result.get("status") == "dropped", f"Expected dropped, got {result}"
    assert result.get("reason") == "stale"
    stop_all()


def test_05_stale_media_update_rejected():
    """Media update with an old action_ts is dropped."""
    stop_all()

    router_event(src_b)
    time.sleep(2)
    current_ts = router_status()["latest_action_ts"]

    result = router_media(src_a, title="Stale Title", action_ts=current_ts - 10.0)
    assert result.get("dropped") is True, f"Expected dropped, got {result}"

    status = router_status()
    media = status.get("media")
    if media:
        assert media.get("title") != "Stale Title"
    stop_all()


def test_06_media_from_wrong_source_rejected():
    """Media update from non-active source is dropped (source_id check)."""
    stop_all()

    router_event(src_b)
    time.sleep(2)

    result = router_media(src_a, title="Wrong Source")
    assert result.get("dropped") is True
    stop_all()


def test_07_rapid_switch():
    """Rapid A->B: B wins, stale register from A fails."""
    stop_all()

    router_event(src_a)
    time.sleep(0.3)
    router_event(src_b)
    time.sleep(3)

    status = router_status()
    assert status["active_source"] == src_b, \
        f"Rapid switch: expected {src_b}, got {status['active_source']}"

    # Stale register from src_a
    stale_ts = status["latest_action_ts"] - 5.0
    router_source(src_a, "playing", action_ts=stale_ts,
                  name=src_a.upper(),
                  command_url=f"http://localhost:{SOURCE_PORTS[src_a]}/command",
                  player="local")
    assert router_status()["active_source"] == src_b
    stop_all()


def test_08_reactivation():
    """A->B->A: re-activating A gets a new, higher timestamp."""
    stop_all()

    router_event(src_a)
    time.sleep(2)
    ts1 = router_status()["latest_action_ts"]

    router_event(src_b)
    time.sleep(2)

    router_event(src_a)
    time.sleep(2)
    s = router_status()
    assert s["active_source"] == src_a, f"Re-activation failed: {s['active_source']}"
    assert s["latest_action_ts"] > ts1
    stop_all()


def test_09_no_timestamp_passes():
    """Commands without action_ts are accepted (backward compat)."""
    stop_all()

    router_source(src_a, "playing", name=src_a.upper(),
                  command_url=f"http://localhost:{SOURCE_PORTS[src_a]}/command",
                  player="local")
    time.sleep(0.5)
    assert router_status()["active_source"] == src_a

    result = player_play(url="http://example.com/test.mp3")
    assert result.get("status") in ("ok", "error"), \
        f"Play without timestamp should not be dropped: {result}"
    stop_all()


def test_10_player_tracks_latest_ts():
    """Player accepts same-or-newer ts, rejects older ts."""
    stop_all()

    # Two real activations to get properly ordered timestamps
    router_event(src_a)
    time.sleep(2)

    router_event(src_b)
    time.sleep(2)

    # Query player's actual latest_action_ts (may differ from router's
    # due to intervening commands during activation)
    ps = player_status()
    player_ts = ps.get("latest_action_ts", 0)
    assert player_ts > 0, "Player should have a non-zero action_ts"

    # Play with older ts — rejected
    result = player_play(action_ts=player_ts - 10.0, url="http://example.com/old.mp3")
    assert result.get("status") == "dropped"

    # Play with same ts — accepted (>= check)
    result = player_play(action_ts=player_ts, url="http://example.com/same.mp3")
    assert result.get("status") != "dropped", \
        f"Same-ts should be accepted: {result}"
    stop_all()


def test_11_concurrent_stale_commands():
    """Burst of stale play + register commands — all rejected."""
    stop_all()

    router_event(src_b)
    time.sleep(2)
    current_ts = router_status()["latest_action_ts"]

    stale_base = current_ts - 20.0
    for i in range(5):
        result = player_play(action_ts=stale_base + i,
                             url=f"http://example.com/{i}.mp3")
        assert result.get("status") == "dropped", \
            f"Stale play #{i} not dropped: {result}"

    for i in range(3):
        router_source(src_a, "playing", action_ts=stale_base + i,
                      name=src_a.upper(),
                      command_url=f"http://localhost:{SOURCE_PORTS[src_a]}/command",
                      player="local")

    assert router_status()["active_source"] == src_b
    stop_all()


def test_12_auto_advance_same_ts():
    """Auto-advance: source re-registers + re-plays with same action_ts.
    Should be accepted (ts >= ts, not strictly less than)."""
    stop_all()

    router_event(src_a)
    time.sleep(2)
    active_ts = router_status()["latest_action_ts"]

    # Same-ts register — accepted (source is already active)
    router_source(src_a, "playing", action_ts=active_ts,
                  name=src_a.upper(),
                  command_url=f"http://localhost:{SOURCE_PORTS[src_a]}/command",
                  player="local")
    assert router_status()["active_source"] == src_a

    # Same-ts play — accepted
    result = player_play(action_ts=active_ts, url="http://example.com/next.mp3")
    assert result.get("status") != "dropped", \
        f"Same-ts play should be accepted: {result}"

    # Same-ts media — accepted
    result = router_media(src_a, title="Next Track", action_ts=active_ts)
    assert result.get("dropped") is not True
    stop_all()


def test_13_stale_stop_rejected():
    """player_stop() with an old action_ts is rejected."""
    stop_all()

    router_event(src_a)
    time.sleep(2)
    ts_a = router_status()["latest_action_ts"]

    router_event(src_b)
    time.sleep(2)
    ts_b = router_status()["latest_action_ts"]
    assert ts_b > ts_a

    # Fresh play from src_b so player has its ts
    player_play(action_ts=ts_b, url="http://example.com/new.mp3")
    time.sleep(0.5)

    # Stale stop from src_a — should be rejected
    result = player_stop(action_ts=ts_a)
    assert result.get("status") == "dropped", \
        f"Stale stop should be rejected: {result}"
    assert result.get("reason") == "stale"
    stop_all()


def test_14_stop_without_ts_accepted():
    """player_stop() without action_ts is accepted (backward compat)."""
    stop_all()

    router_event(src_a)
    time.sleep(2)

    result = player_stop()
    assert result.get("status") != "dropped", \
        f"Stop without timestamp should be accepted: {result}"
    stop_all()


def test_15_rapid_switch_no_overlap():
    """Rapid A->B: src_a's late play is rejected by the player.

    This is the exact race condition that caused overlapping audio:
    src_a's play arrives at the player AFTER src_b's play, but src_a's
    action_ts is older so the player drops it.
    """
    stop_all()

    router_event(src_a)
    time.sleep(2)
    ts_a = router_status()["latest_action_ts"]

    router_event(src_b)
    time.sleep(2)
    ts_b = router_status()["latest_action_ts"]
    assert ts_b > ts_a

    # Simulate src_a's late play arriving after src_b already took over
    result = player_play(action_ts=ts_a, url="http://example.com/stale-source.mp3")
    assert result.get("status") == "dropped", \
        f"Late play from old source should be rejected: {result}"

    # Simulate src_a's late stop arriving after src_b already took over
    result = player_stop(action_ts=ts_a)
    assert result.get("status") == "dropped", \
        f"Late stop from old source should be rejected: {result}"
    stop_all()


def test_16_next_updates_authority():
    """player_next() advances authority so auto-advance play still works.

    After user presses next, the source's action_ts must be updated to
    the next command's timestamp. Otherwise a subsequent player_play()
    (auto-advance) would use the stale activation ts and be rejected.
    """
    stop_all()

    router_event(src_a)
    time.sleep(2)
    ts_activate = router_status()["latest_action_ts"]

    # First play at activation ts
    result = player_play(action_ts=ts_activate, url="http://example.com/t1.mp3")
    assert result.get("status") != "dropped"

    # Simulate user pressing next — stamps fresh ts via player
    ts_next = time.monotonic()
    result = post(f"{PLAYER}/player/next", {"action_ts": ts_next})

    # Auto-advance play should use ts_next (>= player's latest) — accepted
    result = player_play(action_ts=ts_next, url="http://example.com/t2.mp3")
    assert result.get("status") != "dropped", \
        f"Auto-advance after next should be accepted: {result}"

    # But a play with the old activation ts should be rejected
    result = player_play(action_ts=ts_activate, url="http://example.com/stale.mp3")
    assert result.get("status") == "dropped", \
        f"Play with pre-next ts should be rejected: {result}"
    stop_all()


def test_17_intra_source_play_after_next():
    """Intra-source play (digit press, track selection) must not be
    rejected after user has pressed next/prev.

    Regression: next/prev bumped player's latest_action_ts, but a
    subsequent digit press used the stale activation _action_ts
    because register() yields allowed it to be overwritten.
    """
    stop_all()

    router_event(src_a)
    time.sleep(3)

    # Press next — bumps player's ts above the activation ts
    post(f"{PLAYER}/player/next", {"action_ts": time.monotonic()})
    time.sleep(0.5)
    post(f"{PLAYER}/player/next", {"action_ts": time.monotonic()})
    time.sleep(0.5)

    ps = player_status()
    player_ts = ps.get("latest_action_ts", 0)
    assert player_ts > 0

    # A fresh play (digit press) should use a ts > player's latest
    time.sleep(0.1)
    ts_fresh = time.monotonic()
    assert ts_fresh > player_ts, \
        f"Fresh monotonic should be > player ts: {ts_fresh} vs {player_ts}"
    result = player_play(action_ts=ts_fresh, url="http://example.com/digit.mp3")
    assert result.get("status") != "dropped", \
        f"Fresh intra-source play should not be rejected: {result}"
    stop_all()


def main():
    global src_a, src_b

    print("=" * 55)
    print(" Action Timestamp Tests — Common")
    print("=" * 55)

    sources = discover()
    print(f"\n  Available: {', '.join(sorted(sources.keys()))}")

    # Pick two sources for testing
    for a, b in [("radio", "usb"), ("radio", "spotify"), ("spotify", "plex")]:
        if a in sources and b in sources:
            src_a, src_b = a, b
            break
    else:
        print("  ERROR: Need at least 2 running sources")
        sys.exit(2)

    print(f"  Test sources: {src_a}, {src_b}")

    # Lower volume during tests
    orig_vol = router_status()["volume"]
    if orig_vol > 10:
        set_volume(10)
        print(f"  Volume: {orig_vol}% -> 10% (will restore)")

    print()

    try:
        test("01. Activate stamps action_ts", test_01_activate_stamps_timestamp)
        test("02. Newer source wins on sequential switch", test_02_newer_source_wins)
        test("03. Stale register(playing) rejected", test_03_stale_register_rejected)
        test("04. Stale player_play() rejected", test_04_stale_player_play_rejected)
        test("05. Stale media update rejected", test_05_stale_media_update_rejected)
        test("06. Media from wrong source rejected", test_06_media_from_wrong_source_rejected)
        test("07. Rapid A->B switch: B wins", test_07_rapid_switch)
        test("08. A->B->A re-activation gets new timestamp", test_08_reactivation)
        test("09. No timestamp (legacy) passes", test_09_no_timestamp_passes)
        test("10. Player tracks latest action_ts", test_10_player_tracks_latest_ts)
        test("11. Burst of stale commands all rejected", test_11_concurrent_stale_commands)
        test("12. Auto-advance: same action_ts accepted", test_12_auto_advance_same_ts)
        test("13. Stale player_stop() rejected", test_13_stale_stop_rejected)
        test("14. Stop without timestamp accepted", test_14_stop_without_ts_accepted)
        test("15. Rapid switch: late play + stop rejected", test_15_rapid_switch_no_overlap)
        test("16. Next updates authority for auto-advance", test_16_next_updates_authority)
        test("17. Intra-source play works after next/prev", test_17_intra_source_play_after_next)
    finally:
        stop_all()
        if orig_vol > 10:
            set_volume(orig_vol)
            print(f"\n  Volume restored to {orig_vol}%")

    failed = summary()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
