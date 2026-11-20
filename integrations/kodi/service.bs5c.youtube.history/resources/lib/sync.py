"""SmartTube's local history.json -> BS5c (pure Python, no Kodi imports).

This adapter does not change SmartTube, seek playback or connect to Google.
A persistent acknowledgement file avoids resending entries after restart.
"""
import json
import os
import re
import tempfile
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
DEVICE_ID = re.compile(r"^[A-Za-z0-9_-]{1,48}$")
MAX_HISTORY_BYTES = 2_000_000
MAX_ACKS = 550


def read_history(path):
    try:
        if os.stat(path).st_size > MAX_HISTORY_BYTES:
            raise ValueError("SmartTube history file too large")
        with open(path, encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, list):
            raise ValueError("Unexpected SmartTube history shape")
        return data[:500]
    except FileNotFoundError:
        return []


def event_for(row, device, observed_ms=None):
    """Convert SmartTube's seconds into the BS5c millisecond event format."""
    if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not VIDEO_ID.fullmatch(row["id"]):
        return None
    if not DEVICE_ID.fullmatch(device):
        raise ValueError("Invalid configured device ID")
    try:
        watched_at = int(row["watched_at"])
        position = max(0, int(float(row.get("position", 0)) * 1000))
        duration = max(0, int(float(row.get("total") or row.get("duration") or 0) * 1000))
    except (ValueError, TypeError, KeyError, OverflowError):
        return None
    if watched_at < 0 or duration > 86400000 or position > 86400000:
        return None
    now_ms = int(time.time()*1000) if observed_ms is None else observed_ms
    # Prefer the source watch timestamp for older records; use the observation
    # time for live progress updates, which can repeat within the same second.
    stamp = now_ms if 0 <= now_ms - watched_at*1000 < 120000 else watched_at*1000
    title = str(row.get("title") or "")[:300]
    channel = str(row.get("channel") or row.get("author") or "")[:150]
    return {
        "video_id": row["id"], "device_id": device,
        "title": title, "channel": channel,
        "position_ms": min(position, duration) if duration else position,
        "duration_ms": duration, "watched_at_ms": stamp,
        "ended": bool(duration and position >= duration * 0.9 and not row.get("is_live")),
    }


def signature(row):
    """Only actual history changes trigger sending (unrelated JSON rewrites do not)."""
    return json.dumps([row.get(k) for k in ("watched_at", "position", "total", "duration",
                                            "title", "channel", "is_live")],
                      ensure_ascii=False, separators=(",", ":"))


def load_acks(path):
    try:
        with open(path, encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_acks(path, acks):
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".youtube-acks-", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(acks, file, ensure_ascii=False)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def send_event(base, key, event, opener=urlopen):
    if not key or len(key) < 32:
        raise ValueError("Configure the dedicated BS5c YouTube history key")
    # Only a configured private LAN IP / hostname; never accept arbitrary URLs
    # from the history file. Transport remains LAN HTTP, not a public endpoint.
    from ipaddress import ip_address
    host = str(base).strip()
    try:
        address = ip_address(host)
        if not address.is_private:
            raise ValueError("BS5c host must be a private LAN address")
    except ValueError as error:
        if "does not appear" in str(error):
            raise ValueError("Specify the BS5c private IPv4 address") from error
        raise
    url = "http://%s:8788/library/youtube/history" % host
    payload = json.dumps(event, separators=(",", ":")).encode("utf-8")
    request = Request(url, data=payload, headers={
        "Content-Type": "application/json",
        "X-HomeMedia-YouTube-Key": key,
    }, method="POST")
    with opener(request, timeout=5) as response:
        result = json.loads(response.read(2048))
        if not result.get("accepted"):
            raise ValueError("BS5c did not acknowledge history event")


def sync_once(history_path, ack_path, device, host, key, sender=send_event, observed_ms=None):
    """Send at most 500 changed entries, oldest first; save each acknowledgement."""
    if not DEVICE_ID.fullmatch(device):
        raise ValueError("Invalid device name")
    history = read_history(history_path)
    acks = load_acks(ack_path)
    changed = 0
    candidates = []
    for row in history:
        if not isinstance(row, dict) or not VIDEO_ID.fullmatch(str(row.get("id", ""))):
            continue
        sig = signature(row)
        if acks.get(row["id"]) != sig:
            event = event_for(row, device, observed_ms=observed_ms)
            if event is not None:
                candidates.append((event["watched_at_ms"], row["id"], sig, event))
    for _, video_id, sig, event in sorted(candidates):
        sender(host, key, event)
        acks[video_id] = sig
        changed += 1
        # Persist the acknowledgement even if the next event fails.
        if len(acks) > MAX_ACKS:
            relevant = {row["id"] for row in history if isinstance(row, dict) and "id" in row}
            acks = {ident: value for ident, value in acks.items() if ident in relevant}
        save_acks(ack_path, acks)
    return changed

def fetch_history(host, key):
    """Read up to the latest 500 records; streaming URLs never leave Kodi."""
    from ipaddress import ip_address
    if not ip_address(host).is_private or len(key) < 32:
        raise ValueError("Private BS5c IP and dedicated key required")
    req = Request("http://%s:8788/library/youtube/history?limit=500" % host,
                  headers={"X-HomeMedia-YouTube-Key": key}, method="GET")
    with urlopen(req, timeout=5) as response:
        raw = response.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError("Remote history response too large")
    result = json.loads(raw)
    if not isinstance(result.get("items"), list):
        raise ValueError("Invalid remote history")
    return result["items"]


def merge_remote_history(history_path, ack_path, remote, active_id=""):
    """Merge remote progress under SmartTube's lock without touching active video.

    Remote records carry millisecond timestamps; SmartTube's JSON uses seconds.
    Imported entries are acknowledged so that the next upload cycle does not
    echo them back to BS5c. No removal of unknown local history is performed.
    """
    import fcntl
    if not isinstance(remote, list):
        raise ValueError("Expected remote history list")
    directory = os.path.dirname(history_path)
    os.makedirs(directory, exist_ok=True)
    lock_path = os.path.join(directory, "store.lock")
    with open(lock_path, "a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            local = read_history(history_path)
            current = {row["id"]: row for row in local
                       if isinstance(row, dict) and VIDEO_ID.fullmatch(str(row.get("id", "")))}
            imported = 0
            changed_ids = []
            for source in remote[:500]:
                if not isinstance(source, dict):
                    continue
                ident = source.get("video_id")
                if not isinstance(ident, str) or not VIDEO_ID.fullmatch(ident) or ident == active_id:
                    continue
                timestamp = source.get("watched_at_ms")
                if type(timestamp) is not int or timestamp < 0:
                    continue
                old = current.get(ident)
                # >1.5s avoids echoing the same update rounded to whole seconds.
                if old is not None and timestamp <= int(old.get("watched_at", 0))*1000 + 1500:
                    continue
                position = source.get("position_ms", 0)
                total = source.get("duration_ms", 0)
                if type(position) is not int or type(total) is not int or not (0 <= position <= 86400000 and 0 <= total <= 86400000):
                    continue
                updated = dict(old or {}, id=ident, type="video",
                               title=source.get("title") or (old or {}).get("title") or "YouTube video",
                               author=source.get("channel") or (old or {}).get("author") or "",
                               position=position // 1000, total=total // 1000,
                               duration=total // 1000,
                               watched_at=timestamp // 1000, is_live=False)
                current[ident] = updated
                changed_ids.append(ident)
                imported += 1
            if not imported:
                return 0
            entries = sorted(current.values(), key=lambda entry: int(entry.get("watched_at", 0)), reverse=True)[:500]
            # Atomic replace follows SmartTube's existing library.py semantics.
            atomic = os.path.join(directory, ".bs5c-import-history.json")
            try:
                with open(atomic, "w", encoding="utf-8") as out:
                    json.dump(entries, out, ensure_ascii=False)
                os.replace(atomic, history_path)
            finally:
                if os.path.exists(atomic):
                    os.unlink(atomic)
            acks = load_acks(ack_path)
            for entry in entries:
                if entry["id"] in changed_ids:
                    acks[entry["id"]] = signature(entry)
            save_acks(ack_path, acks)
            return imported
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
