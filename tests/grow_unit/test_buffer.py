"""LocalBuffer: append messages, replay in timestamp order, prune by age."""
import time
from datetime import datetime, timedelta
from mlss_grow.buffer import LocalBuffer, _BYTE_CAP_CHECK_EVERY


def test_append_and_size(tmp_path):
    buf = LocalBuffer(db_path=str(tmp_path / "buf.sqlite"))
    buf.append("telemetry", '{"a":1}', ts=datetime(2026, 1, 1))
    buf.append("event", '{"b":2}', ts=datetime(2026, 1, 2))
    assert buf.size() == 2


def test_pop_all_returns_in_timestamp_order(tmp_path):
    buf = LocalBuffer(db_path=str(tmp_path / "buf.sqlite"))
    buf.append("telemetry", '{"i":2}', ts=datetime(2026, 1, 2))
    buf.append("telemetry", '{"i":1}', ts=datetime(2026, 1, 1))
    buf.append("telemetry", '{"i":3}', ts=datetime(2026, 1, 3))
    rows = buf.pop_all()
    assert [r.body for r in rows] == ['{"i":1}', '{"i":2}', '{"i":3}']
    assert buf.size() == 0


def test_pop_all_empty_buffer(tmp_path):
    buf = LocalBuffer(db_path=str(tmp_path / "buf.sqlite"))
    assert buf.pop_all() == []


def test_prune_drops_rows_older_than_retention(tmp_path):
    buf = LocalBuffer(db_path=str(tmp_path / "buf.sqlite"))
    old = datetime(2025, 1, 1)
    new = datetime(2026, 5, 1)
    buf.append("telemetry", '{"x":1}', ts=old)
    buf.append("telemetry", '{"x":2}', ts=new)
    buf.prune(retention_days=7, now=datetime(2026, 5, 8))
    assert buf.size() == 1
    rows = buf.pop_all()
    assert rows[0].body == '{"x":2}'


def test_buffer_survives_close_reopen(tmp_path):
    path = str(tmp_path / "buf.sqlite")
    b1 = LocalBuffer(db_path=path)
    b1.append("telemetry", '{"persist":true}', ts=datetime(2026, 1, 1))
    b1.close()

    b2 = LocalBuffer(db_path=path)
    assert b2.size() == 1


# ---------------------------------------------------------------------------
# peek_all + delete — building blocks for the safer replay path that doesn't
# drop messages on a mid-replay disconnect.
# ---------------------------------------------------------------------------

def test_buffer_peek_all_does_not_delete(tmp_path):
    """peek_all is a non-destructive read; calling it twice must return
    the same set of rows. Replay logic relies on this so a failed send
    leaves the buffer intact."""
    buf = LocalBuffer(db_path=str(tmp_path / "buf.sqlite"))
    buf.append("telemetry", '{"i":1}', ts=datetime(2026, 1, 1))
    buf.append("telemetry", '{"i":2}', ts=datetime(2026, 1, 2))
    buf.append("telemetry", '{"i":3}', ts=datetime(2026, 1, 3))

    first = buf.peek_all()
    second = buf.peek_all()

    assert len(first) == 3
    assert len(second) == 3
    assert [r.body for r in first] == [r.body for r in second]
    assert buf.size() == 3, "peek_all must not delete"


def test_buffer_peek_all_returns_in_timestamp_order(tmp_path):
    """Same ordering guarantee as pop_all — replay must preserve original
    timestamp order so server-side time-series stay coherent."""
    buf = LocalBuffer(db_path=str(tmp_path / "buf.sqlite"))
    buf.append("telemetry", '{"i":2}', ts=datetime(2026, 1, 2))
    buf.append("telemetry", '{"i":1}', ts=datetime(2026, 1, 1))
    buf.append("telemetry", '{"i":3}', ts=datetime(2026, 1, 3))

    rows = buf.peek_all()
    assert [r.body for r in rows] == ['{"i":1}', '{"i":2}', '{"i":3}']


def test_buffer_peek_all_empty_returns_empty_list(tmp_path):
    buf = LocalBuffer(db_path=str(tmp_path / "buf.sqlite"))
    assert buf.peek_all() == []


def test_buffer_delete_removes_specific_row(tmp_path):
    """Per-row delete is the second half of the safer replay protocol —
    rows are removed only after a successful send, so a mid-replay drop
    leaves the unsent tail in place."""
    buf = LocalBuffer(db_path=str(tmp_path / "buf.sqlite"))
    buf.append("telemetry", '{"i":1}', ts=datetime(2026, 1, 1))
    buf.append("telemetry", '{"i":2}', ts=datetime(2026, 1, 2))
    buf.append("telemetry", '{"i":3}', ts=datetime(2026, 1, 3))

    rows = buf.peek_all()
    middle = next(r for r in rows if r.body == '{"i":2}')
    buf.delete(middle.id)

    assert buf.size() == 2
    remaining = [r.body for r in buf.peek_all()]
    assert remaining == ['{"i":1}', '{"i":3}']


def test_buffer_delete_unknown_id_is_noop(tmp_path):
    """Deleting a row that isn't there must not raise — idempotency
    matters because the replay loop may catch a transient and retry."""
    buf = LocalBuffer(db_path=str(tmp_path / "buf.sqlite"))
    buf.append("telemetry", '{"x":1}', ts=datetime(2026, 1, 1))
    buf.delete(99999)  # never-existed id
    assert buf.size() == 1


def test_buffer_delete_then_size_zero(tmp_path):
    """Deleting all rows leaves size() at zero — paranoia check that
    individual delete()s collectively drain the buffer the same way
    pop_all() did atomically."""
    buf = LocalBuffer(db_path=str(tmp_path / "buf.sqlite"))
    buf.append("telemetry", '{"i":1}', ts=datetime(2026, 1, 1))
    buf.append("telemetry", '{"i":2}', ts=datetime(2026, 1, 2))
    rows = buf.peek_all()
    for r in rows:
        buf.delete(r.id)
    assert buf.size() == 0


# ---------------------------------------------------------------------------
# C2: hard size caps + on_eviction callback. Defence-in-depth against a
# permanently-down server so the local buffer can't fill the SD card.
# ---------------------------------------------------------------------------

def test_buffer_evicts_oldest_when_row_cap_exceeded(tmp_path):
    """Row cap: appending past max_rows drops oldest rows first (FIFO).

    Newer telemetry has more diagnostic value than week-old already-stale
    rows, so the eviction order is oldest-first — same as a circular log.
    """
    buf = LocalBuffer(db_path=str(tmp_path / "buf.sqlite"), max_rows=10)
    for i in range(15):
        buf.append(
            "telemetry",
            f'{{"i":{i}}}',
            ts=datetime(2026, 1, 1) + timedelta(seconds=i),
        )
    # After 15 inserts with cap=10, the 5 oldest must be gone.
    rows = buf.peek_all()
    assert len(rows) == 10
    surviving = [r.body for r in rows]
    # Bodies of i=0..4 are the oldest; they must NOT be in the surviving
    # set. Bodies i=5..14 must all still be there.
    for dropped in range(5):
        assert f'{{"i":{dropped}}}' not in surviving, (
            f"row i={dropped} should have been evicted (oldest first)"
        )
    for kept in range(5, 15):
        assert f'{{"i":{kept}}}' in surviving, (
            f"row i={kept} should still be in the buffer (newer than cap edge)"
        )


def test_buffer_evicts_when_byte_cap_exceeded(tmp_path):
    """Byte cap: large bodies trigger oldest-first eviction once the
    SUM(LENGTH(body)) check fires. The check runs every
    _BYTE_CAP_CHECK_EVERY inserts (currently 100), so we drive the
    insertion count to a multiple of that value to force the check.
    """
    # Tiny byte cap (1 KB) but a row cap big enough that the row-cap
    # branch never fires — we want to isolate the byte-cap path.
    buf = LocalBuffer(
        db_path=str(tmp_path / "buf.sqlite"),
        max_rows=10_000,
        max_bytes=1024,
    )
    big_body = "x" * 200  # 200 bytes per row
    # Insert exactly _BYTE_CAP_CHECK_EVERY rows so the byte-cap check
    # fires on the 100th insert. Total bytes = 100 * 200 = 20_000 which
    # is way over the 1 KB cap, so eviction must drop ~10% of rows.
    for i in range(_BYTE_CAP_CHECK_EVERY):
        buf.append(
            "telemetry",
            big_body,
            ts=datetime(2026, 1, 1) + timedelta(seconds=i),
        )
    # Some rows must have been evicted; we don't pin the exact count
    # (it's "max(1, row_count // 10)" which is implementation detail)
    # but the buffer must be smaller than the inserted count.
    assert buf.size() < _BYTE_CAP_CHECK_EVERY, (
        f"byte-cap eviction should have dropped rows; "
        f"size={buf.size()}, inserted={_BYTE_CAP_CHECK_EVERY}"
    )
    # And FIFO: the oldest rows are the ones gone. Since all bodies are
    # the same string we can't compare bodies directly — instead, check
    # that the smallest-id surviving row is greater than 1 (rows are
    # auto-incremented from 1).
    rows = buf.peek_all()
    surviving_ids = [r.id for r in rows]
    assert min(surviving_ids) > 1, (
        f"oldest row (id=1) should have been evicted first; "
        f"surviving ids start at {min(surviving_ids)}"
    )


def test_buffer_eviction_calls_on_eviction_callback(tmp_path):
    """on_eviction(reason, evicted_count) fires when the row cap evicts.

    Wired by ws_client to emit a `buffer_eviction` event so the operator
    sees "your unit dropped data because the server was unreachable too
    long" rather than silently losing telemetry.
    """
    calls: list[dict] = []

    def record(*, reason, evicted_count):
        calls.append({"reason": reason, "evicted_count": evicted_count})

    buf = LocalBuffer(
        db_path=str(tmp_path / "buf.sqlite"),
        max_rows=5,
        on_eviction=record,
    )
    for i in range(10):
        buf.append(
            "telemetry",
            f'{{"i":{i}}}',
            ts=datetime(2026, 1, 1) + timedelta(seconds=i),
        )
    # Each insert past the cap (rows 6, 7, 8, 9, 10) triggers eviction.
    # Pin the count + reason rather than the exact evicted_count per
    # call — implementation may batch in future.
    assert len(calls) >= 1, (
        f"on_eviction should have fired at least once; got {calls}"
    )
    for c in calls:
        assert c["reason"] == "row_cap"
        assert c["evicted_count"] >= 1


def test_buffer_eviction_callback_failure_does_not_break_append(tmp_path):
    """A buggy on_eviction callback must NOT propagate the exception out
    of .append(). The eviction itself already committed; losing the
    notification is the least bad outcome.
    """
    def boom(*, reason, evicted_count):
        raise RuntimeError("simulated callback bug")

    buf = LocalBuffer(
        db_path=str(tmp_path / "buf.sqlite"),
        max_rows=2,
        on_eviction=boom,
    )
    # First 2 appends fit under the cap, no eviction.
    buf.append("telemetry", '{"i":1}', ts=datetime(2026, 1, 1))
    buf.append("telemetry", '{"i":2}', ts=datetime(2026, 1, 2))
    # Third append triggers eviction → boom() raises → must be swallowed.
    buf.append("telemetry", '{"i":3}', ts=datetime(2026, 1, 3))
    # And the row was still inserted (and the oldest evicted) despite
    # the callback failure.
    assert buf.size() == 2
    rows = buf.peek_all()
    bodies = {r.body for r in rows}
    assert '{"i":1}' not in bodies, "oldest row should still have been evicted"
    assert '{"i":3}' in bodies, "newest row should be present"


def test_buffer_byte_cap_only_checked_every_100_inserts(tmp_path):
    """Performance pin: the SUM(LENGTH(body)) scan only runs every
    _BYTE_CAP_CHECK_EVERY (=100) inserts. Per-insert byte-cap checks
    would crater write throughput on a Pi Zero, so we accept brief
    excursions over the byte cap between checks.

    Test: byte cap so tiny that ANY two rows would exceed it. If the
    check fired on every insert we'd see eviction immediately. Because
    the check only fires at multiples of 100, rows 1..99 must all
    survive intact.
    """
    buf = LocalBuffer(
        db_path=str(tmp_path / "buf.sqlite"),
        max_rows=10_000,
        max_bytes=1,  # absurdly small — any row exceeds it
    )
    body = "x" * 50
    # 99 inserts: well under _BYTE_CAP_CHECK_EVERY=100, so byte check
    # never runs. All 99 rows survive (row cap is 10k, so it doesn't
    # fire either).
    for i in range(99):
        buf.append(
            "telemetry",
            body,
            ts=datetime(2026, 1, 1) + timedelta(seconds=i),
        )
    assert buf.size() == 99, (
        f"byte cap should NOT fire before insert #{_BYTE_CAP_CHECK_EVERY}; "
        f"all 99 rows must be present, got size={buf.size()}"
    )
    # 100th insert: row_count becomes 100, 100 % 100 == 0 → byte cap
    # check fires → drops 10% of rows.
    buf.append(
        "telemetry",
        body,
        ts=datetime(2026, 1, 1) + timedelta(seconds=99),
    )
    assert buf.size() < 100, (
        f"100th insert should trigger byte-cap eviction; "
        f"size={buf.size()} (expected < 100)"
    )


# ---------------------------------------------------------------------------
# Phase 3 Task 4: clear() — destructive emptying driven by the server's
# `clear_buffer` WS command. Operator confirms in the Diagnostics tab
# Danger Zone before this is invoked.
# ---------------------------------------------------------------------------


def test_buffer_clear_empties_table(tmp_path):
    """append a few rows, then clear() — peek_all() must return [] and
    size() must be 0. Pins the basic destructive contract."""
    buf = LocalBuffer(db_path=str(tmp_path / "buf.sqlite"))
    for i in range(5):
        buf.append(
            "telemetry",
            f'{{"i":{i}}}',
            ts=datetime(2026, 1, 1) + timedelta(seconds=i),
        )
    assert buf.size() == 5

    buf.clear()

    assert buf.size() == 0
    assert buf.peek_all() == []


def test_buffer_clear_idempotent_on_empty_buffer(tmp_path):
    """Calling clear() on an already-empty buffer must NOT raise — same
    idempotency contract as delete(unknown_id). Lets the server retry
    a clear-buffer command without creating an error."""
    buf = LocalBuffer(db_path=str(tmp_path / "buf.sqlite"))
    assert buf.size() == 0
    buf.clear()  # must not raise
    assert buf.size() == 0


def test_buffer_clear_persists_across_close_reopen(tmp_path):
    """clear() commits the DELETE — closing + reopening must show 0 rows
    (proves we're not just clearing an in-memory cache)."""
    path = str(tmp_path / "buf.sqlite")
    b1 = LocalBuffer(db_path=path)
    b1.append("telemetry", '{"persist":true}', ts=datetime(2026, 1, 1))
    b1.clear()
    b1.close()

    b2 = LocalBuffer(db_path=path)
    assert b2.size() == 0


def test_buffer_eviction_does_not_recurse_when_callback_appends(tmp_path):
    """The on_eviction callback typically appends an event row back into
    the buffer (so the server eventually sees the eviction). That append
    must NOT re-enter _evict_if_over_cap and trigger another eviction
    (which would call on_eviction again, ad infinitum).

    This pins the _eviction_in_progress re-entry guard.
    """
    fire_count = [0]

    buf_holder = {}

    def evict_handler(*, reason, evicted_count):
        fire_count[0] += 1
        # Simulate ws_client's handler: append an event row back into
        # the same buffer. This is the recursion source we're pinning.
        buf_holder["buf"].append(
            "event", '{"kind":"buffer_eviction"}',
            ts=datetime(2026, 1, 1),
        )

    buf = LocalBuffer(
        db_path=str(tmp_path / "buf.sqlite"),
        max_rows=2,
        on_eviction=evict_handler,
    )
    buf_holder["buf"] = buf

    buf.append("telemetry", '{"i":1}', ts=datetime(2026, 1, 1))
    buf.append("telemetry", '{"i":2}', ts=datetime(2026, 1, 2))
    # This third append fires eviction. The callback re-appends a row,
    # which would loop without the re-entry guard.
    buf.append("telemetry", '{"i":3}', ts=datetime(2026, 1, 3))

    # Re-entry guard => exactly one fire per outer eviction.
    assert fire_count[0] == 1, (
        f"on_eviction must fire exactly once per outer eviction; "
        f"got {fire_count[0]} — likely missing re-entry guard"
    )
