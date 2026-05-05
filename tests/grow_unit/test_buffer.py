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
