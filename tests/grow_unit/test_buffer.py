"""LocalBuffer: append messages, replay in timestamp order, prune by age."""
import time
from datetime import datetime, timedelta
from mlss_grow.buffer import LocalBuffer


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
