"""Single source of truth for the set of replicated tables.

Both the lint test (``tests/test_no_direct_writes_to_replicated_tables.py``)
and the backup worker's DB drain loop (``mlss_monitor/backup/worker.py``)
import ``REPLICATED_TABLES`` from this module so the set can't drift.

Each entry maps ``table_name → {"pk_columns", "pk_types"}``:

  * ``pk_columns`` — ordered column names of the SQLite primary key.
    The Postgres-side conflict target is ``(*pk_columns, source_pi_id)``.
  * ``pk_types`` — matching Python types. The outbox stores ``pk`` as
    TEXT, so e.g. ``"42"`` must be parsed back to ``int(42)`` before the
    WHERE binding lines up with an INTEGER-PK SQLite column.

The lint test only iterates the keys (table names). The worker uses
the full dict — see ``_parse_pk``, ``_read_live_row``, and
``_ship_row_batch`` in ``mlss_monitor/backup/worker.py``.

Verified against ``database/init_db.py`` + ``database/grow_schema.py``.
Most tables have INTEGER autoincrement PK; the exceptions are
``incidents`` (TEXT id like ``"INC-2026-05-18T12:00:00"``),
``grow_medium_defaults`` (TEXT medium_type PK), and the composite-PK
tables (``incident_alerts``, ``incident_signature_features``,
``grow_unit_capabilities``).

Spec: docs/superpowers/specs/2026-05-18-mlss-backup-design.md
"""
from __future__ import annotations

REPLICATED_TABLES: dict[str, dict] = {
    "sensor_data":                 {"pk_columns": ["id"],                         "pk_types": [int]},
    "weather_log":                 {"pk_columns": ["id"],                         "pk_types": [int]},
    "inferences":                  {"pk_columns": ["id"],                         "pk_types": [int]},
    "event_tags":                  {"pk_columns": ["id"],                         "pk_types": [int]},
    "incidents":                   {"pk_columns": ["id"],                         "pk_types": [str]},   # TEXT PK
    "incident_alerts":             {"pk_columns": ["incident_id", "alert_id"],    "pk_types": [str, int]},
    "incident_signature_features": {"pk_columns": ["incident_id", "feature_idx"], "pk_types": [str, int]},
    "grow_units":                  {"pk_columns": ["id"],                         "pk_types": [int]},
    "grow_telemetry":              {"pk_columns": ["id"],                         "pk_types": [int]},
    "grow_unit_capabilities":      {"pk_columns": ["unit_id", "channel"],         "pk_types": [int, str]},
    "grow_watering_events":        {"pk_columns": ["id"],                         "pk_types": [int]},
    "grow_errors":                 {"pk_columns": ["id"],                         "pk_types": [int]},
    "grow_photos":                 {"pk_columns": ["id"],                         "pk_types": [int]},
    "grow_journal_entries":        {"pk_columns": ["id"],                         "pk_types": [int]},
    "grow_plant_profiles":         {"pk_columns": ["id"],                         "pk_types": [int]},
    "grow_light_windows":          {"pk_columns": ["id"],                         "pk_types": [int]},
    "grow_timelapse_jobs":         {"pk_columns": ["id"],                         "pk_types": [int]},
    "grow_medium_defaults":        {"pk_columns": ["medium_type"],                "pk_types": [str]},   # TEXT PK
}
