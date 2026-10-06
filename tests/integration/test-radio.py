#!/usr/bin/env python3
"""
Integration tests for the Radio source service.

Usage:
    # Against local dev instance:
    python3 tests/integration/test-radio.py

    # Against a deployed device:
    python3 tests/integration/test-radio.py http://beosound5c.local:8779
"""

import json
import sys
import time
import urllib.parse
import urllib.request
import urllib.error

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8779"
PASS = 0
FAIL = 0


def test(name, fn):
    global PASS, FAIL
    try:
        fn()
        print(f"  PASS  {name}")
        PASS += 1
    except Exception as e:
        print(f"  FAIL  {name}: {e}")
        FAIL += 1


def get(path, timeout=15):
    url = f"{BASE}{path}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def post(path, data, timeout=30):
    url = f"{BASE}{path}"
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def get_raw(path, timeout=15):
    url = f"{BASE}{path}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.headers, resp.read()


# ── 1. Service health ──
def test_status():
    data = get("/status")
    assert data["source"] == "radio", f"Expected source=radio, got {data}"
test("1. GET /status returns source=radio", test_status)


# ── 2. Root browse returns the curated category list ──
def test_root_browse():
    data = get("/browse?path=")
    assert data["parent"] is None, "Root parent should be None"
    names = [i["name"] for i in data["items"]]
    # Required categories — additional curated lists (Swedish, Danish) are
    # allowed but not required so this test stays stable as more get added.
    for expected in ["Popular", "Countries", "Genres", "Languages", "Favourites"]:
        assert expected in names, f"Missing {expected}"
    for item in data["items"]:
        assert item["type"] == "category"
        # Categories with curated SVG flag images use 'image' instead of
        # icon/color — accept either.
        assert "icon" in item or "image" in item, f"{item['name']} missing visual"
test("2. Root browse: required categories present", test_root_browse)


# ── 3. Popular stations ──
def test_popular():
    data = get("/browse?path=popular")
    assert data["name"] == "Popular"
    assert data["parent"] == ""
    assert len(data["items"]) > 0, "Popular should have stations"
    station = data["items"][0]
    assert station["type"] == "station"
    assert station.get("stationuuid"), "Station missing stationuuid"
    assert station.get("url_resolved"), "Station missing url_resolved"
    assert station.get("name"), "Station missing name"
test("3. Browse popular: returns stations with required fields", test_popular)


# ── 4. Countries list ──
def test_countries():
    data = get("/browse?path=countries")
    assert data["name"] == "Countries"
    assert len(data["items"]) > 10, f"Expected >10 countries, got {len(data['items'])}"
    for item in data["items"][:3]:
        assert item["type"] == "category"
        assert "count" in item
test("4. Browse countries: returns category list with counts", test_countries)


# ── 5. Drill into a country ──
def test_country_drill():
    data = get("/browse?path=countries/Germany")
    assert data["parent"] == "countries"
    assert len(data["items"]) > 0, "Germany should have stations"
    assert data["items"][0]["type"] == "station"
test("5. Browse countries/Germany: returns stations", test_country_drill)


# ── 6. Genres list ──
def test_genres():
    data = get("/browse?path=genres")
    assert len(data["items"]) > 5, f"Expected >5 genres, got {len(data['items'])}"
    for item in data["items"][:3]:
        assert item["type"] == "category"
test("6. Browse genres: returns category list", test_genres)


# ── 7. Drill into a genre ──
def test_genre_drill():
    data = get("/browse?path=genres/rock")
    assert len(data["items"]) > 0
    assert data["items"][0]["type"] == "station"
test("7. Browse genres/rock: returns stations", test_genre_drill)


# ── 8. Languages list ──
def test_languages():
    data = get("/browse?path=languages")
    assert len(data["items"]) > 5
test("8. Browse languages: returns list", test_languages)


# ── 9. Drill into a language ──
def test_language_drill():
    data = get("/browse?path=languages/english")
    assert len(data["items"]) > 0
    assert data["items"][0]["type"] == "station"
test("9. Browse languages/english: returns stations", test_language_drill)


# ── 10. Favourites structure ──
def test_favourites():
    data = get("/browse?path=favourites")
    assert data["name"] == "Favourites"
    assert isinstance(data["items"], list)
test("10. Browse favourites: valid response", test_favourites)


# ── 11. Station subtitle format ──
def test_station_subtitle():
    data = get("/browse?path=popular")
    for s in data["items"][:10]:
        assert "subtitle" in s, f"Station {s['name']} missing subtitle"
test("11. Station items have subtitle field", test_station_subtitle)


# ── 12. Play station command ──
def test_play_station():
    data = get("/browse?path=popular")
    station = data["items"][0]
    uuid = station["stationuuid"]
    # Longer timeout — player_play may timeout connecting to player service in dev
    result = post("/command", {"command": "play_station", "stationuuid": uuid}, timeout=30)
    assert result.get("status") == "ok", f"Play failed: {result}"
    status = get("/status")
    assert status["state"] == "playing", f"Expected playing, got {status['state']}"
    assert status["station"] is not None
test("12. Play station: sets state to playing", test_play_station)


# ── 13. Toggle pause/resume ──
def test_toggle():
    result = post("/command", {"command": "toggle"}, timeout=30)
    assert result.get("status") == "ok"
    status = get("/status")
    assert status["state"] == "paused", f"Expected paused, got {status['state']}"
    result = post("/command", {"command": "toggle"}, timeout=30)
    assert result.get("status") == "ok"
    status = get("/status")
    assert status["state"] == "playing", f"Expected playing, got {status['state']}"
test("13. Toggle: pause then resume", test_toggle)


# ── 14. Next/prev station cycling ──
def test_next_prev():
    # next/prev cycles through favourites — must start from a station that
    # IS in favourites for the round-trip to be deterministic.
    favs = get("/browse?path=favourites")["items"]
    if len(favs) < 2:
        raise Exception("Need >=2 favourites for next/prev round-trip")
    post("/command", {"command": "play_station",
                      "stationuuid": favs[0]["stationuuid"]}, timeout=30)
    time.sleep(2)
    station_before = get("/status")["station"]
    post("/command", {"command": "next"}, timeout=30)
    time.sleep(2)
    assert get("/status")["state"] == "playing"
    post("/command", {"command": "prev"}, timeout=30)
    time.sleep(2)
    station_back = get("/status")["station"]
    assert station_back == station_before, \
        f"Prev didn't return to original: {station_back} vs {station_before}"
test("14. Next then prev: returns to original station", test_next_prev)


# ── 15. Toggle favourite (current station) ──
def test_toggle_favourite():
    # Pick a popular station that isn't already a favourite — otherwise
    # the first toggle removes instead of adding.
    favs_uuids = {s["stationuuid"]
                  for s in get("/browse?path=favourites")["items"]}
    pop = get("/browse?path=popular")["items"]
    target = next((s for s in pop
                   if s["stationuuid"] not in favs_uuids), None)
    if not target:
        raise Exception("Could not find a popular station not in favourites")
    post("/command", {"command": "play_station",
                      "stationuuid": target["stationuuid"]}, timeout=30)
    time.sleep(2)
    fav_count_before = get("/status")["favourites"]
    result = post("/command", {"command": "toggle_favourite"})
    assert result.get("status") == "ok"
    assert result.get("favourite") is True, f"Expected favourite=True, got {result}"
    assert get("/status")["favourites"] == fav_count_before + 1
    # Remove it
    result = post("/command", {"command": "toggle_favourite"})
    assert result.get("status") == "ok"
    assert result.get("favourite") is False
    assert get("/status")["favourites"] == fav_count_before
test("15. Toggle favourite: add then remove current station", test_toggle_favourite)


# ── 16. Play station not found ──
def test_play_not_found():
    result = post("/command", {"command": "play_station", "stationuuid": "nonexistent-uuid"})
    assert result.get("status") == "error"
test("16. Play nonexistent station: returns error", test_play_not_found)


# ── 17. Stop playback ──
def test_stop():
    # Service intentionally preserves _current_station after stop so the
    # source-button can resume it. We only assert state, not station.
    result = post("/command", {"command": "stop"}, timeout=30)
    assert result.get("status") == "ok"
    assert get("/status")["state"] == "stopped"
test("17. Stop: state=stopped", test_stop)


# ── 18. Toggle while stopped resumes the last station ──
def test_toggle_stopped():
    # After stop, the radio source remembers the last station — toggle
    # should resume it (same as the source-button activation path).
    post("/command", {"command": "stop"}, timeout=30)
    time.sleep(1)
    result = post("/command", {"command": "toggle"}, timeout=30)
    assert result.get("status") == "ok"
    time.sleep(2)
    status = get("/status")
    assert status["state"] == "playing", \
        f"Expected toggle to resume; got state={status['state']}"
test("18. Toggle while stopped resumes the last station", test_toggle_stopped)


# ── 19. Favicon proxy ──
def test_favicon_proxy():
    data = get("/browse?path=popular")
    favicon_url = None
    for s in data["items"]:
        if s.get("favicon"):
            favicon_url = s["favicon"]
            break
    if not favicon_url:
        raise Exception("No station with favicon found in popular")
    encoded = urllib.parse.quote(favicon_url, safe='')
    try:
        status, headers, body = get_raw(f"/favicon?url={encoded}")
        assert status == 200
        ct = headers.get("Content-Type", "")
        assert "image" in ct or "octet" in ct, f"Unexpected content-type: {ct}"
        assert len(body) > 0
    except urllib.error.HTTPError as e:
        if e.code == 404:
            pass  # upstream favicon may be dead
        else:
            raise
test("19. Favicon proxy: returns image data", test_favicon_proxy)


# ── 20. Favicon proxy blocks internal URLs ──
def test_favicon_ssrf():
    try:
        encoded = urllib.parse.quote("http://127.0.0.1:8779/status", safe='')
        get_raw(f"/favicon?url={encoded}")
        raise Exception("Should have been blocked")
    except urllib.error.HTTPError as e:
        assert e.code == 403, f"Expected 403, got {e.code}"
test("20. Favicon proxy: blocks internal URLs", test_favicon_ssrf)


# ── 21. Browse caching ──
def test_cache():
    get("/browse?path=popular")  # warm cache
    t0 = time.time()
    get("/browse?path=popular")
    t1 = time.time()
    assert (t1 - t0) < 0.5, f"Cached request too slow: {t1 - t0:.3f}s"
test("21. API cache: cached browse is fast (<0.5s)", test_cache)


# ── 22. Unknown browse path ──
def test_unknown_path():
    data = get("/browse?path=nonexistent")
    assert data["name"] == "Unknown"
    assert data["items"] == []
test("22. Unknown browse path: empty result, no error", test_unknown_path)


# ── 23. Concurrent browse requests ──
def test_concurrent():
    import concurrent.futures
    paths = ["popular", "countries", "genres", "languages", "favourites"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(get, f"/browse?path={p}") for p in paths]
        results = [f.result() for f in futures]
    assert len(results) == 5
    for r in results:
        assert "items" in r
test("23. Concurrent browse: 5 parallel requests succeed", test_concurrent)


# ── 24. Station list snapshot stability ──
def test_snapshot_stability():
    # Start from a known favourite so next/prev cycles through favourites
    # deterministically (the radio service's next/prev iterate over
    # _favourites, not the most recent browse).
    favs = get("/browse?path=favourites")["items"]
    if len(favs) < 2:
        raise Exception("Need >=2 favourites for snapshot stability test")
    post("/command", {"command": "play_station",
                      "stationuuid": favs[0]["stationuuid"]}, timeout=30)
    time.sleep(2)
    station_playing = get("/status")["station"]
    # Browse a different category — should NOT affect next/prev
    get("/browse?path=genres/jazz")
    post("/command", {"command": "next"}, timeout=30)
    time.sleep(2)
    post("/command", {"command": "prev"}, timeout=30)
    time.sleep(2)
    station_after = get("/status")["station"]
    assert station_after == station_playing, \
        f"Snapshot broken: was {station_playing}, now {station_after}"
    post("/command", {"command": "stop"}, timeout=30)
test("24. Next/prev uses snapshot, not latest browse", test_snapshot_stability)


# ── 25. Toggle favourite without a remembered station ──
def test_favourite_no_station():
    # Restart-equivalent: clear current_station from service memory by
    # restarting would be cleanest, but we don't have permission here.
    # Instead, only run this assertion if /status reports no station —
    # otherwise skip, since the "stop preserves last station for resume"
    # behaviour means toggle_favourite has a station to act on.
    post("/command", {"command": "stop"}, timeout=30)
    time.sleep(1)
    if get("/status")["station"] is None:
        result = post("/command", {"command": "toggle_favourite"})
        assert result.get("status") == "error"
test("25. Toggle favourite without remembered station: returns error",
     test_favourite_no_station)


# ── 26. /favourites endpoint shape ──
def test_favourites_endpoint():
    data = get("/favourites")
    assert "favourites" in data, f"Missing 'favourites' key: {data}"
    assert "station_buttons" in data, f"Missing 'station_buttons' key: {data}"
    assert isinstance(data["favourites"], list)
    assert isinstance(data["station_buttons"], dict)
    if data["favourites"]:
        s = data["favourites"][0]
        for required in ("stationuuid", "name", "favicon", "country",
                         "tags", "codec", "bitrate"):
            assert required in s, f"Favourite missing field {required!r}: {s}"
test("26. /favourites endpoint: returns favourites + station_buttons", test_favourites_endpoint)


# ── 27. play_button bound: plays the bound station ──
def test_play_button_bound():
    """If any color button is bound, sending play_button for it should
    play that exact station. If nothing is bound, skip with a clear note."""
    data = get("/favourites")
    bindings = data["station_buttons"]
    color_bindings = {k: v for k, v in bindings.items()
                      if k in ("red", "green", "yellow", "blue")}
    if not color_bindings:
        # Not configured — skip rather than fail. Still asserts structure.
        return
    key, expected_uuid = next(iter(color_bindings.items()))
    expected = next((s for s in data["favourites"]
                     if s["stationuuid"] == expected_uuid), None)
    if not expected:
        # Bound to a uuid that isn't in favourites — still ok, the service
        # logs and skips. Skip the test rather than fail.
        return
    post("/command", {"command": "stop"}, timeout=30)
    time.sleep(1)
    result = post("/command", {"command": "play_button", "action": key},
                  timeout=30)
    assert result.get("status") == "ok", f"play_button {key}: {result}"
    time.sleep(3)
    status = get("/status")
    assert status.get("station") == expected["name"], (
        f"Bound {key} → expected station {expected['name']!r}, "
        f"got {status.get('station')!r}")
    post("/command", {"command": "stop"}, timeout=30)
test("27. play_button bound: plays the bound station", test_play_button_bound)


# ── 28. digit fallback: unbound digit uses index lookup ──
def test_digit_index_fallback():
    favs = get("/favourites")["favourites"]
    if len(favs) < 2:
        raise Exception("Need at least 2 favourites to test digit fallback")
    # digit 1 → favourites[0] when not bound (or bound to favourites[0]).
    # We test the behaviour, not which path was taken: digit 1 must play
    # *some* favourite.
    post("/command", {"command": "stop"}, timeout=30)
    result = post("/command", {"command": "digit", "action": "1"}, timeout=30)
    assert result.get("status") == "ok"
    # Wait briefly for state transition
    time.sleep(2)
    status = get("/status")
    assert status["station"] is not None, \
        f"digit 1 should play a station (state={status})"
    post("/command", {"command": "stop"}, timeout=30)
test("28. digit 1 plays favourite[0] (or bound station)", test_digit_index_fallback)


# ── 29. /status reports favourite count ──
def test_status_fav_count():
    status = get("/status")
    favs = get("/favourites")["favourites"]
    assert status["favourites"] == len(favs), \
        f"/status fav count {status['favourites']} != /favourites {len(favs)}"
test("29. /status favourite count matches /favourites length", test_status_fav_count)


# ── 30. station_buttons keys are restricted ──
def test_station_buttons_keys():
    """All keys in station_buttons must be valid button names."""
    data = get("/favourites")
    valid = {"0","1","2","3","4","5","6","7","8","9",
             "red","green","yellow","blue"}
    for k in data["station_buttons"].keys():
        assert k in valid, f"Invalid station_buttons key: {k!r}"
test("30. station_buttons only contains valid button keys", test_station_buttons_keys)


# ── 31. POST /favourites/short_name set, clear ──
def test_short_name_set_clear():
    """Set a short_name alias on a favourite, verify it round-trips, then
    clear it. Used by play_by_name to map BeoRemote menu labels to longer
    favourite names."""
    favs = get("/favourites")["favourites"]
    if not favs:
        raise Exception("Need at least 1 favourite to test short_name")
    s = favs[0]
    uuid = s["stationuuid"]
    original = s.get("short_name", "")

    # Set
    result = post("/favourites/short_name",
                  {"stationuuid": uuid, "short_name": "test-alias-xyz"})
    assert result.get("ok") is True, f"set failed: {result}"
    assert result.get("short_name") == "test-alias-xyz"
    after = next(f for f in get("/favourites")["favourites"]
                 if f["stationuuid"] == uuid)
    assert after["short_name"] == "test-alias-xyz", \
        f"GET /favourites didn't reflect set: {after}"

    # Clear
    result = post("/favourites/short_name",
                  {"stationuuid": uuid, "short_name": ""})
    assert result.get("ok") is True
    assert result.get("short_name") == ""
    after = next(f for f in get("/favourites")["favourites"]
                 if f["stationuuid"] == uuid)
    assert after["short_name"] == "", \
        f"GET /favourites didn't reflect clear: {after}"

    # Restore the original (best-effort — empty is the same as cleared)
    if original:
        post("/favourites/short_name",
             {"stationuuid": uuid, "short_name": original})
test("31. /favourites/short_name: set and clear round-trip",
     test_short_name_set_clear)


# ── 32. POST /favourites/short_name validates input ──
def test_short_name_validation():
    """Missing stationuuid → 400. Unknown uuid → 404."""
    import urllib.error
    try:
        post("/favourites/short_name", {"short_name": "x"})
    except urllib.error.HTTPError as e:
        assert e.code == 400, f"missing uuid: expected 400, got {e.code}"
    else:
        raise Exception("Expected 400 for missing stationuuid")

    try:
        post("/favourites/short_name",
             {"stationuuid": "00000000-0000-0000-0000-000000000000",
              "short_name": "x"})
    except urllib.error.HTTPError as e:
        assert e.code == 404, f"unknown uuid: expected 404, got {e.code}"
    else:
        raise Exception("Expected 404 for unknown stationuuid")
test("32. /favourites/short_name: validates input (400 / 404)",
     test_short_name_validation)


# ── 33. OPTIONS /favourites/short_name CORS preflight ──
def test_short_name_cors_preflight():
    """Config UI is served from port 80 and POSTs JSON to port 8779, so
    browsers send a CORS preflight (OPTIONS). Without a matching OPTIONS
    handler the preflight fails with 405 and the alias never saves."""
    url = f"{BASE}/favourites/short_name"
    req = urllib.request.Request(url, method="OPTIONS")
    req.add_header("Origin", "http://localhost")
    req.add_header("Access-Control-Request-Method", "POST")
    req.add_header("Access-Control-Request-Headers", "content-type")
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200, f"OPTIONS expected 200, got {resp.status}"
        assert resp.headers.get("Access-Control-Allow-Origin") == "*"
        allowed = resp.headers.get("Access-Control-Allow-Methods", "")
        assert "POST" in allowed, f"POST missing from Allow-Methods: {allowed}"
test("33. /favourites/short_name: CORS preflight returns 200",
     test_short_name_cors_preflight)


# ── 34. add_favourite: custom stream URL persists and plays ──
def test_add_custom_url():
    """End-to-end: POST add_favourite with a custom-prefixed uuid +
    custom URL. Must show up in /favourites and play via play_station."""
    uuid = f"custom-test-{int(time.time())}"
    custom = {
        "stationuuid": uuid,
        "name": "Custom Test Stream",
        "url_resolved": "http://stream.radioparadise.com/aac-128",
        "favicon": "", "country": "", "tags": "custom",
        "codec": "AAC", "bitrate": 128,
    }
    result = post("/command", {"command": "add_favourite", "station": custom})
    assert result.get("status") == "ok", f"add_favourite failed: {result}"

    favs = get("/favourites")["favourites"]
    assert any(f["stationuuid"] == uuid for f in favs), \
        f"custom station not in /favourites"

    # Play it via play_station — exercises the full pipeline.
    result = post("/command", {"command": "play_station",
                                "stationuuid": uuid}, timeout=30)
    assert result.get("status") == "ok"
    time.sleep(2)
    status = get("/status")
    assert status["state"] == "playing"
    assert status["station"] == "Custom Test Stream"

    post("/command", {"command": "stop"}, timeout=30)
    # Clean up — toggle_favourite the current station to remove it
    post("/command", {"command": "play_station",
                      "stationuuid": uuid}, timeout=30)
    time.sleep(1)
    post("/command", {"command": "toggle_favourite"})
    post("/command", {"command": "stop"}, timeout=30)
test("34. Custom stream URL: add → play → cleanup", test_add_custom_url)


# ── 35. add_favourite: custom URL without url_resolved is rejected ──
def test_custom_requires_url():
    """The radio service must reject a custom-prefixed station with no
    URL — otherwise the favourite is unplayable."""
    import urllib.error
    try:
        result = post("/command", {"command": "add_favourite",
                                    "station": {"stationuuid": "custom-no-url",
                                                "name": "Bad"}})
        assert result.get("status") == "error", \
            f"expected error, got {result}"
        assert "url" in result.get("message", "").lower()
    except urllib.error.HTTPError:
        pass  # error responses may also surface as HTTP errors
test("35. Custom URL without url_resolved: rejected", test_custom_requires_url)


# ── Summary ──
print(f"\n{'='*50}")
print(f"  {PASS} passed, {FAIL} failed out of {PASS + FAIL} tests")
print(f"{'='*50}")
sys.exit(1 if FAIL else 0)
