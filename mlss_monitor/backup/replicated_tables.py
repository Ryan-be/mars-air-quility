"""Single source of truth for the set of replicated tables.

Both the lint test (``tests/test_no_direct_writes_to_replicated_tables.py``)
and the backup worker's DB drain loop
(``mlss_monitor/backup/_drain.py``) import ``REPLICATED_TABLES`` from
this module so the set can't drift.

Each entry maps ``table_name → {"pk_columns", "pk_types"}``:

  * ``pk_columns`` — ordered column names of the SQLite primary key.
    The Postgres-side conflict target is ``(*pk_columns, source_pi_id)``.
  * ``pk_types`` — matching Python types. The outbox stores ``pk`` as
    TEXT, so e.g. ``"42"`` must be parsed back to ``int(42)`` before the
    WHERE binding lines up with an INTEGER-PK SQLite column.

The lint test only iterates the keys (table names). The drain module
uses the full dict — see ``parse_pk``, ``_read_live_row``, and
``_ship_row_batch`` in ``mlss_monitor/backup/_drain.py``.

``parse_pk`` lives here (rather than in ``_drain.py``) because it's
purely schema-aware: it consumes ``pk_types`` from this module's
canonical schema. Keeping the parser next to the schema means a
reader who changes a PK type sees the parser in the same file.

Verified against ``database/init_db.py`` + ``database/grow_schema.py``.
Most tables have INTEGER autoincrement PK; the exceptions are
``incidents`` (TEXT id like ``"INC-2026-05-18T12:00:00"``),
``grow_medium_defaults`` (TEXT medium_type PK), and the composite-PK
tables (``incident_alerts``, ``incident_signature_features``,
``grow_unit_capabilities``).

Spec: docs/superpowers/specs/2026-05-18-mlss-backup-design.md
"""
from __future__ import annotations


def parse_pk(pk_str: str, pk_types: list[type]) -> tuple:
    """Convert outbox.pk (always TEXT) into a tuple of typed values.

    Single-PK tables: pk_str is just the value, e.g. "42" → (42,) for
    int PK or "INC-…" → ("INC-…",) for str PK.

    Composite-PK tables: pk_str is f"{a}:{b}" — for example "1:pump"
    for grow_unit_capabilities(unit_id, channel). The "incidents:alerts"
    case is trickier because the incident_id itself contains colons
    (ISO 8601 timestamp like "INC-2026-05-18T12:00:00"), so we always
    split from the RIGHT len(pk_types)-1 times. That way the rightmost
    colon delimits the trailing integer (alert_id / feature_idx) and
    the timestamp's internal colons stay intact.
    """
    if len(pk_types) == 1:
        parts = [pk_str]
    else:
        # Composite. rsplit from the right N-1 times so any colons
        # inside an early-position string PK are preserved.
        parts = pk_str.rsplit(":", len(pk_types) - 1)
    return tuple(t(p) for t, p in zip(pk_types, parts))


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
