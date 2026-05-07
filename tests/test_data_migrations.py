"""Tests for the historic-data migrations in database.migrations.

Pin the back-fill, idempotency, and corrupt-data semantics for the
two migrations that promote pre-existing JSON-in-TEXT rows to the
typed columns introduced in Commits B (9c745fe) and C (85ce40e).

  * migrate_incident_signatures — incidents.signature (JSON list) →
    incident_signature_features sub-table rows.
  * migrate_inference_evidence — inferences.evidence (JSON dict) →
    typed evidence_* columns + evidence_extras JSON.

Both must be idempotent (re-run = 0 rows touched), fast on the empty
case (filtered by NOT EXISTS / IS NULL), and robust to corrupt legacy
data (log + skip, never raise — a handful of bad rows shouldn't block
startup).
"""
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock

# Stub hardware libs and authlib before app imports (matches conftest pattern;
# database.init_db pulls in config which is loaded at module-import time and
# indirectly touches sys.modules).
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
from database.migrations import (  # noqa: E402
    migrate_incident_signatures,
    migrate_inference_evidence,
    run_all_migrations,
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


def _seed_incident(conn, incident_id, signature_json):
    """Insert a minimal pre-migration incident with only the legacy JSON
    column populated (no rows in incident_signature_features)."""
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


def _seed_inference_with_legacy_evidence(conn, evidence_json):
    """Insert a pre-migration inferences row whose only evidence
    representation is the legacy JSON column. Returns the row id."""
    now = datetime.utcnow().isoformat()
    cur = conn.execute(
        "INSERT INTO inferences (created_at, event_type, severity, title, "
        "description, action, evidence, confidence) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (now, "tvoc_spike", "info", "Test", "desc", "action",
         evidence_json, 0.5),
    )
    conn.commit()
    return cur.lastrowid


# ── migrate_incident_signatures ──────────────────────────────────────────────

def test_migrate_incident_signatures_backfills_pre_migration_rows(db):
    """A pre-migration incident (legacy JSON only, sub-table empty) is
    back-filled into incident_signature_features after migration."""
    conn = sqlite3.connect(db)
    vector = [0.1, 0.2, 0.3, 0.4]
    _seed_incident(conn, "INC-BF-1", json.dumps(vector))
    n = migrate_incident_signatures(conn)
    rows = conn.execute(
        "SELECT feature_idx, value FROM incident_signature_features "
        "WHERE incident_id=? ORDER BY feature_idx",
        ("INC-BF-1",),
    ).fetchall()
    conn.close()
    assert n == 1
    assert rows == [(0, 0.1), (1, 0.2), (2, 0.3), (3, 0.4)]


def test_migrate_incident_signatures_skips_already_migrated(db):
    """An incident that already has rows in the sub-table is not touched
    (no duplicate inserts, no PRIMARY-KEY collisions)."""
    conn = sqlite3.connect(db)
    _seed_incident(conn, "INC-DONE-1", json.dumps([1.0, 2.0]))
    # Pre-populate sub-table to mark this incident as already migrated.
    conn.execute(
        "INSERT INTO incident_signature_features "
        "(incident_id, feature_idx, value) VALUES (?, ?, ?)",
        ("INC-DONE-1", 0, 1.0),
    )
    conn.commit()
    n = migrate_incident_signatures(conn)
    count = conn.execute(
        "SELECT COUNT(*) FROM incident_signature_features "
        "WHERE incident_id=?",
        ("INC-DONE-1",),
    ).fetchone()[0]
    conn.close()
    assert n == 0
    # Pre-existing row stayed; nothing duplicated.
    assert count == 1


def test_migrate_incident_signatures_skips_empty_signature(db):
    """Incidents with legacy signature='[]' or NULL are not migrated
    (no point inserting zero rows)."""
    conn = sqlite3.connect(db)
    _seed_incident(conn, "INC-EMPTY-1", "[]")
    # Inserting NULL requires bypassing _seed_incident's NOT NULL default.
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    later = (datetime.utcnow() + timedelta(minutes=5)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    conn.execute(
        "INSERT INTO incidents (id, started_at, ended_at, max_severity, "
        "confidence, title, signature) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("INC-EMPTY-2", now, later, "info", 0.5, "Test", ""),
    )
    conn.commit()
    n = migrate_incident_signatures(conn)
    count = conn.execute(
        "SELECT COUNT(*) FROM incident_signature_features"
    ).fetchone()[0]
    conn.close()
    assert n == 0
    assert count == 0


def test_migrate_incident_signatures_handles_corrupt_json_gracefully(db, caplog):
    """Invalid JSON in the legacy signature column → log + skip the
    incident, don't raise (one bad historical row shouldn't block
    startup for the other 999)."""
    conn = sqlite3.connect(db)
    _seed_incident(conn, "INC-BAD-1", "not-json-at-all{")
    _seed_incident(conn, "INC-OK-1", json.dumps([5.5, 6.6]))
    n = migrate_incident_signatures(conn)
    bad_rows = conn.execute(
        "SELECT COUNT(*) FROM incident_signature_features WHERE incident_id=?",
        ("INC-BAD-1",),
    ).fetchone()[0]
    ok_rows = conn.execute(
        "SELECT COUNT(*) FROM incident_signature_features WHERE incident_id=?",
        ("INC-OK-1",),
    ).fetchone()[0]
    conn.close()
    # Bad row was skipped, good row was migrated.
    assert n == 1
    assert bad_rows == 0
    assert ok_rows == 2


def test_migrate_incident_signatures_handles_non_list_payload(db):
    """A legacy signature that decodes to a non-list (e.g. a dict) →
    log + skip the incident, don't raise."""
    conn = sqlite3.connect(db)
    _seed_incident(conn, "INC-DICT-1", json.dumps({"not": "a list"}))
    n = migrate_incident_signatures(conn)
    rows = conn.execute(
        "SELECT COUNT(*) FROM incident_signature_features WHERE incident_id=?",
        ("INC-DICT-1",),
    ).fetchone()[0]
    conn.close()
    assert n == 0
    assert rows == 0


def test_migrate_incident_signatures_returns_count(db):
    """Return value matches the number of incidents actually migrated
    (not the number of feature rows inserted)."""
    conn = sqlite3.connect(db)
    _seed_incident(conn, "INC-CNT-1", json.dumps([1.0, 2.0, 3.0]))
    _seed_incident(conn, "INC-CNT-2", json.dumps([4.0, 5.0]))
    _seed_incident(conn, "INC-CNT-3", json.dumps([6.0]))
    n = migrate_incident_signatures(conn)
    conn.close()
    assert n == 3  # 3 incidents migrated, regardless of vector lengths


def test_migrate_incident_signatures_is_idempotent(db):
    """Calling the migration twice: the second call returns 0 (and
    inserts no extra rows). Pin the rerun-safe contract."""
    conn = sqlite3.connect(db)
    _seed_incident(conn, "INC-IDEM-1", json.dumps([1.0, 2.0, 3.0]))
    n1 = migrate_incident_signatures(conn)
    n2 = migrate_incident_signatures(conn)
    count = conn.execute(
        "SELECT COUNT(*) FROM incident_signature_features WHERE incident_id=?",
        ("INC-IDEM-1",),
    ).fetchone()[0]
    conn.close()
    assert n1 == 1
    assert n2 == 0
    assert count == 3


# ── migrate_inference_evidence ───────────────────────────────────────────────

def test_migrate_inference_evidence_backfills_pre_migration_rows(db):
    """A pre-migration inference (legacy evidence JSON only, all 6 typed
    cols NULL) is back-filled into the typed columns + extras."""
    conn = sqlite3.connect(db)
    legacy = {
        "attribution_source": "smoking",
        "attribution_confidence": 0.82,
        "runner_up_id": "candle",
        "runner_up_confidence": 0.4,
        "detection_method": "ml",
        "feature_vector": {"tvoc_current": 487.0},
        "thresholds_used": ["tvoc_high"],
    }
    inf_id = _seed_inference_with_legacy_evidence(conn, json.dumps(legacy))
    n = migrate_inference_evidence(conn)
    row = conn.execute(
        "SELECT evidence_attribution_source, evidence_attribution_confidence, "
        "evidence_runner_up_id, evidence_runner_up_confidence, "
        "evidence_detection_method, evidence_extras "
        "FROM inferences WHERE id=?",
        (inf_id,),
    ).fetchone()
    conn.close()
    assert n == 1
    assert row[0] == "smoking"
    assert row[1] == 0.82
    assert row[2] == "candle"
    assert row[3] == 0.4
    assert row[4] == "ml"
    extras = json.loads(row[5])
    assert extras == {
        "feature_vector": {"tvoc_current": 487.0},
        "thresholds_used": ["tvoc_high"],
    }


def test_migrate_inference_evidence_skips_already_migrated(db):
    """An inferences row with at least one typed column non-NULL is
    treated as already-migrated and is not touched."""
    conn = sqlite3.connect(db)
    inf_id = _seed_inference_with_legacy_evidence(
        conn, json.dumps({"attribution_source": "old"}),
    )
    # Mark migrated: set typed col + matching extras.
    conn.execute(
        "UPDATE inferences SET evidence_attribution_source=? WHERE id=?",
        ("already_done", inf_id),
    )
    conn.commit()
    n = migrate_inference_evidence(conn)
    val = conn.execute(
        "SELECT evidence_attribution_source FROM inferences WHERE id=?",
        (inf_id,),
    ).fetchone()[0]
    conn.close()
    assert n == 0
    # Row was not re-migrated; pre-existing typed value preserved.
    assert val == "already_done"


def test_migrate_inference_evidence_skips_partial_already_migrated(db):
    """A row with extras_json set but typed cols NULL is still treated
    as already-migrated (extras_json being set indicates a previous
    migration touched the row — e.g. evidence dict had only extras keys)."""
    conn = sqlite3.connect(db)
    inf_id = _seed_inference_with_legacy_evidence(
        conn, json.dumps({"attribution_source": "fresh"}),
    )
    # Simulate a prior migration that wrote only extras (no typed cols).
    conn.execute(
        "UPDATE inferences SET evidence_extras=? WHERE id=?",
        (json.dumps({"some": "leftover"}), inf_id),
    )
    conn.commit()
    n = migrate_inference_evidence(conn)
    extras = conn.execute(
        "SELECT evidence_extras FROM inferences WHERE id=?",
        (inf_id,),
    ).fetchone()[0]
    typed = conn.execute(
        "SELECT evidence_attribution_source FROM inferences WHERE id=?",
        (inf_id,),
    ).fetchone()[0]
    conn.close()
    assert n == 0
    # Extras was preserved; typed col was NOT populated by re-running migration.
    assert json.loads(extras) == {"some": "leftover"}
    assert typed is None


def test_migrate_inference_evidence_handles_corrupt_json(db):
    """Non-JSON garbage in the legacy evidence column → log + skip,
    never raise."""
    conn = sqlite3.connect(db)
    bad_id = _seed_inference_with_legacy_evidence(conn, "<<not json>>")
    good_id = _seed_inference_with_legacy_evidence(
        conn, json.dumps({"attribution_source": "ok"}),
    )
    n = migrate_inference_evidence(conn)
    bad_typed = conn.execute(
        "SELECT evidence_attribution_source FROM inferences WHERE id=?",
        (bad_id,),
    ).fetchone()[0]
    good_typed = conn.execute(
        "SELECT evidence_attribution_source FROM inferences WHERE id=?",
        (good_id,),
    ).fetchone()[0]
    conn.close()
    assert n == 1
    assert bad_typed is None
    assert good_typed == "ok"


def test_migrate_inference_evidence_handles_non_dict_payload(db):
    """Legacy evidence that decodes to a list (or any non-dict) → log
    + skip, don't raise."""
    conn = sqlite3.connect(db)
    inf_id = _seed_inference_with_legacy_evidence(
        conn, json.dumps(["not", "a", "dict"]),
    )
    n = migrate_inference_evidence(conn)
    typed = conn.execute(
        "SELECT evidence_attribution_source FROM inferences WHERE id=?",
        (inf_id,),
    ).fetchone()[0]
    conn.close()
    assert n == 0
    assert typed is None


def test_migrate_inference_evidence_handles_runner_up_alias(db):
    """Pre-migration evidence using ``runner_up`` (no ``_id``) lands in
    ``evidence_runner_up_id`` — matches the alias handling in
    split_evidence so historical rows don't lose this field on migration."""
    conn = sqlite3.connect(db)
    inf_id = _seed_inference_with_legacy_evidence(
        conn, json.dumps({"runner_up": "smoking"}),
    )
    n = migrate_inference_evidence(conn)
    row = conn.execute(
        "SELECT evidence_runner_up_id, evidence_extras FROM inferences "
        "WHERE id=?",
        (inf_id,),
    ).fetchone()
    conn.close()
    assert n == 1
    assert row[0] == "smoking"
    # The alias did not leak into extras.
    assert row[1] is None or json.loads(row[1]) == {}


def test_migrate_inference_evidence_returns_count(db):
    """Return value matches the number of inferences rows migrated."""
    conn = sqlite3.connect(db)
    for i in range(4):
        _seed_inference_with_legacy_evidence(
            conn, json.dumps({"attribution_source": f"src_{i}"}),
        )
    n = migrate_inference_evidence(conn)
    conn.close()
    assert n == 4


def test_migrate_inference_evidence_is_idempotent(db):
    """Second call returns 0 and produces no further changes."""
    conn = sqlite3.connect(db)
    _seed_inference_with_legacy_evidence(
        conn, json.dumps({"attribution_source": "idem"}),
    )
    n1 = migrate_inference_evidence(conn)
    n2 = migrate_inference_evidence(conn)
    conn.close()
    assert n1 == 1
    assert n2 == 0


# ── run_all_migrations ───────────────────────────────────────────────────────

def test_run_all_migrations_returns_summary_dict(db):
    """Both migrations are called; dict has both keys with int counts."""
    conn = sqlite3.connect(db)
    _seed_incident(conn, "INC-ALL-1", json.dumps([1.0, 2.0]))
    _seed_inference_with_legacy_evidence(
        conn, json.dumps({"attribution_source": "x"}),
    )
    conn.close()
    summary = run_all_migrations(db)
    assert set(summary.keys()) == {"incident_signatures", "inference_evidence"}
    assert summary["incident_signatures"] == 1
    assert summary["inference_evidence"] == 1


def test_run_all_migrations_with_no_data_returns_zero_counts(db):
    """Fresh DB with no legacy rows → both migrations return 0 (and
    do so quickly via index lookups, not full scans)."""
    summary = run_all_migrations(db)
    assert summary == {
        "incident_signatures": 0,
        "inference_evidence": 0,
    }


def test_create_db_runs_migrations_on_startup(tmp_path, monkeypatch):
    """End-to-end: pre-seed legacy rows directly via sqlite3 (so they
    look like rows from a pre-migration release), then call create_db
    a second time and verify both migrations executed and the typed
    cols are populated.

    This brings the database/init_db.py wiring under coverage and pins
    the contract that startup back-fills historic data automatically.
    """
    db_path = str(tmp_path / "boot.db")
    import mlss_monitor.hot_tier as ht
    monkeypatch.setattr(dbi, "DB_FILE", db_path)
    monkeypatch.setattr(dbl, "DB_FILE", db_path)
    monkeypatch.setattr(udb, "DB_FILE", db_path)
    monkeypatch.setattr(ht, "DB_FILE", db_path)

    # First run: build schema from scratch.
    dbi.create_db()

    # Pre-seed legacy-format rows (sub-table empty + typed cols NULL).
    conn = sqlite3.connect(db_path)
    _seed_incident(conn, "INC-BOOT-1", json.dumps([0.5, 0.6, 0.7]))
    inf_id = _seed_inference_with_legacy_evidence(
        conn, json.dumps({
            "attribution_source": "boot_src",
            "feature_vector": {"a": 1},
        }),
    )
    conn.close()

    # Second run: migrations should fire and back-fill.
    dbi.create_db()

    conn = sqlite3.connect(db_path)
    sig_rows = conn.execute(
        "SELECT feature_idx, value FROM incident_signature_features "
        "WHERE incident_id=? ORDER BY feature_idx",
        ("INC-BOOT-1",),
    ).fetchall()
    typed_src = conn.execute(
        "SELECT evidence_attribution_source FROM inferences WHERE id=?",
        (inf_id,),
    ).fetchone()[0]
    extras = conn.execute(
        "SELECT evidence_extras FROM inferences WHERE id=?",
        (inf_id,),
    ).fetchone()[0]
    conn.close()
    assert sig_rows == [(0, 0.5), (1, 0.6), (2, 0.7)]
    assert typed_src == "boot_src"
    assert json.loads(extras) == {"feature_vector": {"a": 1}}
