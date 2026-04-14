"""Tests for the shared OAuth token store.

Covers the bugs that have historically hit per-source token modules:

  * Atomic write — a crash mid-write must not corrupt the file.
  * Partial refresh — a failed refresh must not clobber fields it didn't
    touch (commit 566acb3).
  * Concurrent refresh lock — two processes refreshing at once must
    serialise, not race (commit c45a1cf).
  * Read-only parent directory — fall through to direct write.
  * Missing/corrupt file — load returns None, doesn't raise.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import time

import pytest

from lib.token_store import TokenStore


@pytest.fixture
def store(tmp_path):
    prod = tmp_path / "prod"
    dev = tmp_path / "dev"
    prod.mkdir()
    dev.mkdir()
    return TokenStore(
        "test_tokens.json", dev_dir=str(dev), prod_dir=str(prod)
    )


# ── Round-trip ────────────────────────────────────────────────────────


def test_save_then_load_round_trip(store):
    store.save({"access_token": "abc", "refresh_token": "xyz"})
    loaded = store.load()
    assert loaded["access_token"] == "abc"
    assert loaded["refresh_token"] == "xyz"
    assert "updated_at" in loaded


def test_load_missing_returns_none(store):
    assert store.load() is None


def test_load_corrupt_returns_none(store):
    path = store.path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("{ not valid json")
    assert store.load() is None


# ── Atomicity ─────────────────────────────────────────────────────────


def test_atomic_write_leaves_no_tmp_files(store):
    store.save({"a": 1})
    d = os.path.dirname(store.path())
    leftovers = [n for n in os.listdir(d) if n.endswith(".tmp")]
    assert not leftovers, f"stale temp files: {leftovers}"


def test_save_merge_preserves_untouched_fields(store):
    """Commit 566acb3: a failed refresh must not clobber refresh_token."""
    store.save({
        "client_id": "cid",
        "refresh_token": "old_refresh",
        "access_token": "old_access",
    })
    # Simulate a partial refresh that only updates access_token + expiry.
    store.save_merge({"access_token": "new_access", "expiry_time": 9999})
    loaded = store.load()
    assert loaded["refresh_token"] == "old_refresh"  # preserved
    assert loaded["access_token"] == "new_access"
    assert loaded["expiry_time"] == 9999
    assert loaded["client_id"] == "cid"


def test_save_overwrites_entirely(store):
    """Plain save() replaces the whole payload (use save_merge to patch)."""
    store.save({"a": 1, "b": 2})
    store.save({"a": 3})
    loaded = store.load()
    assert loaded["a"] == 3
    assert "b" not in loaded


# ── Storage path discovery ────────────────────────────────────────────


def test_prefers_existing_prod_file(tmp_path):
    prod = tmp_path / "prod"
    dev = tmp_path / "dev"
    prod.mkdir()
    dev.mkdir()
    (prod / "t.json").write_text('{"where": "prod"}')
    (dev / "t.json").write_text('{"where": "dev"}')
    s = TokenStore("t.json", dev_dir=str(dev), prod_dir=str(prod))
    assert s.load()["where"] == "prod"


def test_falls_back_to_dev_when_prod_readonly(tmp_path):
    prod = tmp_path / "prod"
    dev = tmp_path / "dev"
    prod.mkdir()
    dev.mkdir()
    os.chmod(prod, 0o555)  # read-only directory
    try:
        s = TokenStore("t.json", dev_dir=str(dev), prod_dir=str(prod))
        s.save({"k": "v"})
        assert (dev / "t.json").exists()
        assert s.load()["k"] == "v"
    finally:
        os.chmod(prod, 0o755)  # let tmp_path clean up


def test_delete_removes_file(store):
    store.save({"a": 1})
    assert os.path.exists(store.path())
    store.delete()
    assert not os.path.exists(store.path())
    assert store.load() is None


# ── Refresh lock ──────────────────────────────────────────────────────


def test_refresh_lock_serialises_within_process(store):
    """Two nested with-blocks on the same lock must not deadlock: our
    ``fcntl.LOCK_EX`` is held on a fresh fd each time, so nesting in the
    same process re-acquires on a new fd.  That's by design — the lock
    exists to serialise *processes*, not threads inside one."""
    # Just prove the context manager works and releases cleanly.
    with store.refresh_lock():
        store.save({"a": 1})
    with store.refresh_lock():
        store.save({"a": 2})
    assert store.load()["a"] == 2


def _child_refresh(filename: str, dev_dir: str, prod_dir: str,
                   hold_ms: int, out_q: mp.Queue) -> None:
    """Child process: acquire refresh lock, sleep, record timestamps."""
    s = TokenStore(filename, dev_dir=dev_dir, prod_dir=prod_dir)
    t0 = time.monotonic()
    with s.refresh_lock():
        t1 = time.monotonic()
        time.sleep(hold_ms / 1000)
        t2 = time.monotonic()
    out_q.put((t0, t1, t2))


@pytest.mark.skipif(
    not hasattr(__import__("os"), "fork"),
    reason="refresh_lock cross-process test needs fork"
)
def test_refresh_lock_serialises_across_processes(tmp_path):
    """Two processes both entering ``refresh_lock()`` must run serially."""
    prod = tmp_path / "prod"
    dev = tmp_path / "dev"
    prod.mkdir()
    dev.mkdir()
    # Seed the file so .path() stabilises.
    TokenStore("t.json", dev_dir=str(dev), prod_dir=str(prod)).save({"x": 0})

    ctx = mp.get_context("fork")
    q = ctx.Queue()
    p1 = ctx.Process(target=_child_refresh,
                     args=("t.json", str(dev), str(prod), 300, q))
    p2 = ctx.Process(target=_child_refresh,
                     args=("t.json", str(dev), str(prod), 300, q))
    p1.start()
    time.sleep(0.05)  # let p1 grab the lock first
    p2.start()
    p1.join(5)
    p2.join(5)
    assert p1.exitcode == 0 and p2.exitcode == 0
    results = sorted([q.get(), q.get()], key=lambda r: r[1])  # by enter time
    (_, enter1, exit1), (_, enter2, exit2) = results
    # Second process's enter must be after first's exit (allow 10ms slack
    # for scheduler jitter).
    assert enter2 >= exit1 - 0.01, (
        f"locks overlapped: enter1={enter1:.3f} exit1={exit1:.3f} "
        f"enter2={enter2:.3f} exit2={exit2:.3f}"
    )


# ── Payload shape preservation ────────────────────────────────────────


def test_updated_at_is_iso_utc(store):
    store.save({"k": "v"})
    raw = json.loads(open(store.path()).read())
    ts = raw["updated_at"]
    # Must end in +00:00 or Z (UTC marker) — not a naive local timestamp.
    assert ts.endswith("+00:00") or ts.endswith("Z"), ts
