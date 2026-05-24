"""``smart_plugs`` + ``node_layout`` schema.

Effector node-map data model — see
``docs/superpowers/plans/2026-05-22-mlss-topology.md`` (Phase 1) and
``docs/EFFECTOR_NODE_MAP_DESIGN.md``.

The single Kasa fan currently configured by the
``MLSS_FAN_KASA_SMART_PLUG_IP`` env var is seeded as the first row on
first migration so the new model is backwards-compatible with the
legacy ``state.fan_smart_plug`` handle and ``/api/fan/*`` endpoints.
"""
from __future__ import annotations

import json
from datetime import datetime

# Canonical list of effector types. Keep in sync with
# ``mlss_monitor/effectors/base.py::_EFFECTOR_TYPES`` (Phase 2 imports
# from here so the API validator and the DB CHECK constraint never
# disagree).
_EFFECTOR_TYPES = (
    "fan",
    "fan_carbon_filter",
    "ac",
    "whole_room_heater",
    "humidifier",
    "dehumidifier",
    "light_supplementary",
    "heat_pad",
    "generic",
    # Two extra types reserved for the topology UI's "Add effector"
    # picker (see ``docs/EFFECTOR_NODE_MAP_DESIGN.md`` §5). They are
    # currently unused by the rule dispatcher but listed in the CHECK
    # so admins can pre-create rows without a schema migration.
    "circulation_fan",
    "co2_injector",
)
_SCOPES = ("hub", "grow_unit")


def _add_column_if_missing(cur, table: str, col_def: str) -> None:
    """ALTER TABLE … ADD COLUMN, skipped if the column already exists.

    Mirrors :func:`database.grow_schema._add_column_if_missing`. ``col_def``
    is the full column DDL (e.g. ``"last_evaluation_json TEXT"``); the
    column name is the first whitespace-delimited token. PRAGMA-guarded
    so a re-run on a DB that already has the column is a clean no-op
    rather than a swallowed ALTER failure.
    """
    col_name = col_def.split()[0]
    cur.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cur.fetchall()}
    if col_name not in existing:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")


def create_effectors_schema(cur):
    """Create the ``smart_plugs`` + ``node_layout`` tables and seed the fan.

    Called from :func:`database.init_db.create_db` immediately after
    ``create_grow_schema(cur)`` so the FK to ``grow_units(id)`` is valid.
    Idempotent: every CREATE uses IF NOT EXISTS, and the fan-seed step
    is wrapped in ``INSERT OR IGNORE`` keyed on the unique
    ``kasa_host`` column so re-running on a populated DB is a no-op.
    """
    # The 11-value CHECK list is built from the module-level tuple so a
    # future addition only needs to be made in one place (the API
    # validator pulls the same constant).
    types_sql = ", ".join(repr(t) for t in _EFFECTOR_TYPES)
    scopes_sql = ", ".join(repr(s) for s in _SCOPES)
    cur.execute(f"""
    CREATE TABLE IF NOT EXISTS smart_plugs (
      id               INTEGER PRIMARY KEY AUTOINCREMENT,
      label            TEXT    NOT NULL,
      effector_type    TEXT    NOT NULL
                         CHECK(effector_type IN ({types_sql})),
      scope            TEXT    NOT NULL
                         CHECK(scope IN ({scopes_sql})),
      grow_unit_id     INTEGER REFERENCES grow_units(id) ON DELETE SET NULL,
      kasa_host        TEXT    NOT NULL UNIQUE,
      protocol         TEXT    NOT NULL DEFAULT 'kasa',
      is_enabled       INTEGER NOT NULL DEFAULT 1,
      auto_mode        INTEGER NOT NULL DEFAULT 1,
      rules_json       TEXT,
      layout_json      TEXT,
      current_state    TEXT,
      current_state_at DATETIME,
      last_evaluation_json TEXT,
      created_at       DATETIME NOT NULL,
      updated_at       DATETIME,
      CHECK ((scope = 'hub'       AND grow_unit_id IS NULL) OR
             (scope = 'grow_unit' AND grow_unit_id IS NOT NULL))
    );
    """)
    # Migrate older DBs to add the per-effector reasoning blob. The
    # evaluator persists JSON of the form
    # ``{"decision": "on"|"off", "evaluated_at": "...", "reasons": [...]}``
    # on each pass so the side-panel can render "Why is the fan on/off?"
    # without the operator having to scrape SSE history.
    _add_column_if_missing(cur, "smart_plugs", "last_evaluation_json TEXT")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_smart_plugs_grow_unit "
        "ON smart_plugs(grow_unit_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_smart_plugs_enabled "
        "ON smart_plugs(is_enabled)"
    )

    cur.execute("""
    CREATE TABLE IF NOT EXISTS node_layout (
      node_kind  TEXT    NOT NULL
                  CHECK(node_kind IN ('hub','grow','effector')),
      node_id    TEXT    NOT NULL,
      x          REAL    NOT NULL,
      y          REAL    NOT NULL,
      updated_at DATETIME NOT NULL,
      PRIMARY KEY (node_kind, node_id)
    );
    """)

    _seed_existing_fan(cur)


def _seed_existing_fan(cur):
    """Insert the legacy single Kasa fan as ``smart_plugs`` row 1.

    A no-op when ``MLSS_FAN_KASA_SMART_PLUG_IP`` is unset (fresh install
    on a hub with no fan). Idempotent on re-run because ``kasa_host``
    is UNIQUE and we use ``INSERT OR IGNORE``.
    """
    # Local import avoids circular config-import-at-module-load issues
    # when this module is imported from ``database/init_db.py`` very
    # early in app startup.
    from config import config  # pylint: disable=import-outside-toplevel
    ip = config.get("FAN_KASA_SMART_PLUG_IP")
    if not ip:
        return

    now = datetime.utcnow().isoformat()
    # Carry the operator's existing fan thresholds over so the new
    # ``rules_json`` blob behaves identically on first boot. The lookup
    # tolerates a missing ``fan_settings`` table (rare; happens only
    # when the old release didn't run the air-quality migrations) and
    # falls back to the same defaults the legacy code shipped with.
    row = None
    try:
        row = cur.execute(
            "SELECT tvoc_max, temp_max, humidity_max, pm25_max, "
            "       temp_enabled, tvoc_enabled, humidity_enabled, "
            "       pm25_enabled "
            "FROM fan_settings ORDER BY id DESC LIMIT 1"
        ).fetchone()
    except Exception:  # pylint: disable=broad-except
        # fan_settings table missing on a brand-new install — treat as
        # "no operator overrides" and use the shipped defaults below.
        row = None

    rules = {
        "tvoc_max":         row[0] if row else 500,
        "temp_max":         row[1] if row else 20.0,
        "humidity_max":     row[2] if row else 70.0,
        "pm25_max":         row[3] if row else 25.0,
        "temp_enabled":     bool(row[4]) if row else True,
        "tvoc_enabled":     bool(row[5]) if row else True,
        "humidity_enabled": bool(row[6]) if row else False,
        "pm25_enabled":     bool(row[7]) if row else False,
    }

    cur.execute(
        "INSERT OR IGNORE INTO smart_plugs "
        "(label, effector_type, scope, kasa_host, protocol, "
        " is_enabled, auto_mode, rules_json, current_state, created_at) "
        "VALUES ('Room fan', 'fan', 'hub', ?, 'kasa', 1, 1, ?, "
        "        'unknown', ?)",
        (ip, json.dumps(rules), now),
    )
