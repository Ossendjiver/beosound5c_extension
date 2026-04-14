#!/usr/bin/env python3
"""
Comprehensive end-to-end canvas integration tests.

Tests metadata correctness, track transitions (auto-advance, manual skip,
source switch), canvas/non-canvas toggling, and text positioning.
Takes screenshots at key moments for visual verification.

Usage:
    python3 tools/test-canvas-e2e.py [hostname]

Default hostname: beosound5c.local
Screenshots saved to: /tmp/canvas-test-*.png
"""

import asyncio
import json
import os
import subprocess
import sys
import time
import tempfile

# ── Config ──

HOST = sys.argv[1] if len(sys.argv) > 1 else "beosound5c.local"
SCREENSHOT_DIR = "/tmp"

# Tracks in the Audiophile playlist (4cZvqesxx8gPtXM5FkdjY0)
# Canvas availability verified 2026-03-27
AUDIOPHILE_PL = "4cZvqesxx8gPtXM5FkdjY0"
TRACKS = {
    # idx: (name, uri, has_canvas)
    0: ("Brothers in Arms", "spotify:track:6EFVCBRoInFA89V4wCwu9m", False),
    2: ("Hundra lax kärlek", "spotify:track:0w9bEyZtjn3CbHB8JSWZAr", False),
    3: ("Sixteen Tons", "spotify:track:50eBP4arxI9WZqSXAy8j9d", False),
    4: ("Lose Yourself to Dance", "spotify:track:5CMjjywI0eZMixPeqNd75R", True),
}

# Tracks in 80s and 90s soft (5yAZ0IX9zuZS3HnnBvUENb)
SOFT_PL = "5yAZ0IX9zuZS3HnnBvUENb"
SOFT_TRACKS = {
    0: ("I Will Always Love You", "spotify:track:4eHbdreAnSOrDDsFfc4Fpm", True),
    1: ("Without You", "spotify:track:0pkIJFV6mviH9dmBYsFwTM", False),
}

# Liked songs — for cross-playlist test
LIKED_PL = "liked-songs"
LIKED_TRACKS = {
    2: ("What Is This Feeling?", "spotify:track:7eGuPhpdS8sBjPJNuAShUX", True),
}

# ── State ──

passed = 0
failed = 0
warnings = 0
errors = []
cdp_target = None
screenshots = []

# ── Helpers ──

def ssh(cmd, timeout=15):
    r = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", HOST, cmd],
        capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip()

def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failed += 1
        msg = f"  ✗ {name}" + (f" — {detail}" if detail else "")
        print(msg)
        errors.append(msg)
    return condition

def warn(name, detail=""):
    global warnings
    warnings += 1
    print(f"  ⚠ {name}" + (f" — {detail}" if detail else ""))

def section(title):
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}")

def cdp_eval(js, timeout=15):
    """Evaluate JS via Chrome DevTools Protocol."""
    script = f"""import json, asyncio, websockets
async def run():
    async with websockets.connect("ws://localhost:9222/devtools/page/{cdp_target}") as ws:
        await ws.send(json.dumps({{"id":1,"method":"Runtime.evaluate","params":{{"expression":{json.dumps(js)},"returnByValue":True}}}}))
        r = json.loads(await ws.recv())
        v = r.get("result",{{}}).get("result",{{}}).get("value")
        if v is not None:
            print(v if isinstance(v, str) else json.dumps(v))
        else:
            print(json.dumps(r.get("result",{{}}).get("result",{{}})))
asyncio.run(run())
"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(script)
        local_path = f.name
    try:
        subprocess.run(["scp", "-q", local_path, f"{HOST}:/tmp/_cdp_eval.py"],
                       capture_output=True, timeout=5)
        return ssh("python3 /tmp/_cdp_eval.py", timeout=timeout)
    finally:
        os.unlink(local_path)

def cdp_json(js, timeout=15):
    """Evaluate JS that returns JSON, parse result."""
    raw = cdp_eval(f"JSON.stringify({js})", timeout)
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None

def screenshot(name):
    """Take a screenshot, copy locally, return path."""
    fname = f"canvas-test-{name}.png"
    local = os.path.join(SCREENSHOT_DIR, fname)
    ssh('DISPLAY=:0 XAUTHORITY=/home/kirsten/.Xauthority scrot -o /tmp/screen.png')
    subprocess.run(["scp", "-q", f"{HOST}:/tmp/screen.png", local],
                   capture_output=True, timeout=10)
    screenshots.append((name, local))
    print(f"  📷 {fname}")
    return local

def play_track(playlist_id, track_index):
    """Play a track via source command API."""
    return ssh(
        f'curl -s -X POST http://localhost:8771/command '
        f'-H "Content-Type: application/json" '
        f'-d \'{{"command":"play_playlist","playlist_id":"{playlist_id}","track_index":{track_index}}}\''
    )

def send_next():
    """Send next command via source API."""
    return ssh(
        'curl -s -X POST http://localhost:8771/command '
        '-H "Content-Type: application/json" '
        '-d \'{"command":"next"}\''
    )

def send_prev():
    """Send prev command via source API."""
    return ssh(
        'curl -s -X POST http://localhost:8771/command '
        '-H "Content-Type: application/json" '
        '-d \'{"command":"prev"}\''
    )

def get_ui_state():
    """Get comprehensive UI state via CDP."""
    return cdp_json("""{
        title: window.uiStore?.mediaInfo?.title || '',
        artist: window.uiStore?.mediaInfo?.artist || '',
        album: window.uiStore?.mediaInfo?.album || '',
        canvas_url: window.uiStore?.mediaInfo?.canvas_url || '',
        state: window.uiStore?.mediaInfo?.state || '',
        route: window.uiStore?.currentRoute || '',
        canvasActive: !!window.CanvasPanel?.active,
        canvasHas: !!window.CanvasPanel?.hasCanvas,
        canvasCycling: !!window.CanvasPanel?.cycling,
        immersiveActive: !!window.ImmersiveMode?.active,
    }""")

def navigate_to_playing():
    cdp_eval("window.uiStore.navigateToView('menu/playing')")
    time.sleep(1)

def enter_immersive():
    cdp_eval("window.uiStore.setMenuVisible(false); window.ImmersiveMode.enter()")
    time.sleep(2)

def exit_immersive():
    cdp_eval("window.ImmersiveMode.exit(); window.uiStore.setMenuVisible(true)")
    time.sleep(1)

def wait_for_canvas(max_wait=15):
    """Wait until canvas_url appears in UI state (background fetch)."""
    for _ in range(max_wait):
        state = get_ui_state()
        if state and state.get("canvas_url"):
            return state
        time.sleep(1)
    return get_ui_state()


# ── Tests ──

def test_01_services():
    """Verify services are running."""
    section("1. Service health")
    for svc in ["beo-source-spotify", "beo-router", "beo-ui", "beo-player-local"]:
        status = ssh(f"systemctl is-active {svc}")
        check(f"{svc} is active", status == "active", status)

def test_02_play_no_canvas():
    """Play a track without canvas — verify metadata, no canvas_url."""
    section("2. Play track WITHOUT canvas")
    name, uri, _ = TRACKS[0]  # Brothers in Arms

    resp = play_track(AUDIOPHILE_PL, 0)
    check("Play command accepted", '"state"' in resp, resp[:80])
    time.sleep(6)

    state = get_ui_state()
    check(f"Title matches: '{state['title']}'", name in state.get("title", ""), state.get("title"))
    check(f"canvas_url is empty", not state.get("canvas_url"), state.get("canvas_url", "")[:40])
    check("CanvasPanel.hasCanvas is false", not state.get("canvasHas"))

    navigate_to_playing()
    screenshot("02-no-canvas-playing")

def test_03_play_with_canvas():
    """Play a track with canvas — verify canvas_url arrives."""
    section("3. Play track WITH canvas")
    name, uri, _ = TRACKS[4]  # Lose Yourself to Dance

    resp = play_track(AUDIOPHILE_PL, 4)
    check("Play command accepted", '"state"' in resp, resp[:80])

    # Wait for background canvas fetch
    state = wait_for_canvas(15)
    check(f"Title matches: '{state['title']}'", "Lose Yourself" in state.get("title", ""), state.get("title"))
    check("canvas_url present", bool(state.get("canvas_url")), "no canvas_url")
    check("CanvasPanel.hasCanvas is true", state.get("canvasHas"))

    navigate_to_playing()
    screenshot("03-canvas-track-playing")

def test_04_immersive_canvas_cycle():
    """Enter immersive mode — verify canvas cycling activates."""
    section("4. Immersive mode canvas cycling")

    # Should already be on Lose Yourself to Dance (canvas track)
    navigate_to_playing()
    enter_immersive()
    screenshot("04a-immersive-artwork")

    # Wait for canvas to show (artwork shows for 8s, then canvas fades in)
    time.sleep(10)
    state = get_ui_state()
    if state.get("canvasActive"):
        check("Canvas active after cycle", True)
        screenshot("04b-immersive-canvas")
    else:
        # Might still be in artwork phase — check cycling at least
        check("Canvas cycling started", state.get("canvasCycling"), "cycling not started")
        time.sleep(8)
        state = get_ui_state()
        check("Canvas active after extra wait", state.get("canvasActive"))
        screenshot("04b-immersive-canvas")

    exit_immersive()

def test_05_text_position():
    """Verify text is at exactly the same position in canvas and artwork modes."""
    section("5. Text positioning (canvas vs artwork)")

    navigate_to_playing()
    enter_immersive()

    # Get immersive overlay text position
    artwork_pos = cdp_json("""(function() {
        var el = document.querySelector('.immersive-info');
        if (!el) return null;
        var r = el.getBoundingClientRect();
        return {left: Math.round(r.left), top: Math.round(r.top),
                width: Math.round(r.width), opacity: getComputedStyle(el).opacity};
    })()""")

    if not artwork_pos:
        check("Immersive overlay exists", False, "no .immersive-info element")
        exit_immersive()
        return

    print(f"  Artwork text pos: left={artwork_pos['left']}, top={artwork_pos['top']}, w={artwork_pos['width']}")
    screenshot("05a-text-artwork-mode")

    # Wait for canvas to show, then check mirror position
    time.sleep(12)
    state = get_ui_state()
    if not state.get("canvasActive"):
        warn("Canvas didn't activate for position test — checking mirror anyway")

    canvas_pos = cdp_json("""(function() {
        var el = document.querySelector('.canvas-text-mirror');
        if (!el) return null;
        var r = el.getBoundingClientRect();
        return {left: Math.round(r.left), top: Math.round(r.top),
                width: Math.round(r.width), html: el.innerHTML.length};
    })()""")

    if not canvas_pos:
        check("Canvas text mirror exists", False, "no .canvas-text-mirror element")
    else:
        print(f"  Canvas text pos:  left={canvas_pos['left']}, top={canvas_pos['top']}, w={canvas_pos['width']}")
        check("Text left matches", abs(artwork_pos['left'] - canvas_pos['left']) <= 2,
              f"artwork={artwork_pos['left']}, canvas={canvas_pos['left']}")
        check("Text top matches", abs(artwork_pos['top'] - canvas_pos['top']) <= 2,
              f"artwork={artwork_pos['top']}, canvas={canvas_pos['top']}")
        check("Text width matches", abs(artwork_pos['width'] - canvas_pos['width']) <= 2,
              f"artwork={artwork_pos['width']}, canvas={canvas_pos['width']}")
        check("Mirror has content", canvas_pos.get('html', 0) > 10, f"html length: {canvas_pos.get('html')}")
        screenshot("05b-text-canvas-mode")

    exit_immersive()

def test_06_manual_next_canvas_to_no_canvas():
    """Skip from canvas track to non-canvas — verify canvas clears, metadata updates."""
    section("6. Manual NEXT: canvas → no canvas")

    # Play Lose Yourself to Dance (canvas, idx 4)
    play_track(AUDIOPHILE_PL, 4)
    state = wait_for_canvas(12)
    check("Starting on canvas track", bool(state.get("canvas_url")))
    old_title = state.get("title", "")

    # Next should go to idx 5 (whatever that is — probably no canvas)
    send_next()
    time.sleep(4)

    state = get_ui_state()
    check("Title changed after next", state.get("title") != old_title,
          f"still: {state.get('title')}")
    check("canvas_url cleared after next to non-canvas track",
          not state.get("canvas_url"),
          f"canvas_url: {state.get('canvas_url', '')[:40]}")
    check("CanvasPanel not active", not state.get("canvasActive"))

    navigate_to_playing()
    screenshot("06-after-next-no-canvas")

def test_07_manual_next_no_canvas_to_canvas():
    """Skip from non-canvas to canvas track — verify canvas loads."""
    section("7. Manual NEXT: no canvas → canvas")

    # Play Sixteen Tons (no canvas, idx 3) → next → Lose Yourself to Dance (canvas, idx 4)
    play_track(AUDIOPHILE_PL, 3)
    time.sleep(5)
    state = get_ui_state()
    check("Starting on non-canvas track", not state.get("canvas_url"), state.get("canvas_url", "")[:40])

    send_next()
    state = wait_for_canvas(12)
    check("canvas_url present after skip to canvas track", bool(state.get("canvas_url")))
    check("CanvasPanel has canvas", state.get("canvasHas"))

    navigate_to_playing()
    screenshot("07-after-next-with-canvas")

def test_08_manual_prev():
    """Skip backwards — verify metadata and canvas update."""
    section("8. Manual PREV")

    # Re-play Lose Yourself to Dance (canvas, idx 4), then PREV immediately
    # so the player goes to previous track (not restart current).
    # Players restart current track if >3s in, so we send PREV within 2s.
    play_track(AUDIOPHILE_PL, 4)
    time.sleep(2)  # Short wait — still in first few seconds
    send_prev()
    time.sleep(5)

    state = get_ui_state()
    check("Title is Sixteen Tons after prev", "Sixteen" in state.get("title", ""),
          f"title: {state.get('title')}")
    check("canvas_url cleared after prev to non-canvas", not state.get("canvas_url"))

    navigate_to_playing()
    screenshot("08-after-prev")

def test_09_auto_advance():
    """Let a short section play and verify auto-advance updates metadata correctly.
    We simulate this by playing a track and sending next after a delay,
    then checking the poll picks up the correct track."""
    section("9. Auto-advance detection (simulated via next)")

    # Play I Will Always Love You (canvas, idx 0 in SOFT_PL)
    play_track(SOFT_PL, 0)
    state = wait_for_canvas(12)
    first_title = state.get("title", "")
    check("Playing canvas track", "Always" in first_title, first_title)

    # Skip to next — Without You (no canvas, idx 1)
    send_next()
    time.sleep(5)

    state = get_ui_state()
    check("Title changed to next track", state.get("title") != first_title,
          f"still: {state.get('title')}")
    check("New title is correct", "Without" in state.get("title", ""),
          f"got: {state.get('title')}")
    check("canvas_url cleared for non-canvas track", not state.get("canvas_url"))

    navigate_to_playing()
    screenshot("09-auto-advance")

def test_10_cross_playlist():
    """Switch between playlists — verify metadata updates correctly."""
    section("10. Cross-playlist switch")

    # Play from one playlist
    play_track(AUDIOPHILE_PL, 0)  # Brothers in Arms (no canvas)
    time.sleep(5)
    state1 = get_ui_state()
    check("First playlist track playing", "Brothers" in state1.get("title", ""), state1.get("title"))

    # Switch to another playlist with canvas
    play_track(LIKED_PL, 2)  # What Is This Feeling? (canvas)
    state2 = wait_for_canvas(12)
    check("Second playlist track title correct", "Feeling" in state2.get("title", ""), state2.get("title"))
    check("canvas_url present after cross-playlist switch", bool(state2.get("canvas_url")))

    navigate_to_playing()
    screenshot("10-cross-playlist-canvas")

def test_11_source_switch():
    """Switch away from Spotify — verify canvas clears."""
    section("11. Source switch (Spotify → other)")

    # Should be on a canvas track from test 10
    state = get_ui_state()
    had_canvas = bool(state.get("canvas_url"))
    if not had_canvas:
        play_track(LIKED_PL, 2)
        wait_for_canvas(12)

    navigate_to_playing()
    enter_immersive()
    time.sleep(3)
    screenshot("11a-canvas-before-source-switch")

    # Navigate away (simulate source switch by going to menu)
    exit_immersive()
    cdp_eval("window.uiStore.navigateToView('menu/spotify')")
    time.sleep(2)

    state = get_ui_state()
    check("Canvas cycling stopped after leaving playing", not state.get("canvasCycling"))
    check("Canvas not active after leaving playing", not state.get("canvasActive"))
    screenshot("11b-after-source-switch")

def test_12_return_to_playing():
    """Return to playing view — verify canvas resumes."""
    section("12. Return to PLAYING view")

    navigate_to_playing()
    time.sleep(2)

    state = get_ui_state()
    check("Route is menu/playing", state.get("route") == "menu/playing")
    check("canvas_url still present", bool(state.get("canvas_url")))
    check("CanvasPanel has canvas ready", state.get("canvasHas"))
    screenshot("12-return-to-playing")

def test_13_rapid_skip():
    """Rapid next/next/next — verify final state is correct (no stale metadata)."""
    section("13. Rapid skip (3x next)")

    play_track(AUDIOPHILE_PL, 0)  # idx 0
    time.sleep(4)

    # Rapid triple skip
    send_next()  # idx 1
    time.sleep(0.5)
    send_next()  # idx 2
    time.sleep(0.5)
    send_next()  # idx 3 = Sixteen Tons

    time.sleep(6)
    state = get_ui_state()

    # Should be on idx 3 (Sixteen Tons) — not stuck on an earlier track
    # Allow for timing variance: might be on idx 2 or 3
    title = state.get("title", "")
    check("After rapid skip, title is not first track",
          "Brothers" not in title, f"still on: {title}")
    print(f"  Final title: {title}")

    # Canvas should be clear (Sixteen Tons has no canvas)
    # Unless we landed on a canvas track
    navigate_to_playing()
    screenshot("13-rapid-skip")

def test_14_no_flicker_transition():
    """Play canvas track, enter immersive, let canvas cycle,
    then skip to another canvas track — verify no flicker (cycling restarts cleanly)."""
    section("14. Canvas → canvas transition (no flicker)")

    # Play What Is This Feeling (canvas)
    play_track(LIKED_PL, 2)
    wait_for_canvas(12)

    navigate_to_playing()
    enter_immersive()
    time.sleep(10)  # Let canvas cycle activate

    state = get_ui_state()
    check("Canvas cycling active", state.get("canvasCycling"), "cycling not started")
    screenshot("14a-first-canvas-cycling")

    # Now play I Will Always Love You (also canvas, different playlist)
    play_track(SOFT_PL, 0)
    time.sleep(2)
    screenshot("14b-mid-transition")

    state2 = wait_for_canvas(12)
    check("New track title correct", "Always" in state2.get("title", ""), state2.get("title"))
    check("New canvas_url loaded", bool(state2.get("canvas_url")))

    # Re-enter immersive for the new track
    navigate_to_playing()
    enter_immersive()
    time.sleep(10)

    state3 = get_ui_state()
    check("Canvas cycling restarted for new track", state3.get("canvasCycling"))
    screenshot("14c-second-canvas-cycling")

    exit_immersive()


# ── Main ──

def main():
    global cdp_target, passed, failed

    print(f"Canvas E2E Test Suite — {HOST}")
    print(f"{'=' * 60}")
    print(f"Screenshots: {SCREENSHOT_DIR}/canvas-test-*.png\n")

    # Get CDP target
    target_json = ssh("curl -s http://localhost:9222/json")
    try:
        targets = json.loads(target_json)
        cdp_target = targets[0]["id"]
    except Exception:
        print(f"FATAL: Cannot get CDP target")
        print(f"Response: {target_json[:200]}")
        sys.exit(1)
    print(f"CDP target: {cdp_target}")

    # Navigate to playing view to start clean
    cdp_eval("window.uiStore.navigateToView('menu/playing')")
    time.sleep(1)

    tests = [
        test_01_services,
        test_02_play_no_canvas,
        test_03_play_with_canvas,
        test_04_immersive_canvas_cycle,
        test_05_text_position,
        test_06_manual_next_canvas_to_no_canvas,
        test_07_manual_next_no_canvas_to_canvas,
        test_08_manual_prev,
        test_09_auto_advance,
        test_10_cross_playlist,
        test_11_source_switch,
        test_12_return_to_playing,
        test_13_rapid_skip,
        test_14_no_flicker_transition,
    ]

    for test_fn in tests:
        try:
            test_fn()
        except Exception as e:
            failed += 1
            errors.append(f"  ✗ {test_fn.__name__} CRASHED: {e}")
            print(f"  ✗ CRASH: {e}")

    # Summary
    print(f"\n{'=' * 60}")
    total = passed + failed
    print(f"  Results: {passed}/{total} passed, {failed} failed, {warnings} warnings")
    if errors:
        print(f"\n  Failures:")
        for e in errors:
            print(f"    {e}")
    if screenshots:
        print(f"\n  Screenshots ({len(screenshots)}):")
        for name, path in screenshots:
            print(f"    {path}")
    print(f"{'=' * 60}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
