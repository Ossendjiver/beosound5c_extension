#!/usr/bin/env python3
"""
End-to-end test for Spotify Canvas pipeline.

Tests the full chain: canvas fetch → source_base → router → WebSocket → UI → video element.
Run from dev machine against a live device.

Usage:
    python3 tools/test-canvas-pipeline.py [hostname]

Default hostname: beosound5c.local
Requires: SP_DC or SPOTIFY_SP_DC env var, SSH access to device.
"""

import asyncio
import json
import os
import subprocess
import sys
import time

# ── Config ──

HOST = sys.argv[1] if len(sys.argv) > 1 else "beosound5c.local"

# Known tracks: (name, uri, has_canvas)
# has_canvas verified against canvaz-cache API as of 2026-03-25
TEST_TRACKS = [
    ("Tusen spänn", "spotify:track:01RdEXps15f3VmQMV6OuTM", True),
    ("Lemon Tree", "spotify:track:2epbL7s3RFV81K5UhTgZje", True),
    ("Zombie", "spotify:track:7EZC6E7UjZe63f1jRmkWxt", True),
    ("Semester", "spotify:track:160hD5JOJTqyQGcZPKNLBJ", False),
]

# ── Helpers ──

passed = 0
failed = 0
errors = []

def ssh(cmd, timeout=10):
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

def section(title):
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}")


# ── Tests ──

def test_1_services():
    """Verify all required services are running."""
    section("1. Service health")
    for svc in ["beo-source-spotify", "beo-router", "beo-ui", "beo-player-local"]:
        status = ssh(f"systemctl is-active {svc}")
        check(f"{svc} is active", status == "active", status)

def test_2_sp_dc():
    """Verify SPOTIFY_SP_DC is in the service environment."""
    section("2. Canvas credentials")
    env = ssh("sudo cat /proc/$(pgrep -f 'sources/spotify/service.py' | head -1)/environ 2>/dev/null | tr '\\0' '\\n' | grep SPOTIFY_SP_DC | wc -l")
    check("SPOTIFY_SP_DC in service env", env.strip() == "1", f"found {env.strip()} matches")

def test_3_cli_canvas():
    """Verify CLI tool can fetch canvas for known tracks."""
    section("3. Canvas API (CLI)")
    sp_dc = os.environ.get("SP_DC") or os.environ.get("SPOTIFY_SP_DC")
    if not sp_dc:
        check("SP_DC env var set", False, "set SP_DC or SPOTIFY_SP_DC")
        return

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "lib"))
    from spotify_canvas import SpotifyCanvasClient
    client = SpotifyCanvasClient(sp_dc=sp_dc)

    for name, uri, expected in TEST_TRACKS:
        url = asyncio.run(client.get_canvas_url(uri))
        has = bool(url)
        check(
            f"{name}: canvas={'yes' if has else 'no'} (expected={'yes' if expected else 'no'})",
            has == expected,
            f"url={url[:50]}..." if url else "no url"
        )

def test_4_play_and_check(cdp_target):
    """Play each test track and verify canvas_url reaches the UI."""
    section("4. Full pipeline (play → router → UI)")

    # Find the playlist that has our test tracks
    playlist_data = ssh('python3 -c "import json;pl=json.load(open(\'/home/kirsten/beosound5c/web/json/spotify_playlists.json\'));[print(json.dumps({\\\"id\\\":p[\\\"id\\\"],\\\"tracks\\\":[t[\\\"uri\\\"] for t in p.get(\\\"tracks\\\",[])]}) ) for p in pl]"')

    # Build uri → (playlist_id, index) map
    uri_to_loc = {}
    for line in playlist_data.strip().split("\n"):
        try:
            pl = json.loads(line)
            for i, track_uri in enumerate(pl["tracks"]):
                if track_uri not in uri_to_loc:
                    uri_to_loc[track_uri] = (pl["id"], i)
        except:
            continue

    for name, uri, expected_canvas in TEST_TRACKS:
        loc = uri_to_loc.get(uri)
        if not loc:
            check(f"{name}: found in playlists", False, "track not in any playlist")
            continue

        pl_id, idx = loc

        # Play the track
        play_resp = ssh(
            f'curl -s -X POST http://localhost:8771/command '
            f'-H "Content-Type: application/json" '
            f'-d \'{{"command":"play_playlist","playlist_id":"{pl_id}","track_index":{idx}}}\''
        )
        check(f"{name}: play command accepted", '"ok"' in play_resp, play_resp[:80])

        # Wait for canvas fetch + media update (canvas is fetched async, needs time)
        time.sleep(12)

        # Check UI state via CDP
        js = (
            "JSON.stringify({"
            "title:window.uiStore.mediaInfo.title,"
            "canvas_url:window.uiStore.mediaInfo.canvas_url||'',"
            "hasCanvas:window.CanvasPanel.hasCanvas"
            "})"
        )
        result = cdp_eval(cdp_target, js)
        try:
            state = json.loads(result)
        except:
            check(f"{name}: CDP query", False, f"invalid response: {result}")
            continue

        has_canvas_url = bool(state.get("canvas_url"))
        has_canvas_ready = state.get("hasCanvas", False)

        check(
            f"{name}: canvas_url in UI = {has_canvas_url} (expected {expected_canvas})",
            has_canvas_url == expected_canvas,
            f"canvas_url={'yes' if has_canvas_url else 'no'}"
        )

        if expected_canvas:
            check(
                f"{name}: video preloaded (hasCanvas={has_canvas_ready})",
                has_canvas_ready,
            )

def test_5_toggle(cdp_target):
    """Test canvas toggle on/off for a track with canvas."""
    section("5. Canvas toggle")

    # Should already be on a track from test 4 — find one with canvas
    js = "JSON.stringify({has:window.CanvasPanel.hasCanvas, active:window.CanvasPanel.active})"
    state = json.loads(cdp_eval(cdp_target, js))

    if not state.get("has"):
        # Play a track with canvas first
        ssh(
            'curl -s -X POST http://localhost:8771/command '
            '-H "Content-Type: application/json" '
            '-d \'{"command":"play_playlist","playlist_id":"7BtiUiOcjrnjYO6ej7uYRz","track_index":0}\''
        )
        time.sleep(8)

    # Enter immersive
    cdp_eval(cdp_target, "window.uiStore.navigateToView('menu/playing')")
    time.sleep(1)
    cdp_eval(cdp_target, "window.uiStore.setMenuVisible(false);window.ImmersiveMode.enter()")
    time.sleep(2)

    # Check indicator
    ind = cdp_eval(cdp_target, "var el = document.querySelector('.canvas-indicator-visible'); String(!!el)")
    check("Indicator visible in immersive", ind == "true", f"got: {ind}")

    # Show canvas
    cdp_eval(cdp_target, "window.CanvasPanel.show()")
    time.sleep(2)
    active = cdp_eval(cdp_target, "String(window.CanvasPanel.active)")
    check("Canvas show() activates", active == "true")

    # Check text overlay
    text = cdp_eval(cdp_target, "document.querySelector('.canvas-text-title').textContent")
    check("Canvas text overlay has title", bool(text) and text != "—", f"text: {text}")

    # Hide canvas
    cdp_eval(cdp_target, "window.CanvasPanel.hide()")
    time.sleep(1)
    active = cdp_eval(cdp_target, "String(window.CanvasPanel.active)")
    check("Canvas hide() deactivates", active == "false")

def test_6_track_switch(cdp_target):
    """Test switching from canvas track to non-canvas track clears canvas."""
    section("6. Track switch (canvas → no canvas)")

    # Play track WITH canvas
    ssh(
        'curl -s -X POST http://localhost:8771/command '
        '-H "Content-Type: application/json" '
        '-d \'{"command":"play_playlist","playlist_id":"7BtiUiOcjrnjYO6ej7uYRz","track_index":0}\''
    )
    time.sleep(8)
    has1 = cdp_eval(cdp_target, "String(!!window.uiStore.mediaInfo.canvas_url)")
    check("Track with canvas: canvas_url present", has1 == "true")

    # Play track WITHOUT canvas
    ssh(
        'curl -s -X POST http://localhost:8771/command '
        '-H "Content-Type: application/json" '
        '-d \'{"command":"play_playlist","playlist_id":"7BtiUiOcjrnjYO6ej7uYRz","track_index":3}\''
    )
    time.sleep(8)
    title = cdp_eval(cdp_target, "window.uiStore.mediaInfo.title")
    has2 = cdp_eval(cdp_target, "String(!!window.uiStore.mediaInfo.canvas_url)")
    check(f"Track without canvas ({title}): canvas_url cleared", has2 == "false", f"canvas_url present: {has2}")


# ── CDP helper ──

def cdp_eval(target, js):
    """Evaluate JS via Chrome DevTools Protocol."""
    import tempfile
    script = f"""import json, asyncio, websockets
async def run():
    async with websockets.connect("ws://localhost:9222/devtools/page/{target}") as ws:
        await ws.send(json.dumps({{"id":1,"method":"Runtime.evaluate","params":{{"expression":{json.dumps(js)}}}}}))
        r = json.loads(await ws.recv())
        v = r.get("result",{{}}).get("result",{{}}).get("value")
        if v is not None:
            print(v)
        else:
            print(json.dumps(r.get("result",{{}}).get("result",{{}})))
asyncio.run(run())
"""
    # Write script to temp file and scp to device to avoid SSH quoting hell
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(script)
        local_path = f.name
    try:
        subprocess.run(["scp", "-q", local_path, f"{HOST}:/tmp/_cdp_eval.py"],
                       capture_output=True, timeout=5)
        return ssh("python3 /tmp/_cdp_eval.py", timeout=15)
    finally:
        os.unlink(local_path)


# ── Main ──

def main():
    global passed, failed

    print(f"Canvas Pipeline Test — {HOST}")
    print(f"{'=' * 60}")

    # Get CDP target
    target_json = ssh("curl -s http://localhost:9222/json")
    try:
        cdp_target = json.loads(target_json)[0]["id"]
    except:
        print(f"FATAL: Cannot get CDP target from device")
        print(f"Response: {target_json[:200]}")
        sys.exit(1)
    print(f"CDP target: {cdp_target}")

    test_1_services()
    test_2_sp_dc()
    test_3_cli_canvas()
    test_4_play_and_check(cdp_target)
    test_5_toggle(cdp_target)
    test_6_track_switch(cdp_target)

    # Summary
    print(f"\n{'=' * 60}")
    total = passed + failed
    print(f"  Results: {passed}/{total} passed, {failed} failed")
    if errors:
        print(f"\n  Failures:")
        for e in errors:
            print(f"  {e}")
    print(f"{'=' * 60}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
