"""Bidirectional SmartTube collections sync; pure Python, no Kodi imports.

Per-item compare-and-swap; a concurrent edit is preserved locally and flagged,
never silently replaced. Only follows.json, watch_later.json and playlists.json
are touched. Caches, DASH manifests, settings and history are not remapped.
"""
import contextlib
import hashlib
import json
import os
import tempfile
from urllib.request import Request, urlopen, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError

import fcntl

class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        return None


def safe_open(request, timeout):
    """A redirected request must never forward the private BS5c key."""
    return build_opener(_NoRedirect()).open(request, timeout=timeout)


COLLECTIONS = ("follows", "watch_later", "playlists")
LIMIT = {"follows": 200, "watch_later": 500, "playlists": 200}


def fingerprint(item):
    return hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def atomic_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".bs5c-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        return default


def _remote(host, key, collection, change=None):
    from ipaddress import ip_address
    if not ip_address(host).is_private or len(key) < 32:
        raise ValueError("Private IPv4 address and 32+ character key required")
    url = "http://%s:8788/library/youtube/collections/%s" % (host, collection)
    data = json.dumps(change, ensure_ascii=False).encode("utf-8") if change is not None else None
    req = Request(url, data=data, method="POST" if data is not None else "GET",
                  headers={"X-HomeMedia-YouTube-Key": key, "Content-Type": "application/json"})
    try:
        with safe_open(req, timeout=8) as resp:
            return json.loads(resp.read(8_000_001))
    except HTTPError as error:
        if error.code == 409:
            return {"conflict": True}
        raise


def sync_collection(profile, private_profile, collection, device, host, key, remote=_remote):
    """Compare local and remote revisions; never lock SmartTube during network I/O.

    Kodi's flock covers local read and replace, not slow HTTP calls.
    CAS versions protect BS5c writes; before committing locally we re-read
    to avoid clobbering a change made by SmartTube during the network roundtrip.
    """
    if collection not in COLLECTIONS:
        raise ValueError("Invalid collection")
    path = os.path.join(profile, collection + ".json")
    state_path = os.path.join(private_profile, collection + ".revisions.json")
    lock_path = os.path.join(profile, "store.lock")
    os.makedirs(profile, exist_ok=True)
    os.makedirs(private_profile, exist_ok=True)
    with open(lock_path, "a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            local_list = load_json(path, [])
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        if not isinstance(local_list, list):
            raise ValueError("Malformed local collection")
        local = {row["id"]: row for row in local_list if isinstance(row, dict)
                 and isinstance(row.get("id"), str)}
        if len(local) != len(local_list):
            raise ValueError("Invalid local collection entries")
        tracked = load_json(state_path, {})
        if not isinstance(tracked, dict):
            raise ValueError("Invalid revision state")
        # All HTTP requests are outside SmartTube's file lock.
        snapshot = remote(host, key, collection)
        server = {r["id"]: r for r in snapshot["items"]}
        new_local = dict(local)
        conflicts = []
        changes = 0
        for ident in sorted(set(local) | set(server) | set(tracked)):
            current = local.get(ident)
            old = tracked.get(ident)
            remote_row = server.get(ident)
            ver = remote_row["version"] if remote_row else 0
            remote_item = None if not remote_row or remote_row["deleted"] else remote_row["item"]
            local_changed = old is not None and (
                (fingerprint(current) if current is not None else None) != old.get("fingerprint"))
            remote_changed = old is not None and ver != old.get("version")
            if old is None and current is not None and remote_row is not None:
                if fingerprint(current) != (fingerprint(remote_item) if remote_item else None):
                    conflicts.append(ident)
                    continue
            if local_changed and remote_changed:
                conflicts.append(ident)
                continue
            if local_changed or (old is None and current is not None and remote_row is None):
                result = remote(host, key, collection, {
                    "id": ident, "device_id": device, "base_version": ver,
                    "deleted": current is None, "item": current or {},
                })
                if result.get("conflict"):
                    conflicts.append(ident)
                    continue
                ver = result["version"]
                changes += 1
            elif not local_changed and (remote_changed or old is None):
                if remote_item is None:
                    new_local.pop(ident, None)
                else:
                    new_local[ident] = remote_item
                changes += 1
            final = new_local.get(ident)
            tracked[ident] = {"version": ver,
                              "fingerprint": fingerprint(final) if final is not None else None}

        if conflicts:
            atomic_json(os.path.join(private_profile, collection + ".conflicts.json"),
                        {"ids": conflicts})
        else:
            conflict_path = os.path.join(private_profile, collection + ".conflicts.json")
            if os.path.exists(conflict_path):
                os.unlink(conflict_path)
        if len(new_local) > LIMIT[collection]:
            raise ValueError("Local list exceeds SmartTube collection cap")

        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            latest = load_json(path, [])
            if latest != local_list:
                # Never overwrite a freshly changed local list.
                # The next poll will reconcile it using these server revisions.
                atomic_json(state_path, tracked)
                return {"changes": changes, "conflicts": len(conflicts), "local_busy": True}
            if new_local != local:
                atomic_json(path, list(new_local.values()))
            atomic_json(state_path, tracked)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        return {"changes": changes, "conflicts": len(conflicts), "local_busy": False}


def sync_all(profile, private_profile, device, host, key):
    return {name: sync_collection(profile, private_profile, name, device, host, key)
            for name in COLLECTIONS}
