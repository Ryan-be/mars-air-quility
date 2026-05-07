"""Tests pinning the post-drop schema for the legacy JSON-in-TEXT
columns ``incidents.signature`` and ``inferences.evidence``.

Background: Commits B (9c745fe) + C (85ce40e) promoted both columns
to typed storage with a one-release deprecation cycle (dual-write +
fallback reads). Commit ``d0a1d07`` back-filled all historic rows so
the typed representation is the single source of truth. The current
commit drops the legacy columns entirely.

These tests catch regressions if anyone:
  * re-adds the dropped columns to the schema,
  * re-introduces a write path that touches the dropped columns,
  * re-adds the deleted ``database/migrations.py`` no-op helpers.
"""
import sqlite3
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock

# Stub hardware libs and authlib before app imports — same pattern as
# the conftest, since this module also pulls database.init_db at
# module-import time which transitively touches sys.modules.
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
from mlss_monitor.incident_signature_storage import save_signature  # noqa: E402
from mlss_monitor.inference_evidence_storage import persist_evidence  # noqa: E402


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


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Return the set of column names defined on ``table``."""
    return {
        row[1]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


# ── Schema contract ──────────────────────────────────────────────────────────

def test_incidents_table_has_no_signature_column(db):
    """The legacy JSON-in-TEXT ``incidents.signature`` column was dropped
    after the typed ``incident_signature_features`` sub-table back-fill
    completed. Re-adding it would silently restore the dual-write debt
    that this commit removed."""
    conn = sqlite3.connect(db)
    cols = _table_columns(conn, "incidents")
    conn.close()
    assert "signature" not in cols, (
        "incidents.signature must remain dropped — the typed sub-table "
        "incident_signature_features is the single source of truth."
    )


def test_inferences_table_has_no_evidence_column(db):
    """The legacy JSON-in-TEXT ``inferences.evidence`` column was dropped
    after the typed ``evidence_*`` columns + ``evidence_extras`` blob
    back-fill completed."""
    conn = sqlite3.connect(db)
    cols = _table_columns(conn, "inferences")
    conn.close()
    assert "evidence" not in cols, (
        "inferences.evidence must remain dropped — the typed evidence_* "
        "columns + evidence_extras are the single source of truth."
    )
    # Sanity check the typed replacements survive.
    assert "evidence_attribution_source" in cols
    assert "evidence_extras" in cols


# ── Write paths no longer touch the dropped columns ──────────────────────────

def test_signature_writes_no_longer_touch_dropped_column(db):
    """save_signature() must NOT issue an UPDATE against a dropped
    column — that would raise OperationalError("no such column").
    A successful round-trip is the proof."""
    conn = sqlite3.connect(db)
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    later = (datetime.utcnow() + timedelta(minutes=5)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    conn.execute(
        "INSERT INTO incidents (id, started_at, ended_at, max_severity, "
        "confidence, title) VALUES (?, ?, ?, ?, ?, ?)",
        ("INC-DROP-1", now, later, "info", 0.5, "Test"),
    )
    conn.commit()
    # No exception => the legacy UPDATE incidents SET signature=? path
    # is gone (it would error against a missing column).
    save_signature(conn, "INC-DROP-1", [1.0, 2.0, 3.0])
    conn.commit()
    n = conn.execute(
        "SELECT COUNT(*) FROM incident_signature_features WHERE incident_id=?",
        ("INC-DROP-1",),
    ).fetchone()[0]
    conn.close()
    assert n == 3


def test_evidence_writes_no_longer_touch_dropped_column(db):
    """persist_evidence() must NOT include the dropped ``evidence``
    column in its UPDATE clause."""
    conn = sqlite3.connect(db)
    cur = conn.execute(
        "INSERT INTO inferences (created_at, event_type, severity, title, "
        "description, action, confidence) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (datetime.utcnow().isoformat(), "tvoc_spike", "info",
         "Test", "desc", "act", 0.5),
    )
    inf_id = cur.lastrowid
    conn.commit()
    # No exception => the legacy ``evidence=?`` clause is gone.
    persist_evidence(conn, inf_id, {
        "attribution_source": "smoking",
        "feature_vector": {"tvoc_current": 487.0},
    })
    conn.commit()
    typed = conn.execute(
        "SELECT evidence_attribution_source, evidence_extras "
        "FROM inferences WHERE id=?",
        (inf_id,),
    ).fetchone()
    conn.close()
    assert typed[0] == "smoking"
    assert typed[1] is not None  # extras populated


def test_save_inference_does_not_reference_dropped_evidence_column(db):
    """End-to-end: save_inference() (the public write path) inserts
    a row + populates the typed columns without referencing the
    dropped legacy column."""
    from database.db_logger import save_inference, get_inference_by_id

    inf_id = save_inference(
        event_type="tvoc_spike",
        severity="warning",
        title="Test",
        description="desc",
        action="act",
        evidence={
            "attribution_source": "smoking",
            "feature_vector": {"tvoc_current": 487.0},
        },
        confidence=0.9,
    )
    fetched = get_inference_by_id(inf_id)
    assert fetched is not None
    assert fetched["evidence"] is not None
    assert fetched["evidence"]["attribution_source"] == "smoking"
    assert fetched["evidence"]["feature_vector"] == {"tvoc_current": 487.0}


# ── Migrations module is gone ────────────────────────────────────────────────

def test_database_migrations_module_removed_or_empty():
    """database/migrations.py held the one-shot back-fill helpers that
    became no-ops once the legacy columns were dropped. Either the
    module is gone, or it carries no functional code. Catches anyone
    re-adding the dead helpers."""
    try:
        # Use importlib so pylint doesn't try to statically resolve a
        # deliberately-deleted module (otherwise it raises I1101
        # c-extension-no-member on the dynamic getattr below).
        import importlib
        migrations = importlib.import_module("database.migrations")
    except ImportError:
        # Module is gone — the desired end state.
        return
    # Module still importable: assert it carries no callables (i.e. the
    # back-fill functions weren't reintroduced).
    public_callables = [
        name for name in dir(migrations)
        if not name.startswith("_")
        and callable(getattr(migrations, name))
    ]
    assert public_callables == [], (
        f"database.migrations re-introduced functional code: "
        f"{public_callables}. The back-fill helpers should remain "
        f"deleted; this commit completed the deprecation cycle."
    )
