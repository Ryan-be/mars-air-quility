"""Tests for the typed incident_signature_features sub-table.

Pin the round-trip + ordering invariants for the
mlss_monitor.incident_signature_storage helpers, plus the schema
contract (cascade delete, primary-key replacement). See
docs/JSON_STORAGE_AUDIT.md for the column-promotion rationale.
"""
import json
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


def _seed_incident(conn, incident_id="INC-TEST-0001", signature_json="[]"):
    """Insert a minimal incident row so signature helpers have a parent."""
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    later = (datetime.utcnow() + timedelta(minutes=5)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    conn.execute(
        "INSERT INTO incidents (id, started_at, ended_at, max_severity, "
        "confidence, title, signature) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (incident_id, now, later, "info", 0.5, f"Test {incident_id}",
         signature_json),
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
    """An incident with no signature rows AND no legacy JSON returns []."""
    conn = sqlite3.connect(db)
    # Seed an incident with empty-list legacy JSON ('[]') and no sub-table rows.
    _seed_incident(conn, "INC-MISS-1", signature_json="[]")
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
    """An empty vector inserts no sub-table rows and raises no error.
    The legacy column is updated to '[]'."""
    conn = sqlite3.connect(db)
    _seed_incident(conn, "INC-EMP-1", signature_json="[1.0, 2.0]")
    save_signature(conn, "INC-EMP-1", [])
    conn.commit()
    n = conn.execute(
        "SELECT COUNT(*) FROM incident_signature_features WHERE incident_id=?",
        ("INC-EMP-1",),
    ).fetchone()[0]
    legacy = conn.execute(
        "SELECT signature FROM incidents WHERE id=?",
        ("INC-EMP-1",),
    ).fetchone()[0]
    conn.close()
    assert n == 0
    # Legacy column should have been overwritten to '[]'.
    assert json.loads(legacy) == []


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


# ── Backward-compat read path ────────────────────────────────────────────────

def test_load_signature_falls_back_to_legacy_json_column(db):
    """For an incident written before the migration (sub-table empty,
    legacy JSON populated), load_signature returns the JSON-decoded
    vector. This is critical so historical incidents keep matching after
    deploy and before the next regroup runs."""
    conn = sqlite3.connect(db)
    legacy = json.dumps([1.5, 2.5, 3.5, 4.5])
    _seed_incident(conn, "INC-LEG-1", signature_json=legacy)
    # No save_signature call — only the legacy column is populated.
    out = load_signature(conn, "INC-LEG-1")
    conn.close()
    assert out == [1.5, 2.5, 3.5, 4.5]


def test_load_signature_prefers_subtable_over_legacy_json(db):
    """When BOTH the sub-table AND the legacy JSON column have data,
    the sub-table wins (it is the new source of truth). This pins
    correctness during the deprecation window where save_signature
    still updates the legacy column for one release."""
    conn = sqlite3.connect(db)
    _seed_incident(conn, "INC-PREF-1", signature_json=json.dumps([99.0, 99.0]))
    save_signature(conn, "INC-PREF-1", [1.0, 2.0, 3.0])
    conn.commit()
    out = load_signature(conn, "INC-PREF-1")
    conn.close()
    # The sub-table value, NOT the legacy [99.0, 99.0].
    assert out == [1.0, 2.0, 3.0]


def test_load_signature_handles_corrupt_legacy_json(db):
    """If the legacy column holds non-JSON garbage and no sub-table
    rows exist, load_signature returns [] rather than crashing."""
    conn = sqlite3.connect(db)
    _seed_incident(conn, "INC-CORRUPT-1", signature_json="not-json-at-all")
    out = load_signature(conn, "INC-CORRUPT-1")
    conn.close()
    assert out == []
