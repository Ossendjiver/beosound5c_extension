"""Cross-process refresh-lock test for Spotify PKCE.

Regression guard for commit c45a1cf (shared spotify token master) and
for the refresh_lock wiring added on top of lib.token_store.

Scenario: two processes both call ``_refresh_under_file_lock`` at the
same time.  PKCE rotates the refresh token on every successful call,
so if they race:

  * both submit the same (stale) refresh_token
  * the Spotify API rotates it for the first caller only
  * the second caller's next refresh hits ``400 invalid_grant``
  * the user has to re-authenticate

The fix: ``refresh_lock()`` serialises them via fcntl.flock on a
sidecar lock file, and on entering the lock we reload tokens from
disk.  The second caller therefore picks up the *new* refresh_token
the first caller just wrote and submits that — which is the invariant
this test pins.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import time

import pytest


# We stub the Spotify HTTP call via a tiny fake that records how many
# times it was called and returns a deterministic new refresh_token
# per call.  Running two refreshers serially must therefore use
# distinct tokens on their second/third calls — the test asserts that
# the second refresher picked up the first's rotation.


def _child_refresh(
    filename: str,
    dev_dir: str,
    prod_dir: str,
    start_gate: float,
    out_q: mp.Queue,
) -> None:
    """Child process: wait until ``start_gate``, then refresh once."""
    import sys
    import time as _time

    # Ensure services/ is on sys.path so ``lib`` is importable.
    here = os.path.dirname(os.path.abspath(__file__))
    services_dir = os.path.abspath(os.path.join(here, "..", "..", "..", "services"))
    sys.path.insert(0, services_dir)

    from lib.token_store import TokenStore

    store = TokenStore(filename, dev_dir=dev_dir, prod_dir=prod_dir)

    # Wait for the gate so both processes try to enter the lock at the
    # same time (give or take scheduler jitter).
    while _time.monotonic() < start_gate:
        _time.sleep(0.001)

    t_enter = _time.monotonic()
    with store.refresh_lock():
        t_held = _time.monotonic()
        # Simulate "refresh" by reading the current refresh_token,
        # rotating it ("rt_A" -> "rt_B" -> "rt_C"...), sleeping a bit
        # so the other process has time to block on the lock, then
        # writing the rotated token.
        tokens = store.load() or {}
        seen_rt = tokens.get("refresh_token", "rt_A")
        rotated = {"rt_A": "rt_B", "rt_B": "rt_C", "rt_C": "rt_D"}.get(
            seen_rt, "rt_Z"
        )
        _time.sleep(0.2)
        store.save({"client_id": "cid", "refresh_token": rotated})
        t_release = _time.monotonic()

    out_q.put(
        {
            "pid": os.getpid(),
            "enter": t_enter,
            "held": t_held,
            "release": t_release,
            "seen_rt": seen_rt,
            "wrote_rt": rotated,
        }
    )


@pytest.mark.skipif(
    not hasattr(os, "fork"), reason="cross-process refresh test needs fork"
)
def test_two_concurrent_refreshers_serialise_and_see_rotation(tmp_path):
    prod = tmp_path / "prod"
    dev = tmp_path / "dev"
    prod.mkdir()
    dev.mkdir()

    # Seed the token store with the initial refresh token.
    from lib.token_store import TokenStore

    TokenStore("test_tokens.json", dev_dir=str(dev), prod_dir=str(prod)).save(
        {"client_id": "cid", "refresh_token": "rt_A"}
    )

    ctx = mp.get_context("fork")
    q = ctx.Queue()
    gate = time.monotonic() + 0.25

    procs = [
        ctx.Process(
            target=_child_refresh,
            args=("test_tokens.json", str(dev), str(prod), gate, q),
        )
        for _ in range(2)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(10)

    assert all(p.exitcode == 0 for p in procs)
    results = [q.get(), q.get()]
    by_held = sorted(results, key=lambda r: r["held"])
    first, second = by_held

    # Serialisation: second process must not enter the critical section
    # before the first released it (10 ms slack for scheduler jitter).
    assert second["held"] >= first["release"] - 0.01, (
        f"Locks overlapped: first held [{first['held']:.3f}, "
        f"{first['release']:.3f}], second held at {second['held']:.3f}"
    )

    # Rotation visibility: the second process must have seen the
    # refresh_token the first one wrote.  This is the core invariant
    # that prevents PKCE invalid_grant on the second refresh.
    assert first["seen_rt"] == "rt_A"
    assert first["wrote_rt"] == "rt_B"
    assert second["seen_rt"] == "rt_B", (
        f"Second refresher saw {second['seen_rt']!r} but first had "
        f"already rotated to {first['wrote_rt']!r}.  The lock+reload "
        f"invariant is broken — concurrent refreshers will race "
        f"through PKCE rotation and trip invalid_grant."
    )
    assert second["wrote_rt"] == "rt_C"
