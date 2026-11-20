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
    channel = str(row.get("channel") or "")[:150]
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
