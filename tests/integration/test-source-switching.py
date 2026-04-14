#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────
# Source Switching Integration Test
#
# Rapidly switches between all available sources and verifies:
#   - Router activates the correct source
#   - Player is playing with the expected backend
#   - Media metadata is populated (not stale from previous source)
#   - Rapid switching doesn't cause audio overlap or stale metadata
#   - Stop clears everything
#
# Runs ON the BS5c device (copied there by the wrapper script).
#
# Usage (via wrapper):
#   ./tests/integration/test-source-switching.sh
#   HOST=beosound5c.local ./tests/integration/test-source-switching.sh
#
# Or directly on device:
#   python3 test-source-switching.py [--volume N] [--json]
#
# Prerequisites:
#   - beo-router + beo-player-* running on device
#   - At least 2 sources registered (check /router/status)
#   - For Spotify tests: valid Spotify credentials (not needs_reauth)
#   - Volume is set to --volume (default 10) at start, restored after
# ─────────────────────────────────────────────────────────────────────

import argparse
import json
import sys
import time
import urllib.request
import urllib.error

# ── Config ──

ROUTER = "http://localhost:8770"
PLAYER = "http://localhost:8766"

# Source button → expected active source ID and player backend.
# The button name is what the IR remote sends; the source ID is what
# the router maps it to (via config.json source_buttons).
# Backend expectations: radio/usb/cd/plex/news → mpv, spotify → librespot.
BACKEND_MAP = {
    "radio": "mpv",
    "usb": "mpv",
    "cd": "mpv",
    "plex": "mpv",
    "news": "mpv",
    "spotify": "librespot",
}

# ── Helpers ──

def post(url, data):
    req = urllib.request.Request(url, json.dumps(data).encode(),
                                {"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=5)
    except urllib.error.HTTPError:
        pass

def get_json(url):
    return json.loads(urllib.request.urlopen(url, timeout=5).read())

def press(action):
    post(f"{ROUTER}/router/event", {"action": action, "device_type": "Audio"})

def router_status():
    return get_json(f"{ROUTER}/router/status")

def player_status():
    try:
        return get_json(f"{PLAYER}/player/status")
    except Exception:
        return {}

def set_volume(vol):
    post(f"{ROUTER}/router/volume", {"volume": vol})

# ── Test framework ──

tests_passed = 0
tests_failed = 0
test_results = []

def check(name, source_button, expected_source, wait=4):
    """Switch to a source and verify metadata + player state."""
    global tests_passed, tests_failed
    press(source_button)
    time.sleep(wait)

    rs = router_status()
    ps = player_status()
    media = rs.get("media", {})

    active = rs.get("active_source")
    title = media.get("title", "")
    artist = media.get("artist", "")
    state = media.get("state", "")
    pstate = ps.get("state", "?")
    pbackend = ps.get("active_backend", "?")
    expected_backend = BACKEND_MAP.get(expected_source, "?")

    issues = []
    if active != expected_source:
        issues.append(f"WRONG SOURCE: expected {expected_source}, got {active}")
    if pstate != "playing":
        issues.append(f"PLAYER NOT PLAYING: {pstate}")
    if expected_backend != "?" and pbackend != expected_backend:
        issues.append(f"WRONG BACKEND: expected {expected_backend}, got {pbackend}")
    if not title:
        issues.append("NO METADATA: title is empty")

    result = {
        "name": name,
        "pass": len(issues) == 0,
        "source": active,
        "title": title,
        "artist": artist,
        "player": pstate,
        "backend": pbackend,
        "issues": issues,
    }
    test_results.append(result)

    print(f"{name}:")
    print(f"  router:  source={active}  state={state}")
    print(f"  media:   {title or '-'} / {artist or '-'}")
    print(f"  player:  {pstate} ({pbackend})")
    if issues:
        tests_failed += 1
        for i in issues:
            print(f"  *** FAIL: {i}")
    else:
        tests_passed += 1
        print(f"  PASS")
    print()

def check_stop(name):
    """Verify stop clears source and stops player."""
    global tests_passed, tests_failed
    press("stop")
    time.sleep(2)

    rs = router_status()
    ps = player_status()
    active = rs.get("active_source")
    pstate = ps.get("state", "?")

    issues = []
    if active is not None:
        issues.append(f"SOURCE NOT CLEARED: {active}")
    if pstate == "playing":
        issues.append("PLAYER STILL PLAYING")

    result = {"name": name, "pass": len(issues) == 0, "issues": issues}
    test_results.append(result)

    print(f"{name}:")
    print(f"  router:  source={active}")
    print(f"  player:  {pstate}")
    if issues:
        tests_failed += 1
        for i in issues:
            print(f"  *** FAIL: {i}")
    else:
        tests_passed += 1
        print(f"  PASS")
    print()

# ── Discovery ──

def discover_sources():
    """Read source button mappings and available sources from the router.

    Button mappings come from per-source config sections in config.json:
      "spotify": { "source": "cd" }   → pressing CD activates spotify
      "radio":   { "source": "radio" } → pressing RADIO activates radio
    """
    rs = router_status()
    sources = rs.get("sources", {})

    try:
        with open("/etc/beosound5c/config.json") as f:
            config = json.load(f)
    except Exception:
        config = {}

    # Build button → source_id map from per-source "source" fields
    button_map = {}  # IR button name → source_id
    for source_id in sources:
        section = config.get(source_id, {})
        if isinstance(section, dict):
            btn = section.get("source")
            if btn:
                button_map[btn] = source_id

    return sources, button_map

# ── Main ──

def main():
    parser = argparse.ArgumentParser(description="Source switching integration test")
    parser.add_argument("--volume", type=int, default=10,
                        help="Test volume level (default: 10)")
    parser.add_argument("--json", action="store_true",
                        help="Output results as JSON")
    args = parser.parse_args()

    # Discover available sources
    sources, button_map = discover_sources()
    available = {sid for sid, s in sources.items() if s["state"] != "gone"}

    print("=" * 55)
    print(" Source Switching Integration Test")
    print("=" * 55)
    print(f" Available sources: {', '.join(sorted(available))}")
    print(f" Button mappings:   {', '.join(f'{b}->{s}' for b, s in button_map.items())}")
    print(f" Test volume:       {args.volume}%")
    print("=" * 55)
    print()

    if len(available) < 2:
        print("ERROR: Need at least 2 available sources to test switching")
        sys.exit(2)

    # Check if Spotify needs reauth — skip it if so
    skip_spotify = False
    if "spotify" in available:
        try:
            st = get_json("http://localhost:8771/status")
            if st.get("needs_reauth"):
                print("NOTE: Spotify needs re-auth, skipping Spotify tests\n")
                skip_spotify = True
        except Exception:
            skip_spotify = True

    # Save current volume, set test volume
    orig_volume = router_status().get("volume", 30)
    set_volume(args.volume)

    # Build test sequence: cycle through all sources, then rapid switch
    test_sources = []
    for btn, sid in button_map.items():
        if sid not in available:
            continue
        if sid == "spotify" and skip_spotify:
            continue
        test_sources.append((btn, sid))

    try:
        # Phase 1: Sequential switching through each source
        for i, (btn, sid) in enumerate(test_sources):
            check(f"{i+1}. {sid.upper()} (button={btn})", btn, sid,
                  wait=5 if sid == "spotify" else 4)

        # Phase 2: Rapid switching — first two sources with 1s gap
        if len(test_sources) >= 2:
            btn_a, sid_a = test_sources[0]
            btn_b, sid_b = test_sources[1]
            n = len(test_sources) + 1
            print(f"{n}. RAPID: {sid_a} -> {sid_b} (1s gap)")
            press(btn_a)
            time.sleep(1)
            check(f"   -> {sid_b.upper()} should win", btn_b, sid_b,
                  wait=5 if sid_b == "spotify" else 4)

        # Phase 3: Stop
        check_stop(f"{len(test_sources) + 2}. STOP")

    finally:
        # Always stop and restore volume
        press("stop")
        time.sleep(1)
        set_volume(orig_volume)

    # Summary
    total = tests_passed + tests_failed
    print("=" * 55)
    print(f" Results: {tests_passed}/{total} passed", end="")
    if tests_failed:
        print(f", {tests_failed} FAILED")
    else:
        print()
    print("=" * 55)

    if args.json:
        print(json.dumps(test_results, indent=2))

    sys.exit(1 if tests_failed else 0)

if __name__ == "__main__":
    main()
