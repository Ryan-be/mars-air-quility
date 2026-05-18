"""Tests for the typed inferences.evidence_* columns + extras blob.

Pin the split / round-trip semantics for the
mlss_monitor.inference_evidence_storage helpers:

  * split_evidence — separates the 5 read-consistently fields from the
    genuinely-heterogeneous extras dict; aliases ``runner_up`` →
    ``runner_up_id`` (some callers use one name, some the other).
  * persist_evidence — writes typed columns + extras JSON.
  * load_evidence — reads back the same dict shape.

See docs/JSON_STORAGE_AUDIT.md for the column-promotion rationale.
"""
import json
import sqlite3
import sys
from datetime import datetime
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
from mlss_monitor.inference_evidence_storage import (  # noqa: E402
    load_evidence,
    persist_evidence,
    split_evidence,
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


def _seed_inference(conn) -> int:
    """Insert a minimal inferences row, returning its id.

    Bypasses save_inference so tests can exercise the storage helper in
    isolation.
    """
    now = datetime.utcnow().isoformat()
    cur = conn.execute(
        "INSERT INTO inferences (created_at, event_type, severity, title, "
        "description, action, confidence) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (now, "tvoc_spike", "info", "Test", "desc", "action", 0.5),
    )
    conn.commit()
    return cur.lastrowid


# ── split_evidence ───────────────────────────────────────────────────────────

def test_split_evidence_separates_typed_from_extras():
    """Given a dict with both typed-column fields and event-specific
    extras, split_evidence returns (typed_dict, extras_dict)."""
    evidence = {
        "attribution_source": "smoking",
        "attribution_confidence": 0.82,
        "feature_vector": {"tvoc_current": 487.0, "co_current": 30.0},
        "thresholds_used": ["tvoc_high"],
    }
    typed, extras = split_evidence(evidence)
    assert typed == {
        "attribution_source": "smoking",
        "attribution_confidence": 0.82,
    }
    assert extras == {
        "feature_vector": {"tvoc_current": 487.0, "co_current": 30.0},
        "thresholds_used": ["tvoc_high"],
    }


def test_split_evidence_handles_none():
    """split_evidence(None) returns ({}, {}) without raising."""
    typed, extras = split_evidence(None)
    assert typed == {}
    assert extras == {}


def test_split_evidence_handles_empty_dict():
    """An empty input dict returns empty typed and empty extras."""
    typed, extras = split_evidence({})
    assert typed == {}
    assert extras == {}


def test_split_evidence_handles_runner_up_alias():
    """Some callers use ``runner_up`` instead of ``runner_up_id``;
    split_evidence aliases the former into the typed column key."""
    typed, extras = split_evidence({"runner_up": "smoking"})
    assert typed == {"runner_up_id": "smoking"}
    assert extras == {}


def test_split_evidence_runner_up_id_passes_through():
    """Callers using the canonical ``runner_up_id`` key are unmolested."""
    typed, extras = split_evidence({"runner_up_id": "candle"})
    assert typed == {"runner_up_id": "candle"}
    assert extras == {}


def test_split_evidence_captures_all_five_typed_fields():
    """Pin which fields are promoted to typed columns: the regression
    canary if someone tries to drop one without also renaming the column."""
    evidence = {
        "attribution_source": "src",
        "attribution_confidence": 0.7,
        "runner_up_id": "ru",
        "runner_up_confidence": 0.3,
        "detection_method": "ml",
    }
    typed, extras = split_evidence(evidence)
    assert typed == evidence
    assert extras == {}


# ── persist_evidence ─────────────────────────────────────────────────────────

def test_persist_evidence_writes_typed_columns_and_extras(db):
    """After persist_evidence, the 5 typed columns hold the read-consistent
    fields and ``evidence_extras`` holds the JSON-encoded leftover dict."""
    conn = sqlite3.connect(db)
    inf_id = _seed_inference(conn)
    persist_evidence(conn, inf_id, {
        "attribution_source": "smoking",
        "attribution_confidence": 0.82,
        "runner_up_id": "candle",
        "runner_up_confidence": 0.4,
        "detection_method": "ml",
        "feature_vector": {"tvoc_current": 487.0},
        "thresholds_used": ["tvoc_high"],
    })
    conn.commit()
    row = conn.execute(
        "SELECT evidence_attribution_source, evidence_attribution_confidence, "
        "evidence_runner_up_id, evidence_runner_up_confidence, "
        "evidence_detection_method, evidence_extras "
        "FROM inferences WHERE id=?",
        (inf_id,),
    ).fetchone()
    conn.close()
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


def test_persist_evidence_with_runner_up_alias_writes_typed_column(db):
    """A caller passing ``runner_up`` (no ``_id`` suffix) lands in the
    ``evidence_runner_up_id`` typed column, NOT in extras."""
    conn = sqlite3.connect(db)
    inf_id = _seed_inference(conn)
    persist_evidence(conn, inf_id, {"runner_up": "smoking"})
    conn.commit()
    row = conn.execute(
        "SELECT evidence_runner_up_id, evidence_extras "
        "FROM inferences WHERE id=?",
        (inf_id,),
    ).fetchone()
    conn.close()
    assert row[0] == "smoking"
    # extras is None or empty — alias did not leak through.
    assert row[1] is None or json.loads(row[1]) == {}


def test_persist_evidence_with_none_clears_all_columns(db):
    """persist_evidence(conn, id, None) sets all evidence-related
    columns (typed + extras) to NULL. Used when a row is being
    rewritten to drop its evidence."""
    conn = sqlite3.connect(db)
    inf_id = _seed_inference(conn)
    # First write something.
    persist_evidence(conn, inf_id, {"attribution_source": "smoking"})
    conn.commit()
    # Then clear it.
    persist_evidence(conn, inf_id, None)
    conn.commit()
    row = conn.execute(
        "SELECT evidence_extras, evidence_attribution_source, "
        "evidence_attribution_confidence, evidence_runner_up_id, "
        "evidence_runner_up_confidence, evidence_detection_method "
        "FROM inferences WHERE id=?",
        (inf_id,),
    ).fetchone()
    conn.close()
    assert all(v is None for v in row)


def test_persist_evidence_with_only_typed_fields_leaves_extras_null(db):
    """If every key in the input dict is a typed-column field, the
    extras column is NULL (not '{}') so we don't bloat the DB with
    empty-object literals."""
    conn = sqlite3.connect(db)
    inf_id = _seed_inference(conn)
    persist_evidence(conn, inf_id, {"attribution_source": "smoking"})
    conn.commit()
    extras = conn.execute(
        "SELECT evidence_extras FROM inferences WHERE id=?", (inf_id,),
    ).fetchone()[0]
    conn.close()
    assert extras is None


# ── load_evidence ────────────────────────────────────────────────────────────

def test_load_evidence_reconstructs_original_shape(db):
    """persist → load round-trip yields the same dict (modulo key order)."""
    conn = sqlite3.connect(db)
    inf_id = _seed_inference(conn)
    original = {
        "attribution_source": "smoking",
        "attribution_confidence": 0.82,
        "runner_up_id": "candle",
        "runner_up_confidence": 0.4,
        "detection_method": "ml",
        "feature_vector": {"tvoc_current": 487.0},
        "thresholds_used": ["tvoc_high"],
    }
    persist_evidence(conn, inf_id, original)
    conn.commit()
    out = load_evidence(conn, inf_id)
    conn.close()
    assert out == original


def test_load_evidence_for_unknown_inference_returns_none(db):
    """Asking for an id that doesn't exist returns None, not a crash."""
    conn = sqlite3.connect(db)
    out = load_evidence(conn, 999_999)
    conn.close()
    assert out is None


def test_load_evidence_for_inference_with_no_evidence_returns_none(db):
    """A row with NULL typed columns AND NULL extras returns None —
    there is genuinely no evidence."""
    conn = sqlite3.connect(db)
    inf_id = _seed_inference(conn)
    out = load_evidence(conn, inf_id)
    conn.close()
    assert out is None
