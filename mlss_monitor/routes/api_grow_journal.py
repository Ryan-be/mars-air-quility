"""GET / POST / PATCH / DELETE /api/grow/units/<id>/journal — operator notes.

Phase 4 Item #7: pin a free-form note to a timestamp on a unit's history.
The History tab's moisture chart and photo-timelapse scrubber overlay these
as markers so an operator can write "started bloom nutrients today" and have
it shown in context next to the soil-moisture curve.

Endpoints:
  GET    /api/grow/units/<id>/journal?range=24h      list (range matches /history)
  POST   /api/grow/units/<id>/journal                create. Body: {timestamp_utc, body}
  PATCH  /api/grow/units/<id>/journal/<entry_id>     edit (author OR admin)
  DELETE /api/grow/units/<id>/journal/<entry_id>     delete (author OR admin)

Range vocabulary (24h / 7d / 30d / 90d / all) matches GET /history and
/photos so the History tab uses one selector across all three panels.

RBAC:
  GET    requires viewer+ (the @require_role decorator below).
  POST   requires controller+.
  PATCH  requires controller+, AND the session user must equal the row's
         author OR hold admin role. The author check happens INSIDE the
         handler because the decorator can't see the row.
  DELETE same as PATCH.

The author/admin gate is the same model the docstring contract calls
out — keeps team annotations from accidentally clobbering each other
while still letting an admin clean up an obviously-mistaken note.
"""
import sqlite3
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request, session

from database.init_db import DB_FILE
from mlss_monitor.grow.api_helpers import RANGE_TO_HOURS
from mlss_monitor.rbac import require_role

api_grow_journal_bp = Blueprint("api_grow_journal", __name__)

# Defensive cap on body length. Long enough for a paragraph or two of
# context (most observed notes are <200 chars), short enough that a
# pasted log dump fails fast rather than bloating the table. Validated
# at create + edit; existing rows above the cap (none today) keep their
# stored body but a future edit must trim.
_BODY_MAX_LEN = 4000


def _parse_iso8601(value: str):
    """Parse an ISO8601 timestamp, normalising trailing ``Z`` to ``+00:00``.

    Returns a naive UTC datetime (tzinfo stripped after conversion to UTC)
    so it round-trips cleanly through SQLite's text-storage format and
    matches what the rest of the grow_* tables store. Raises ValueError
    if the input isn't parseable.
    """
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is not None:
        dt = dt.astimezone(tz=None).replace(tzinfo=None)
    return dt


def _row_to_dict(row):
    """Serialise a grow_journal_entries row for JSON. Empty `updated_at`
    serialises as None rather than the SQLite default empty-string."""
    return {
        "id": row["id"],
        "unit_id": row["unit_id"],
        "timestamp_utc": row["timestamp_utc"],
        "author": row["author"],
        "body": row["body"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _validate_body(body: str) -> str | None:
    """Return None if valid, else the error reason string."""
    if not isinstance(body, str):
        return "body must be a string"
    body = body.strip()
    if not body:
        return "body must be non-empty"
    if len(body) > _BODY_MAX_LEN:
        return f"body must be <= {_BODY_MAX_LEN} chars"
    return None


def _author_or_admin(row_author: str) -> bool:
    """True if the session user is the row's author OR holds admin role."""
    if session.get("user_role") == "admin":
        return True
    return session.get("user") == row_author


@api_grow_journal_bp.route(
    "/api/grow/units/<int:unit_id>/journal", methods=["GET"]
)
@require_role("viewer", "controller", "admin")
def list_entries(unit_id):
    range_str = request.args.get("range", "24h")
    if range_str not in RANGE_TO_HOURS:
        return jsonify({"error": "invalid_range"}), 400
    hours = RANGE_TO_HOURS[range_str]
    cutoff = (
        (datetime.utcnow() - timedelta(hours=hours))
        if hours is not None else None
    )

    conn = sqlite3.connect(DB_FILE, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        if cutoff is not None:
            rows = conn.execute(
                "SELECT id, unit_id, timestamp_utc, author, body, "
                "       created_at, updated_at "
                "FROM grow_journal_entries "
                "WHERE unit_id=? AND timestamp_utc >= ? "
                "ORDER BY timestamp_utc DESC",
                (unit_id, cutoff),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, unit_id, timestamp_utc, author, body, "
                "       created_at, updated_at "
                "FROM grow_journal_entries WHERE unit_id=? "
                "ORDER BY timestamp_utc DESC",
                (unit_id,),
            ).fetchall()
    finally:
        conn.close()
    return jsonify([_row_to_dict(r) for r in rows])


@api_grow_journal_bp.route(
    "/api/grow/units/<int:unit_id>/journal", methods=["POST"]
)
@require_role("controller", "admin")
def create_entry(unit_id):
    """Create a new journal entry. Author = session["user"]."""
    data = request.get_json(silent=True) or {}
    ts_raw = data.get("timestamp_utc")
    body = data.get("body")

    err = _validate_body(body)
    if err:
        return jsonify({"error": err}), 400
    if not ts_raw:
        return jsonify({"error": "timestamp_utc is required"}), 400
    try:
        ts = _parse_iso8601(ts_raw)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid timestamp_utc"}), 400

    author = session.get("user") or "unknown"
    now = datetime.utcnow()

    conn = sqlite3.connect(DB_FILE, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        # Fast-fail on unknown / soft-deleted unit so the operator gets a
        # 404 instead of a successful insert that no one will ever see.
        unit_row = conn.execute(
            "SELECT id FROM grow_units WHERE id=? AND is_active=1",
            (unit_id,),
        ).fetchone()
        if unit_row is None:
            return jsonify({"error": "unit_not_found"}), 404

        cur = conn.execute(
            "INSERT INTO grow_journal_entries "
            "(unit_id, timestamp_utc, author, body, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (unit_id, ts, author, body.strip(), now),
        )
        new_id = cur.lastrowid
        conn.commit()
        row = conn.execute(
            "SELECT id, unit_id, timestamp_utc, author, body, "
            "       created_at, updated_at "
            "FROM grow_journal_entries WHERE id=?",
            (new_id,),
        ).fetchone()
    finally:
        conn.close()
    return jsonify(_row_to_dict(row)), 201


@api_grow_journal_bp.route(
    "/api/grow/units/<int:unit_id>/journal/<int:entry_id>", methods=["PATCH"]
)
@require_role("controller", "admin")
def update_entry(unit_id, entry_id):
    """Edit an entry's body. Author OR admin only.

    Only ``body`` is editable — moving the timestamp would let one
    operator clobber another's notes by retroactively repointing them at
    a different moment, which obscures the audit trail. To "move" a note,
    delete + recreate.
    """
    data = request.get_json(silent=True) or {}
    body = data.get("body")
    err = _validate_body(body)
    if err:
        return jsonify({"error": err}), 400

    conn = sqlite3.connect(DB_FILE, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id, author FROM grow_journal_entries "
            "WHERE id=? AND unit_id=?",
            (entry_id, unit_id),
        ).fetchone()
        if row is None:
            return jsonify({"error": "entry_not_found"}), 404
        if not _author_or_admin(row["author"]):
            return jsonify({"error": "forbidden_author"}), 403

        conn.execute(
            "UPDATE grow_journal_entries SET body=?, updated_at=? WHERE id=?",
            (body.strip(), datetime.utcnow(), entry_id),
        )
        conn.commit()
        updated = conn.execute(
            "SELECT id, unit_id, timestamp_utc, author, body, "
            "       created_at, updated_at "
            "FROM grow_journal_entries WHERE id=?",
            (entry_id,),
        ).fetchone()
    finally:
        conn.close()
    return jsonify(_row_to_dict(updated))


@api_grow_journal_bp.route(
    "/api/grow/units/<int:unit_id>/journal/<int:entry_id>",
    methods=["DELETE"],
)
@require_role("controller", "admin")
def delete_entry(unit_id, entry_id):
    """Delete a journal entry. Author OR admin only."""
    conn = sqlite3.connect(DB_FILE, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT author FROM grow_journal_entries "
            "WHERE id=? AND unit_id=?",
            (entry_id, unit_id),
        ).fetchone()
        if row is None:
            return jsonify({"error": "entry_not_found"}), 404
        if not _author_or_admin(row["author"]):
            return jsonify({"error": "forbidden_author"}), 403

        conn.execute(
            "DELETE FROM grow_journal_entries WHERE id=?", (entry_id,),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True})
