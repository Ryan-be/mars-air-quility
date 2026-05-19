"""regroup_all enqueues a delete-scope for each strict-mirror incident
table and a row pointer for each INSERTed row.

Note on test seeding: the inferences table CHECK constraint requires
event_type to be one of a fixed set (``tvoc_spike`` is the canonical
spike event used by other incident_grouper tests). Two ``tvoc_spike``
alerts seeded without alert_signal_deps form two singleton components
(edge_probability is 0 without shared sensors), which is fine for
these assertions: 2 incidents -> 2 incidents row enqueues, 2
incident_alerts enqueues, 2*32 signature feature enqueues.
"""
import json
import sqlite3
import sys
import tempfile
import gc
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest


# Stub hardware libs before any app imports — matches conftest.py pattern;
# this file imports nothing app-side at module scope, but the test bodies
# import mlss_monitor.incident_grouper, which transitively pulls in
# database.db_logger via incident_signature_storage's outbox import path.
for _mod in [
    "board", "busio", "adafruit_ahtx0", "adafruit_sgp30",
    "mics6814", "authlib", "authlib.integrations",
    "authlib.integrations.flask_client",
]:
    sys.modules.setdefault(_mod, MagicMock())


@pytest.fixture
def db_path():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    original = init_db.DB_FILE
    init_db.DB_FILE = tmp.name
    init_db.create_db()
    yield tmp.name
    init_db.DB_FILE = original
    gc.collect()
    Path(tmp.name).unlink(missing_ok=True)


def _seed_two_alerts(db_path):
    """Insert two non-dismissed inferences close enough in time to form
    one incident. Returns their ids.

    NOTE: with no alert_signal_deps seeded, edge_probability is 0 so
    these will form two singleton components — that's fine, the tests
    just want to see >= 1 incident enqueue, >= 2 incident_alerts
    enqueues, and >= 32 signature feature enqueues.
    """
    conn = sqlite3.connect(db_path)
    base = datetime(2026, 5, 18, 12, 0, 0)
    cur = conn.execute(
        "INSERT INTO inferences "
        "(event_type, severity, title, created_at, confidence, dismissed) "
        "VALUES ('tvoc_spike', 'warning', 'spike A', ?, 0.8, 0)",
        (base.isoformat(sep=" "),)
    )
    a1 = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO inferences "
        "(event_type, severity, title, created_at, confidence, dismissed) "
        "VALUES ('tvoc_spike', 'warning', 'spike B', ?, 0.8, 0)",
        ((base + timedelta(minutes=5)).isoformat(sep=" "),)
    )
    a2 = cur.lastrowid
    conn.commit()
    conn.close()
    return a1, a2


def _outbox_rows(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return list(conn.execute(
            "SELECT table_name, pk FROM outbox_changes ORDER BY id"))
    finally:
        conn.close()


def _delete_scope_rows(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return list(conn.execute(
            "SELECT table_name, scope_json FROM outbox_delete_scope ORDER BY id"))
    finally:
        conn.close()


def test_regroup_all_enqueues_delete_scope_for_three_tables(db_path):
    from mlss_monitor.incident_grouper import regroup_all
    _seed_two_alerts(db_path)
    regroup_all(db_path)
    scopes = _delete_scope_rows(db_path)
    table_names = {tn for tn, _ in scopes}
    assert {"incidents", "incident_alerts", "incident_signature_features"} <= table_names
    # The whole-table wipe scopes for these three tables should be {}.
    # save_signature also enqueues a per-incident scope of the form
    # {"incident_id": "..."} for incident_signature_features, so we only
    # assert that AT LEAST ONE {} scope exists per table.
    for table in ("incidents", "incident_alerts", "incident_signature_features"):
        whole_table_scopes = [
            sj for tn, sj in scopes
            if tn == table and json.loads(sj) == {}
        ]
        assert whole_table_scopes, (
            f"Expected at least one whole-table delete-scope for {table}; "
            f"saw {[s for s in scopes if s[0] == table]}"
        )


def test_regroup_all_enqueues_incidents_row_per_component(db_path):
    from mlss_monitor.incident_grouper import regroup_all
    _seed_two_alerts(db_path)
    regroup_all(db_path)
    rows = _outbox_rows(db_path)
    # At least one incidents row enqueued.
    incidents_pks = [pk for tn, pk in rows if tn == "incidents"]
    assert len(incidents_pks) >= 1
    # The pk should be the textual incident id (e.g. INC-2026-...).
    assert all(isinstance(pk, str) and len(pk) > 0 for pk in incidents_pks)


def test_regroup_all_enqueues_incident_alerts_composite_pk(db_path):
    from mlss_monitor.incident_grouper import regroup_all
    a1, a2 = _seed_two_alerts(db_path)
    regroup_all(db_path)
    rows = _outbox_rows(db_path)
    alert_pks = [pk for tn, pk in rows if tn == "incident_alerts"]
    # Should contain entries shaped "<incident_id>:<alert_id>".
    assert len(alert_pks) >= 2
    for pk in alert_pks:
        assert ":" in pk
        incident_part, alert_part = pk.rsplit(":", 1)
        assert incident_part  # non-empty
        assert alert_part.isdigit()


def test_regroup_all_enqueues_signature_features_for_each_incident(db_path):
    """save_signature is called per incident with a 32-element vector.
    Each INSERT should enqueue a row pointer."""
    from mlss_monitor.incident_grouper import regroup_all
    _seed_two_alerts(db_path)
    regroup_all(db_path)
    rows = _outbox_rows(db_path)
    sig_pks = [pk for tn, pk in rows if tn == "incident_signature_features"]
    # Vector is 32 elements per incident; at least one incident; so at
    # least 32 entries.
    assert len(sig_pks) >= 32, (
        f"Expected at least 32 signature feature enqueues; got {len(sig_pks)}"
    )
    for pk in sig_pks:
        assert ":" in pk
        incident_part, idx_part = pk.rsplit(":", 1)
        assert incident_part
        assert idx_part.isdigit()
        assert 0 <= int(idx_part) < 32


def test_regroup_all_idempotent_re_enqueues_delete_scope(db_path):
    """Calling regroup_all twice should produce TWO whole-table delete-scope
    entries for incidents — each regroup is a distinct event the server
    processes in order. The shipper coalesces; we don't here."""
    from mlss_monitor.incident_grouper import regroup_all
    _seed_two_alerts(db_path)
    regroup_all(db_path)
    regroup_all(db_path)
    scopes = _delete_scope_rows(db_path)
    incidents_whole_table = [
        s for s in scopes if s[0] == "incidents" and json.loads(s[1]) == {}
    ]
    assert len(incidents_whole_table) == 2
