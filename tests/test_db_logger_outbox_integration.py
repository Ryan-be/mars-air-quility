"""db_logger save helpers enqueue outbox entries automatically.

These are the canonical write sites for the air-quality replicated tables
(sensor_data, weather, inferences, event_tags). After Task 5 of the
MLSS backup feature, each save helper is wrapped in @tee_to_outbox so its
live INSERT/UPDATE and the outbox pointer commit in a single transaction.

The `db` fixture (in conftest.py) already redirects config.DB_FILE to the
tempfile, so the decorator's call-time connection opens the test DB.
"""
import sqlite3


def _outbox_rows(db_path: str, *, table: str | None = None):
    conn = sqlite3.connect(db_path)
    try:
        if table is None:
            return list(conn.execute(
                "SELECT table_name, pk FROM outbox_changes ORDER BY id"))
        return list(conn.execute(
            "SELECT table_name, pk FROM outbox_changes "
            "WHERE table_name=? ORDER BY id", (table,)))
    finally:
        conn.close()


# ── sensor_data — INSERT (log_sensor_data) ──────────────────────────────────

def test_log_sensor_data_enqueues_outbox(db):
    from database.db_logger import log_sensor_data
    pk = log_sensor_data(
        22.0, 45.0, 400, 20,
        annotation=None, fan_power_w=0.0, vpd_kpa=1.2,
        pm1_0=None, pm2_5=None, pm10=None,
        gas_co=None, gas_no2=None, gas_nh3=None,
    )
    assert isinstance(pk, int) and pk > 0
    assert _outbox_rows(db, table="sensor_data") == [("sensor_data", str(pk))]


def test_log_sensor_data_existing_positional_call_still_works(db):
    """Pre-refactor callers (mlss_monitor/app.py, tests) pass `temp` first."""
    from database.db_logger import log_sensor_data
    pk = log_sensor_data(22.0, 45.0, 400, 20)
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT temperature, humidity, eco2, tvoc FROM sensor_data WHERE id=?",
            (pk,),
        ).fetchone()
    finally:
        conn.close()
    assert row == (22.0, 45.0, 400, 20)


# ── sensor_data — UPDATE (annotation helpers) ───────────────────────────────

def test_add_annotation_enqueues_outbox(db):
    from database.db_logger import log_sensor_data, add_annotation
    sensor_id = log_sensor_data(22.0, 45.0, 400, 20)
    # Clear the INSERT outbox entry so we can isolate the UPDATE
    conn = sqlite3.connect(db)
    try:
        conn.execute("DELETE FROM outbox_changes")
        conn.commit()
    finally:
        conn.close()
    add_annotation(sensor_id, "cooking smoke")
    assert _outbox_rows(db, table="sensor_data") == [
        ("sensor_data", str(sensor_id))
    ]


def test_remove_annotation_enqueues_outbox(db):
    from database.db_logger import log_sensor_data, add_annotation, remove_annotation
    sensor_id = log_sensor_data(22.0, 45.0, 400, 20)
    add_annotation(sensor_id, "x")
    conn = sqlite3.connect(db)
    try:
        conn.execute("DELETE FROM outbox_changes")
        conn.commit()
    finally:
        conn.close()
    remove_annotation(sensor_id)
    assert _outbox_rows(db, table="sensor_data") == [
        ("sensor_data", str(sensor_id))
    ]


def test_edit_annotation_enqueues_outbox(db):
    from database.db_logger import log_sensor_data, add_annotation, edit_annotation
    sensor_id = log_sensor_data(22.0, 45.0, 400, 20)
    add_annotation(sensor_id, "old text")
    conn = sqlite3.connect(db)
    try:
        conn.execute("DELETE FROM outbox_changes")
        conn.commit()
    finally:
        conn.close()
    edit_annotation(sensor_id, "new text")
    assert _outbox_rows(db, table="sensor_data") == [
        ("sensor_data", str(sensor_id))
    ]


# ── weather — INSERT (log_weather) ──────────────────────────────────────────

def test_log_weather_enqueues_outbox(db):
    from database.db_logger import log_weather
    pk = log_weather(
        temp=18.5, humidity=55.0, feels_like=18.0,
        wind_speed=3.2, weather_code=1, uv_index=2.0,
    )
    assert isinstance(pk, int) and pk > 0
    # Live table is named `weather_log` in database/init_db.py
    assert _outbox_rows(db, table="weather_log") == [("weather_log", str(pk))]


# ── inferences — INSERT (save_inference) ────────────────────────────────────

def test_save_inference_enqueues_outbox(db):
    from database.db_logger import save_inference
    pk = save_inference(
        event_type="tvoc_spike",
        severity="warning",
        title="Test",
        description="desc",
        action="act",
        evidence={"attribution_source": "cooking",
                  "attribution_confidence": 0.9},
        confidence=0.5,
    )
    assert isinstance(pk, int) and pk > 0
    # save_inference also calls persist_evidence which UPDATEs the same row.
    # The outbox coalesces multiple writes to the same (table, pk), so exactly
    # one row entry should be present for this inference.
    assert _outbox_rows(db, table="inferences") == [("inferences", str(pk))]


def test_save_inference_returns_lastrowid(db):
    """Existing callers store the return value (api_history.py uses it to
    attach tags). Verify that contract is preserved."""
    from database.db_logger import save_inference
    pk = save_inference(
        event_type="tvoc_spike",
        severity="warning",
        title="t", description="d", action="a",
        evidence={}, confidence=0.5,
    )
    assert isinstance(pk, int) and pk > 0


# ── event_tags — INSERT (add_inference_tag) ─────────────────────────────────

def test_add_inference_tag_enqueues_outbox(db):
    from database.db_logger import save_inference, add_inference_tag
    inf_id = save_inference(
        event_type="tvoc_spike", severity="warning",
        title="t", description="d", action="a",
        evidence={}, confidence=0.5,
    )
    conn = sqlite3.connect(db)
    try:
        conn.execute("DELETE FROM outbox_changes")
        conn.commit()
    finally:
        conn.close()
    add_inference_tag(inf_id, "cooking")
    # PK of the inserted event_tags row
    conn = sqlite3.connect(db)
    try:
        tag_id = conn.execute(
            "SELECT id FROM event_tags WHERE inference_id=? AND tag=?",
            (inf_id, "cooking"),
        ).fetchone()[0]
    finally:
        conn.close()
    assert _outbox_rows(db, table="event_tags") == [
        ("event_tags", str(tag_id))
    ]


# ── DELETEs do NOT enqueue (append-mostly) ──────────────────────────────────

def test_remove_inference_tag_does_not_enqueue_outbox(db):
    """DELETEs never propagate — append-mostly server semantics."""
    from database.db_logger import (
        save_inference, add_inference_tag, remove_inference_tag,
    )
    inf_id = save_inference(
        event_type="tvoc_spike", severity="warning",
        title="t", description="d", action="a",
        evidence={}, confidence=0.5,
    )
    add_inference_tag(inf_id, "cooking")
    conn = sqlite3.connect(db)
    try:
        conn.execute("DELETE FROM outbox_changes")
        conn.commit()
    finally:
        conn.close()
    remove_inference_tag(inf_id, "cooking")
    # No new outbox entry for event_tags after the DELETE
    assert _outbox_rows(db, table="event_tags") == []
