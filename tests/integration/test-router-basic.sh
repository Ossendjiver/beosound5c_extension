#!/bin/bash
# ─────────────────────────────────────────────────────────────────────
# Basic Router Media Tests
#
# Tests the router's media POST, WebSocket push, suppression logic,
# and frontend wiring — without requiring a Sonos speaker.
#
# Usage:
#   ./tests/integration/test-router-basic.sh
#   HOST=beosound5c-kitchen.local ./tests/integration/test-router-basic.sh
#
# Prerequisites:
#   - beo-router + beo-http + beo-ui running on device
#   - No Sonos or specific player config required
# ─────────────────────────────────────────────────────────────────────

HOST="${HOST:-beosound5c.local}"
ROUTER="http://localhost:8770"
PASS=0
FAIL=0

run_on() { ssh -o ConnectTimeout=5 "$HOST" "$@" 2>/dev/null; }

test_result() {
    local name="$1" ok="$2" detail="$3"
    if [ "$ok" = "true" ]; then
        printf "  \033[32mPASS\033[0m  %s\n" "$name"
        PASS=$((PASS + 1))
    else
        printf "  \033[31mFAIL\033[0m  %s — %s\n" "$name" "$detail"
        FAIL=$((FAIL + 1))
    fi
}

echo "═══════════════════════════════════════════════════"
echo " Basic Router Media Tests"
echo " Device: $HOST"
echo "═══════════════════════════════════════════════════"
echo ""

# Check connectivity
if ! ssh -o ConnectTimeout=3 "$HOST" "true" 2>/dev/null; then
    echo "ERROR: Cannot connect to $HOST"
    exit 1
fi

# ── Test 1: POST /router/media returns 200 OK ──
echo "Test 1: POST /router/media accepts media data"
RESP=$(run_on "curl -s -w '\n%{http_code}' -X POST $ROUTER/router/media \
  -H 'Content-Type: application/json' \
  -d '{\"title\":\"Test Song\",\"artist\":\"Test Artist\",\"album\":\"Test Album\",\"artwork\":\"\",\"state\":\"playing\",\"_reason\":\"test\"}'")
HTTP_CODE=$(echo "$RESP" | tail -1)
BODY=$(echo "$RESP" | head -1)
STATUS=$(echo "$BODY" | python3 -c "import sys,json;print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "")
if [ "$HTTP_CODE" = "200" ]; then
    test_result "POST /router/media → 200" "true" ""
else
    test_result "POST /router/media → 200" "false" "HTTP $HTTP_CODE"
fi

# ── Test 2: POST /router/media returns status=ok (no active source) ──
echo "Test 2: Media POST returns status=ok when no local source active"
if [ "$STATUS" = "ok" ]; then
    test_result "status=ok (no suppression)" "true" ""
else
    test_result "status=ok (no suppression)" "false" "status=$STATUS"
fi

# ── Test 3: GET /router/ws connects and receives cached state ──
# Router replays multiple messages on connect (source_change if active,
# volume_update always, media_update if cached). Loop until we see the
# media_update or time out.
echo "Test 3: WebSocket connect receives cached media state"
WS_MSG=$(run_on 'timeout 5 python3 -c "
import asyncio, websockets, json
async def test():
    async with websockets.connect(\"ws://localhost:8770/router/ws\") as ws:
        for _ in range(8):
            msg = await asyncio.wait_for(ws.recv(), timeout=2)
            data = json.loads(msg)
            if data.get(\"type\") == \"media_update\":
                print(json.dumps({\"type\": data[\"type\"], \"reason\": data.get(\"reason\",\"\"), \"title\": data.get(\"data\",{}).get(\"title\",\"\")}))
                return
        print(json.dumps({\"type\":\"\",\"reason\":\"\",\"title\":\"\"}))
asyncio.run(test())
"' || echo '{}')
WS_TYPE=$(echo "$WS_MSG" | python3 -c "import sys,json;print(json.load(sys.stdin).get('type',''))" 2>/dev/null || echo "")
WS_REASON=$(echo "$WS_MSG" | python3 -c "import sys,json;print(json.load(sys.stdin).get('reason',''))" 2>/dev/null || echo "")
WS_TITLE=$(echo "$WS_MSG" | python3 -c "import sys,json;print(json.load(sys.stdin).get('title',''))" 2>/dev/null || echo "")
if [ "$WS_TYPE" = "media_update" ] && [ "$WS_REASON" = "client_connect" ]; then
    test_result "WS receives cached media_update" "true" ""
else
    test_result "WS receives cached media_update" "false" "type=$WS_TYPE reason=$WS_REASON"
fi

# ── Test 4: Cached state has correct data ──
echo "Test 4: Cached state contains posted title"
if [ "$WS_TITLE" = "Test Song" ]; then
    test_result "Cached title = 'Test Song'" "true" ""
else
    test_result "Cached title = 'Test Song'" "false" "title=$WS_TITLE"
fi

# ── Test 5: WS push — POST media and verify WS client receives it ──
echo "Test 5: POST media pushes to connected WS clients"
PUSH_RESULT=$(run_on 'timeout 6 python3 -c "
import asyncio, websockets, json, aiohttp
async def test():
    async with websockets.connect(\"ws://localhost:8770/router/ws\") as ws:
        # Drain replay messages (source_change/volume_update/media_update)
        try:
            while True:
                await asyncio.wait_for(ws.recv(), timeout=0.5)
        except asyncio.TimeoutError:
            pass
        # Now POST new media
        async with aiohttp.ClientSession() as s:
            await s.post(\"http://localhost:8770/router/media\", json={
                \"title\": \"Push Test\", \"artist\": \"Push Artist\", \"album\": \"Push Album\",
                \"artwork\": \"\", \"state\": \"playing\", \"_reason\": \"push_test\"
            })
        # Wait for the matching media_update push
        for _ in range(8):
            msg = await asyncio.wait_for(ws.recv(), timeout=2)
            data = json.loads(msg)
            if data.get(\"type\") == \"media_update\":
                print(data.get(\"data\",{}).get(\"title\",\"\"))
                return
        print(\"\")
asyncio.run(test())
"' || echo "")
if [ "$PUSH_RESULT" = "Push Test" ]; then
    test_result "WS push received with correct title" "true" ""
else
    test_result "WS push received with correct title" "false" "title=$PUSH_RESULT"
fi

# ── Test 6: Inactive-source media is dropped ──
# The router rejects media tagged with a _source_id that isn't the
# currently active source. Player-originated media (_source_id absent)
# is always accepted — the player owns metadata once external playback
# starts. So we test the inactive-source case explicitly.
echo "Test 6: Media tagged with non-active _source_id is dropped"
# Register a fake local source as playing — it becomes the active source
run_on "curl -s -X POST $ROUTER/router/source \
  -H 'Content-Type: application/json' \
  -d '{\"id\":\"test_local\",\"state\":\"playing\",\"name\":\"Test Local\",\"command_url\":\"http://localhost:9999/cmd\",\"player\":\"local\",\"handles\":[\"play\",\"pause\"]}'" > /dev/null
SUPP_RESP=$(run_on "curl -s -X POST $ROUTER/router/media \
  -H 'Content-Type: application/json' \
  -d '{\"title\":\"Should Drop\",\"artist\":\"X\",\"_reason\":\"test\",\"_source_id\":\"some_other_source\"}'")
SUPP_DROPPED=$(echo "$SUPP_RESP" | python3 -c "import sys,json;print(json.load(sys.stdin).get('dropped',''))" 2>/dev/null || echo "")
SUPP_REASON=$(echo "$SUPP_RESP" | python3 -c "import sys,json;print(json.load(sys.stdin).get('reason',''))" 2>/dev/null || echo "")
if [ "$SUPP_DROPPED" = "True" ] && [ "$SUPP_REASON" = "inactive_source" ]; then
    test_result "POST with non-active _source_id is dropped" "true" ""
else
    test_result "POST with non-active _source_id is dropped" "false" "dropped=$SUPP_DROPPED reason=$SUPP_REASON"
fi

# ── Test 7: After local source deactivates, POST without _source_id is accepted ──
echo "Test 7: Media flows after local source deactivated"
run_on "curl -s -X POST $ROUTER/router/source \
  -H 'Content-Type: application/json' \
  -d '{\"id\":\"test_local\",\"state\":\"gone\"}'" > /dev/null
UNSUPP_RESP=$(run_on "curl -s -X POST $ROUTER/router/media \
  -H 'Content-Type: application/json' \
  -d '{\"title\":\"Should Flow\",\"artist\":\"Y\",\"_reason\":\"test\"}'")
UNSUPP_STATUS=$(echo "$UNSUPP_RESP" | python3 -c "import sys,json;print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "")
if [ "$UNSUPP_STATUS" = "ok" ]; then
    test_result "POST returns ok after source gone" "true" ""
else
    test_result "POST returns ok after source gone" "false" "status=$UNSUPP_STATUS"
fi

# ── Test 8: Router status endpoint still works ──
echo "Test 8: GET /router/status returns valid JSON"
STATUS_RESP=$(run_on "curl -s $ROUTER/router/status")
HAS_VOLUME=$(echo "$STATUS_RESP" | python3 -c "import sys,json;d=json.load(sys.stdin);print('yes' if 'volume' in d else 'no')" 2>/dev/null || echo "no")
if [ "$HAS_VOLUME" = "yes" ]; then
    test_result "/router/status returns valid data" "true" ""
else
    test_result "/router/status returns valid data" "false" "has_volume=$HAS_VOLUME"
fi

# ── Test 9: Frontend has no src="" in DOM (broken image fix) ──
echo "Test 9: No src=\"\" in playing view DOM"
CDP_TARGETS=$(run_on "curl -s http://localhost:9222/json" 2>/dev/null || echo "[]")
# Find the main UI page — it loads softarc/index.html (or root /). Skip
# config/system pages which don't carry the media WS. Fall back to the
# first page target if nothing better matches.
MAIN_WS_URL=$(echo "$CDP_TARGETS" | python3 -c "
import sys, json
targets = [t for t in json.load(sys.stdin) if t.get('type') == 'page']
def is_main(t):
    u = t.get('url','')
    return ('localhost/' in u or 'localhost:' in u) and not (
        'config.html' in u or 'system.html' in u or '/devtools/' in u)
main = next((t for t in targets if is_main(t)), None) or (targets[0] if targets else None)
if main: print(main['webSocketDebuggerUrl'])
" 2>/dev/null || echo "")

if [ -n "$MAIN_WS_URL" ]; then
    SRC_EMPTY=$(run_on "python3 -c \"
import json, asyncio, websockets
async def run():
    async with websockets.connect('$MAIN_WS_URL') as ws:
        await ws.send(json.dumps({'id':1,'method':'Runtime.evaluate','params':{'expression':'Array.from(document.querySelectorAll(\\\"img\\\")).filter(i => i.getAttribute(\\\"src\\\") === \\\"\\\").length'}}))
        r = json.loads(await ws.recv())
        print(r.get('result',{}).get('result',{}).get('value', -1))
asyncio.run(run())
\"" || echo "-1")
    if [ "$SRC_EMPTY" = "0" ]; then
        test_result "No img with src=\"\" in DOM" "true" ""
    else
        test_result "No img with src=\"\" in DOM" "false" "count=$SRC_EMPTY"
    fi
else
    test_result "No img with src=\"\" in DOM" "false" "Could not find CDP target"
fi

# ── Test 10: Frontend media WS connected to router ──
echo "Test 10: Frontend media WS connects to router port 8770"
if [ -n "$MAIN_WS_URL" ]; then
    MEDIA_WS_URL=$(run_on "python3 -c \"
import json, asyncio, websockets
async def run():
    async with websockets.connect('$MAIN_WS_URL') as ws:
        await ws.send(json.dumps({'id':1,'method':'Runtime.evaluate','params':{'expression':'window.mediaWebSocket ? window.mediaWebSocket.url : \\\"not connected\\\"'}}))
        r = json.loads(await ws.recv())
        print(r.get('result',{}).get('result',{}).get('value', ''))
asyncio.run(run())
\"" || echo "")
    case "$MEDIA_WS_URL" in
        *8770/router/ws*)
            test_result "Media WS URL points to router:8770" "true" ""
            ;;
        *)
            test_result "Media WS URL points to router:8770" "false" "url=$MEDIA_WS_URL"
            ;;
    esac
else
    test_result "Media WS URL points to router:8770" "false" "Could not find CDP target"
fi

echo ""
echo "═══════════════════════════════════════════════════"
printf " Results: \033[32m%d passed\033[0m" "$PASS"
[ "$FAIL" -gt 0 ] && printf ", \033[31m%d failed\033[0m" "$FAIL"
echo ""
echo "═══════════════════════════════════════════════════"
exit $FAIL
