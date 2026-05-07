"""Tests for the typed incident_signature_features sub-table.

Pin the round-trip + ordering invariants for the
mlss_monitor.incident_signature_storage helpers, plus the schema
contract (cascade delete, primary-key replacement). See
docs/JSON_STORAGE_AUDIT.md for the column-promotion rationale.
"""
import sqlite3
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock

# Stub hardware libs and authlib before app imports (matches conftest pattern;
# this file imports nothing app-side, but database.init_db pulls in config which
# is loaded at module-import time and indirectly touches sys.modules).
for _mod in [
    "board", "busio", "adafruit_ahtx0", "adafruit_sgp30",
    "mics6814", "authlib", "authlib.integrations",
    "authlib.integrations.flask_client",
]:
    sys.modules.setdefault(_mod, MagicMock())

import pytest  # noqa: E402

import database.init_db as dbi  # noqa: E402
import database.db_logger as dbl  # noqa: E402
import database.user_db as udb  # noqa: E402
from mlss_monitor.incident_signature_storage import (  # noqa: E402
    load_signature,
    save_signature,
)


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    import mlss_monitor.hot_tier as ht
    dbi.DB_FILE = db_path
    dbl.DB_FILE = db_path
    udb.DB_FILE = db_path
    ht.DB_FILE = db_path
    dbi.create_db()
    yield db_path
    dbi.DB_FILE = "data/sensor_data.db"
    dbl.DB_FILE = "data/sensor_data.db"
    udb.DB_FILE = "data/sensor_data.db"
    ht.DB_FILE = "data/sensor_data.db"


def _seed_incident(conn, incident_id="INC-TEST-0001"):
    """Insert a minimal incident row so signature helpers have a parent."""
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    later = (datetime.utcnow() + timedelta(minutes=5)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    conn.execute(
        "INSERT INTO incidents (id, started_at, ended_at, max_severity, "
        "confidence, title) VALUES (?, ?, ?, ?, ?, ?)",
        (incident_id, now, later, "info", 0.5, f"Test {incident_id}"),
    )
    conn.commit()


# ── Round trip ───────────────────────────────────────────────────────────────

def test_signature_round_trip_via_helper(db):
    """save_signature(...) followed by load_signature(...) returns the
    same vector, element by element, in feature_idx order."""
    vector = [float(i) * 0.1 for i in range(32)]
    conn = sqlite3.connect(db)
    _seed_incident(conn, "INC-RT-1")
    save_signature(conn, "INC-RT-1", vector)
    conn.commit()
    out = load_signature(conn, "INC-RT-1")
    conn.close()
    assert out == vector


def test_save_signature_replaces_existing(db):
    """Saving a vector twice for the same incident replaces the old rows
    (no duplicate / orphan rows under the new primary key)."""
    conn = sqlite3.connect(db)
    _seed_incident(conn, "INC-REPL-1")
    save_signature(conn, "INC-REPL-1", [1.0, 2.0, 3.0])
    save_signature(conn, "INC-REPL-1", [9.0, 8.0])  # different length
    conn.commit()
    out = load_signature(conn, "INC-REPL-1")
    # Confirm only the second save persists.
    assert out == [9.0, 8.0]
    # And there are exactly 2 rows in the sub-table for this incident.
    n = conn.execute(
        "SELECT COUNT(*) FROM incident_signature_features WHERE incident_id=?",
        ("INC-REPL-1",),
    ).fetchone()[0]
    conn.close()
    assert n == 2


def test_load_signature_returns_empty_list_when_missing(db):
    """An incident with no signature rows returns []."""
    conn = sqlite3.connect(db)
    _seed_incident(conn, "INC-MISS-1")
    out = load_signature(conn, "INC-MISS-1")
    conn.close()
    assert out == []


def test_load_signature_for_unknown_incident_returns_empty_list(db):
    """An incident_id that doesn't exist at all returns []."""
    conn = sqlite3.connect(db)
    out = load_signature(conn, "INC-NOPE")
    conn.close()
    assert out == []


def test_load_signature_orders_by_feature_idx(db):
    """Rows inserted in arbitrary order are returned indexed by
    feature_idx ascending. Pins the ordering invariant explicitly."""
    conn = sqlite3.connect(db)
    _seed_incident(conn, "INC-ORD-1")
    # Insert rows in *reverse* order to defeat any insertion-order assumption.
    conn.execute(
        "INSERT INTO incident_signature_features "
        "(incident_id, feature_idx, value) VALUES (?, ?, ?)",
        ("INC-ORD-1", 2, 7.7),
    )
    conn.execute(
        "INSERT INTO incident_signature_features "
        "(incident_id, feature_idx, value) VALUES (?, ?, ?)",
        ("INC-ORD-1", 0, 1.1),
    )
    conn.execute(
        "INSERT INTO incident_signature_features "
        "(incident_id, feature_idx, value) VALUES (?, ?, ?)",
        ("INC-ORD-1", 1, 3.3),
    )
    conn.commit()
    out = load_signature(conn, "INC-ORD-1")
    conn.close()
    assert out == [1.1, 3.3, 7.7]


def test_save_signature_handles_empty_vector_gracefully(db):
    """An empty vector inserts no sub-table rows and raises no error."""
    conn = sqlite3.connect(db)
    _seed_incident(conn, "INC-EMP-1")
    save_signature(conn, "INC-EMP-1", [])
    conn.commit()
    n = conn.execute(
        "SELECT COUNT(*) FROM incident_signature_features WHERE incident_id=?",
        ("INC-EMP-1",),
    ).fetchone()[0]
    conn.close()
    assert n == 0


def test_cascade_delete_removes_signature_rows(db):
    """Deleting an incident removes its signature rows via ON DELETE CASCADE."""
    conn = sqlite3.connect(db)
    # CASCADE only fires when foreign_keys pragma is on for *this* connection.
    conn.execute("PRAGMA foreign_keys=ON")
    _seed_incident(conn, "INC-DEL-1")
    save_signature(conn, "INC-DEL-1", [4.4, 5.5, 6.6])
    conn.commit()
    # Sanity: rows are there before the parent delete.
    n_before = conn.execute(
        "SELECT COUNT(*) FROM incident_signature_features WHERE incident_id=?",
        ("INC-DEL-1",),
    ).fetchone()[0]
    assert n_before == 3
    conn.execute("DELETE FROM incidents WHERE id=?", ("INC-DEL-1",))
    conn.commit()
    n_after = conn.execute(
        "SELECT COUNT(*) FROM incident_signature_features WHERE incident_id=?",
        ("INC-DEL-1",),
    ).fetchone()[0]
    conn.close()
    assert n_after == 0
