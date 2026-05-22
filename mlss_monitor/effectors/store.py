"""Pure CRUD against the ``smart_plugs`` table.

Every function opens its own short-lived SQLite connection (5 s timeout,
matching the rest of the codebase's hub-room writers) so the store can
be called from any thread — Flask request handlers, the Phase 3
evaluator background loop, or test fixtures — without sharing
connection state.

Return shapes:

* ``list_smart_plugs() -> list[dict]``     — all rows, ``rules`` /
  ``layout`` parsed from JSON into dicts (or ``None``).
* ``get_smart_plug(id) -> dict | None``    — row or None on miss.
* ``create_smart_plug(**kw) -> int``       — last_insert_rowid.
* ``update_smart_plug(id, **kw) -> bool``  — True on update, False on miss.
* ``delete_smart_plug(id) -> bool``        — True on delete, False on miss.
* ``update_layout(id, x, y) -> bool``      — convenience: writes
  ``layout_json = {"x": x, "y": y}``.
* ``update_last_state(id, state) -> bool`` — writes ``current_state`` +
  ``current_state_at`` together (atomic single-row UPDATE).

All write paths refresh ``updated_at`` so the v2 API's PATCH endpoints
can surface "last edited at" without a separate column-tracking layer.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from database.init_db import DB_FILE


# Connection timeout for every short-lived store-layer write. Matches
# ``api_grow_units.list_units`` and the rest of the hub-room writers so
# a slow background flush from another thread can't deadlock a request
# for longer than 5 seconds.
_TIMEOUT_S = 5

# Columns we always select in get/list. Kept as a tuple constant so the
# row-shape used by callers (the v2 API + the Phase 3 evaluator) stays
# pinned to a single source of truth.
_COLUMNS = (
    "id", "label", "effector_type", "scope", "grow_unit_id",
    "kasa_host", "protocol", "is_enabled", "auto_mode",
    "rules_json", "layout_json", "current_state",
    "current_state_at", "created_at", "updated_at",
)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE, timeout=_TIMEOUT_S)
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict:
    """Convert a sqlite3.Row to a dict + parse rules_json / layout_json."""
    out = {k: row[k] for k in _COLUMNS}
    out["rules"] = _parse_json(out.pop("rules_json"))
    out["layout"] = _parse_json(out.pop("layout_json"))
    return out


def _parse_json(value: str | None) -> dict | None:
    """Return a parsed dict or ``None``. Tolerates malformed JSON.

    Malformed JSON in the DB is a real possibility — an admin could
    have edited the row by hand from sqlite3 CLI. Falling back to
    ``None`` keeps the API endpoints responsive at the cost of
    silently dropping the bad blob; the operator can then re-set the
    field via the UI.
    """
    if value is None:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def list_smart_plugs() -> list[dict]:
    """Return every row in ``smart_plugs`` in id order."""
    conn = _connect()
    try:
        rows = conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM smart_plugs ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(r) for r in rows]


def get_smart_plug(plug_id: int) -> dict | None:
    """Return one row by id, or ``None`` when missing."""
    conn = _connect()
    try:
        row = conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM smart_plugs WHERE id=?",
            (plug_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return _row_to_dict(row)


def create_smart_plug(  # pylint: disable=too-many-arguments
    *,
    label: str,
    effector_type: str,
    scope: str,
    kasa_host: str,
    grow_unit_id: int | None = None,
    protocol: str = "kasa",
    is_enabled: int = 1,
    auto_mode: int = 1,
    rules: dict | None = None,
    layout: dict | None = None,
    current_state: str = "unknown",
) -> int:
    """Insert a new row and return its id.

    Raises :class:`sqlite3.IntegrityError` on duplicate ``kasa_host``,
    invalid ``effector_type`` / ``scope`` (CHECK constraint), or a
    scope-mismatched ``grow_unit_id``. The API layer translates those
    into 400/409 responses.
    """
    now = datetime.utcnow().isoformat()
    rules_json = json.dumps(rules) if rules is not None else None
    layout_json = json.dumps(layout) if layout is not None else None
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO smart_plugs "
            "(label, effector_type, scope, grow_unit_id, kasa_host, "
            " protocol, is_enabled, auto_mode, rules_json, layout_json, "
            " current_state, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                label, effector_type, scope, grow_unit_id, kasa_host,
                protocol, is_enabled, auto_mode, rules_json, layout_json,
                current_state, now,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


# Whitelist of columns the API PATCH endpoint may mutate. Keeping this
# explicit (rather than accepting ``**kw`` straight into a dynamic
# UPDATE) means a typo in the caller surfaces as a KeyError at the API
# boundary instead of silently no-op'ing.
_UPDATABLE_COLUMNS = frozenset({
    "label", "effector_type", "scope", "grow_unit_id",
    "kasa_host", "protocol", "is_enabled", "auto_mode",
    "current_state",
})


def update_smart_plug(plug_id: int, **fields: Any) -> bool:
    """Patch the named fields on one row. Returns False on missing row.

    ``rules`` / ``layout`` may be passed as dicts; the store serialises
    them to JSON before writing.
    """
    sets: list[str] = []
    values: list[Any] = []
    for key, val in fields.items():
        if key == "rules":
            sets.append("rules_json = ?")
            values.append(json.dumps(val) if val is not None else None)
        elif key == "layout":
            sets.append("layout_json = ?")
            values.append(json.dumps(val) if val is not None else None)
        elif key in _UPDATABLE_COLUMNS:
            sets.append(f"{key} = ?")
            values.append(val)
        else:
            raise KeyError(f"Unknown column: {key!r}")
    if not sets:
        return False
    sets.append("updated_at = ?")
    values.append(datetime.utcnow().isoformat())
    values.append(plug_id)
    conn = _connect()
    try:
        cur = conn.execute(
            f"UPDATE smart_plugs SET {', '.join(sets)} WHERE id = ?",
            values,
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_smart_plug(plug_id: int) -> bool:
    """Hard-delete one row. Returns False on missing row."""
    conn = _connect()
    try:
        cur = conn.execute(
            "DELETE FROM smart_plugs WHERE id = ?", (plug_id,)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def update_layout(plug_id: int, x: float, y: float) -> bool:
    """Persist ``{"x":x,"y":y}`` to ``layout_json``. Returns False on miss."""
    now = datetime.utcnow().isoformat()
    conn = _connect()
    try:
        cur = conn.execute(
            "UPDATE smart_plugs SET layout_json = ?, updated_at = ? "
            "WHERE id = ?",
            (json.dumps({"x": x, "y": y}), now, plug_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def update_last_state(plug_id: int, current_state: str) -> bool:
    """Atomically update ``current_state`` + ``current_state_at``."""
    now = datetime.utcnow().isoformat()
    conn = _connect()
    try:
        cur = conn.execute(
            "UPDATE smart_plugs "
            "SET current_state = ?, current_state_at = ?, updated_at = ? "
            "WHERE id = ?",
            (current_state, now, now, plug_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
