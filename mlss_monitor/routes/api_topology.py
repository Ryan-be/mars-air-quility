"""Single-fetch topology snapshot for the /controls page.

The /controls topology view paints its initial frame from one
``GET /api/topology`` call: hub sensors + every active grow unit's
latest telemetry + every smart-plug effector + the persisted node
positions. Subsequent updates land via the SSE bus (Phase 10
wiring) — this endpoint is deliberately read-only and never
publishes events.

Response shape (matched against the prototype data.js from
``docs/assets/effector-map-handoff/``)::

    {
      "hub":       {id: "hub",          kind: "hub",      label, sensors, ...},
      "grows":     [{id: "grow:<n>",     kind: "grow",     label, sensors, ...}, ...],
      "effectors": [{id: "effector:<n>", kind: "effector", parent, label, ...}, ...],
      "layout":    {"<node-id>": {x, y}, ...},
    }

The ``layout`` dict merges two sources of truth:

* ``node_layout`` rows (hub + grow positions) — keyed by ``"hub"``
  for the singleton hub and ``"grow:<id>"`` for each grow unit.
* ``smart_plugs.layout_json`` blobs — keyed by ``"effector:<id>"``.

Keeping the merge here (and not at the v2 layout API boundary) means
the topology endpoint stays the single canonical "first paint" call
that the frontend has to make.
"""
from __future__ import annotations

import sqlite3

from flask import Blueprint, jsonify

from database.init_db import DB_FILE
from mlss_monitor import state as _state
from mlss_monitor.effectors import store as _eff_store


api_topology_bp = Blueprint("api_topology", __name__)


def _latest_grow_telemetry(unit_id: int, conn: sqlite3.Connection) -> dict:
    """Return the most recent grow_telemetry row for *unit_id* as a dict.

    Empty dict when the unit has never reported. The caller layers
    ``.get(key)`` over the result so missing fields surface as ``None``
    rather than KeyError.
    """
    row = conn.execute(
        "SELECT * FROM grow_telemetry WHERE unit_id=? "
        "ORDER BY timestamp_utc DESC LIMIT 1",
        (unit_id,),
    ).fetchone()
    return dict(row) if row else {}


def _load_node_layout(conn: sqlite3.Connection) -> dict:
    """Return the ``node_layout`` table as ``{node-key: {x, y}}``.

    Hub rows are keyed by the bare ``"hub"`` string (there is only ever
    one hub); grow + effector rows are keyed ``"<kind>:<id>"`` to
    match the prototype's data.js layout format.
    """
    layout = {}
    for row in conn.execute(
        "SELECT node_kind, node_id, x, y FROM node_layout"
    ):
        kind, node_id, x_val, y_val = row[0], row[1], row[2], row[3]
        key = node_id if kind == "hub" else f"{kind}:{node_id}"
        layout[key] = {"x": x_val, "y": y_val}
    return layout


def _hub_sensors_from_hot_tier() -> dict:
    """Pull the latest hub-room sensor values off the in-memory tier.

    The hot_tier returns ``NormalisedReading`` dataclass instances
    (see ``mlss_monitor.data_sources.base``), so we use ``getattr``
    against the canonical field names. Missing tier or empty buffer
    both yield a dict of three ``None`` values — the UI's defensive
    ``?? "--"`` then renders the placeholder.
    """
    snap = _state.hot_tier.snapshot() if _state.hot_tier else []
    if not snap:
        return {"temp": None, "rh": None, "co2": None}
    last = snap[-1]
    return {
        "temp": getattr(last, "temperature_c", None),
        "rh":   getattr(last, "humidity_pct", None),
        "co2":  getattr(last, "eco2_ppm", None),
    }


def _derive_mode(plug: dict) -> str:
    """Derive the UI mode label (``auto`` / ``on`` / ``off``) from the row.

    Mirrors the AUTO / ON / OFF segmented control on each effector
    card: if ``auto_mode`` is set the user is back in rule-driven mode
    (regardless of physical state); otherwise the physical state wins.
    """
    if plug["auto_mode"]:
        return "auto"
    if plug["current_state"] == "on":
        return "on"
    return "off"


def _effector_parent(plug: dict) -> str:
    """Return the topology parent key for *plug*.

    Hub-scoped → ``"hub"``; grow-scoped → ``"grow:<unit_id>"``. The
    DB CHECK constraint guarantees those are the only two cases.
    """
    if plug["scope"] == "hub":
        return "hub"
    return f"grow:{plug['grow_unit_id']}"


def _grow_node(unit_row: sqlite3.Row, conn: sqlite3.Connection) -> dict:
    """Project one ``grow_units`` row + its latest telemetry into the
    topology node shape. Sensor values are always present (None when
    no telemetry has landed yet) so the frontend can render uniformly.
    """
    tel = _latest_grow_telemetry(unit_row["id"], conn)
    return {
        "id":         f"grow:{unit_row['id']}",
        "kind":       "grow",
        "label":      unit_row["label"],
        "plant_type": unit_row["plant_type"],
        "phase":      unit_row["current_phase"],
        "medium":     unit_row["medium_type"],
        "sensors": {
            "soil_moisture":    tel.get("soil_moisture_pct"),
            "soil_temp_c":      tel.get("soil_temp_c"),
            "air_temp_c":       tel.get("air_temp_c"),
            "air_humidity_pct": tel.get("air_humidity_pct"),
        },
    }


def _effector_node(plug: dict) -> dict:
    """Project one ``smart_plugs`` row into the topology node shape.

    ``parent`` is the renderer's link source — every effector hangs
    off either the hub or a specific grow. ``mode`` collapses
    ``auto_mode`` + ``current_state`` into the three-state label the
    AUTO/ON/OFF segmented control uses.

    ``last_evaluation`` carries the per-tick rule-reasoning dict so the
    side-panel "Why?" surface can render on the very first paint
    without a follow-up GET /api/effectors/<id> call. ``None`` until
    the evaluator's first pass.
    """
    return {
        "id":              f"effector:{plug['id']}",
        "kind":            "effector",
        "parent":          _effector_parent(plug),
        "label":           plug["label"],
        "effector_type":   plug["effector_type"],
        "mode":            _derive_mode(plug),
        "current_state":   plug["current_state"],
        "is_enabled":      plug["is_enabled"],
        "auto_mode":       plug["auto_mode"],
        "last_evaluation": plug.get("last_evaluation"),
        "kasa_host":       plug["kasa_host"],
        "protocol":        plug["protocol"],
    }


@api_topology_bp.route("/api/topology", methods=["GET"])
def get_topology():
    """Build the single-shot topology snapshot.

    Composition order: hub from in-memory tier (cheap) → grows from a
    single SQL pass (one connection, one transaction) → effectors via
    the store layer (which opens its own short-lived connection but
    surfaces the parsed ``layout`` blob in one hop) → layout merge
    over the two sources.
    """
    hub = {
        "id":      "hub",
        "kind":    "hub",
        "label":   "MLSS Hub",
        "sub":     "central coordinator",
        "sensors": _hub_sensors_from_hot_tier(),
        "notes":   "Whole-room sensors. Coordinates room-level effectors.",
    }

    conn = sqlite3.connect(DB_FILE, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        grows = []
        for unit in conn.execute(
            "SELECT id, label, plant_type, current_phase, medium_type "
            "FROM grow_units WHERE is_active=1 ORDER BY id"
        ).fetchall():
            grows.append(_grow_node(unit, conn))

        layout = _load_node_layout(conn)
    finally:
        conn.close()

    # Effectors + per-effector layout merge — the store layer parses
    # layout_json so we can read it as a dict directly.
    plugs = _eff_store.list_smart_plugs()
    effectors = [_effector_node(p) for p in plugs]
    for plug in plugs:
        if plug.get("layout"):
            layout[f"effector:{plug['id']}"] = plug["layout"]

    return jsonify({
        "hub":       hub,
        "grows":     grows,
        "effectors": effectors,
        "layout":    layout,
    })
