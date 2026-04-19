"""Concurrent push + prune on a single HotTier SQLite connection.

Before H2 was fixed the shared `check_same_thread=False` connection was used
by both `_sensor_read_loop` (push) and `_background_log` (prune_old) with no
lock — under contention sqlite raised `database is locked` or corrupted the
cursor. The test fires 500 pushes and 500 prune_old calls in lock-step on
two threads and asserts no exception propagates out.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

import database.init_db as dbi
import mlss_monitor.hot_tier as ht_mod
from mlss_monitor.data_sources.base import NormalisedReading
from mlss_monitor.hot_tier import HotTier


def _reading(seconds_ago: int = 0, tvoc: float = 1.0) -> NormalisedReading:
    return NormalisedReading(
        timestamp=datetime.now(timezone.utc) - timedelta(seconds=seconds_ago),
        source="test",
        tvoc_ppb=tvoc,
        temperature_c=22.0,
        humidity_pct=50.0,
    )


def test_concurrent_push_and_prune(tmp_path):
    """500 pushes + 500 prunes on the same HotTier must not raise.

    Uses a Barrier so both threads start the hot loop at the same instant,
    maximising the chance of interleaved execute()/commit() calls on the
    shared connection. Any exception observed on either thread fails the
    test. Final row count must be >=0 and equal to the number of pushes
    that landed within the 60-minute keep-window (all of them, since each
    reading uses "now").
    """
    db_path = str(tmp_path / "test.db")
    dbi.DB_FILE = db_path
    ht_mod.DB_FILE = db_path
    try:
        dbi.create_db()
        tier = HotTier(maxlen=3600, db_file=db_path)

        n_ops = 500
        barrier = threading.Barrier(2)
        errors: list[BaseException] = []
        errors_lock = threading.Lock()

        def _record(exc: BaseException) -> None:
            with errors_lock:
                errors.append(exc)

        def push_worker() -> None:
            barrier.wait()
            for _ in range(n_ops):
                try:
                    tier.push(_reading())
                except BaseException as exc:  # pylint: disable=broad-except
                    _record(exc)
                    return

        def prune_worker() -> None:
            barrier.wait()
            for _ in range(n_ops):
                try:
                    tier.prune_old()
                except BaseException as exc:  # pylint: disable=broad-except
                    _record(exc)
                    return

        t_push = threading.Thread(target=push_worker, name="push")
        t_prune = threading.Thread(target=prune_worker, name="prune")
        t_push.start()
        t_prune.start()
        t_push.join(timeout=30)
        t_prune.join(timeout=30)

        assert not t_push.is_alive(), "push worker hung"
        assert not t_prune.is_alive(), "prune worker hung"
        assert not errors, f"Unexpected exceptions under contention: {errors!r}"

        # Prune deletes anything older than 60 minutes; every push uses
        # "now", so all n_ops rows should remain. In-memory deque has the
        # same count.
        assert tier.size() == n_ops
    finally:
        dbi.DB_FILE = "data/sensor_data.db"
        ht_mod.DB_FILE = "data/sensor_data.db"


@pytest.mark.parametrize("n_ops", [200])
def test_concurrent_push_from_many_threads(tmp_path, n_ops):
    """4 pushers + 1 pruner on the same HotTier must not raise."""
    db_path = str(tmp_path / "test.db")
    dbi.DB_FILE = db_path
    ht_mod.DB_FILE = db_path
    try:
        dbi.create_db()
        tier = HotTier(maxlen=3600, db_file=db_path)

        n_pushers = 4
        barrier = threading.Barrier(n_pushers + 1)
        errors: list[BaseException] = []
        errors_lock = threading.Lock()

        def _record(exc: BaseException) -> None:
            with errors_lock:
                errors.append(exc)

        def push_worker() -> None:
            barrier.wait()
            for _ in range(n_ops):
                try:
                    tier.push(_reading())
                except BaseException as exc:  # pylint: disable=broad-except
                    _record(exc)
                    return

        def prune_worker() -> None:
            barrier.wait()
            for _ in range(n_ops):
                try:
                    tier.prune_old()
                except BaseException as exc:  # pylint: disable=broad-except
                    _record(exc)
                    return

        threads = [
            threading.Thread(target=push_worker, name=f"push-{i}")
            for i in range(n_pushers)
        ]
        threads.append(threading.Thread(target=prune_worker, name="prune"))
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
            assert not t.is_alive(), f"{t.name} hung"

        assert not errors, f"Unexpected exceptions under contention: {errors!r}"
        assert tier.size() == n_pushers * n_ops
    finally:
        dbi.DB_FILE = "data/sensor_data.db"
        ht_mod.DB_FILE = "data/sensor_data.db"
