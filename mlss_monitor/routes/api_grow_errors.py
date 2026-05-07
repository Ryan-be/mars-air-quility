"""GET / PATCH /api/grow/errors — fleet-wide error log + resolve/snooze.

Two endpoints on this blueprint:

* GET  /api/grow/errors                 (viewer/controller/admin)
    Filterable list of every grow_errors row across the fleet, JOIN'd to
    grow_units so each row carries the unit label. Filter query params:
      - unresolved_only (bool, default false)
      - unit_id (int)
      - severity (info / warning / critical)
      - kind (string)
      - since (ISO8601 timestamp)
      - limit (int, default 100, hard cap 500)
    Snoozed rows are NOT filtered out server-side; the client renders
    them muted when snoozed_until > now. Keeping the API simple this way
    means admins can still see + un-snooze them, and the muted vs
    unmuted decision is purely visual.

* PATCH /api/grow/errors/<id>           (admin only)
    Resolve / unresolve / snooze / unsnooze a single error row. Body:
      - resolved_at: "now" | <iso8601> | null   (set/unset)
      - snoozed_until: <iso8601> | null         (set/unset)
    Both fields optional but at least one required (empty body → 400).
    Combined "resolve and snooze" PATCH is supported (one round-trip).

The fleet-wide errors page (/grow/errors) is the consumer.
"""
import sqlite3
from datetime import datetime
from flask import Blueprint, jsonify, request

from database.init_db import DB_FILE
from mlss_monitor.rbac import require_role

api_grow_errors_bp = Blueprint("api_grow_errors", __name__)

_VALID_SEVERITIES = ("info", "warning", "critical")
_DEFAULT_LIMIT = 100
_MAX_LIMIT = 500


def _parse_iso8601(value: str):
    """Parse an ISO8601 string; return datetime or raise ValueError."""
    # datetime.fromisoformat is lenient enough for "2026-05-06T12:00:00",
    # "2026-05-06T12:00:00Z", and "+00:00" offsets in modern Python.
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


@api_grow_errors_bp.route("/api/grow/errors", methods=["GET"])
@require_role("viewer", "controller", "admin")
def list_errors():
    args = request.args

    # unresolved_only: accept "1"/"true"/"yes" (case-insensitive). Default false.
    unresolved_raw = (args.get("unresolved_only") or "").strip().lower()
    unresolved_only = unresolved_raw in ("1", "true", "yes")

    # unit_id (optional, int)
    unit_id = None
    if args.get("unit_id"):
        try:
            unit_id = int(args["unit_id"])
        except ValueError:
            return jsonify({"error": "invalid_unit_id"}), 400

    # severity (optional)
    severity = args.get("severity")
    if severity is not None and severity not in _VALID_SEVERITIES:
        return jsonify({"error": "invalid_severity"}), 400

    # kind (optional, free-form text)
    kind = args.get("kind") or None

    # since (optional, ISO8601)
    since_dt = None
    if args.get("since"):
        try:
            since_dt = _parse_iso8601(args["since"])
        except (TypeError, ValueError):
            return jsonify({"error": "invalid_since"}), 400

    # limit (default 100, silently clamped at 500)
    try:
        limit = int(args.get("limit", _DEFAULT_LIMIT))
    except (TypeError, ValueError):
        limit = _DEFAULT_LIMIT
    if limit <= 0:
        limit = _DEFAULT_LIMIT
    if limit > _MAX_LIMIT:
        limit = _MAX_LIMIT

    # Build the query. JOIN to grow_units so each row carries unit_label.
    where = []
    params = []
    if unresolved_only:
        where.append("e.resolved_at IS NULL")
    if unit_id is not None:
        where.append("e.unit_id = ?")
        params.append(unit_id)
    if severity is not None:
        where.append("e.severity = ?")
        params.append(severity)
    if kind is not None:
        where.append("e.kind = ?")
        params.append(kind)
    if since_dt is not None:
        where.append("e.timestamp_utc >= ?")
        params.append(since_dt)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = (
        "SELECT e.id, e.unit_id, u.label AS unit_label, e.timestamp_utc, "
        "       e.severity, e.kind, e.message, e.subject_sensor, "
        "       e.details_json, e.resolved_at, e.snoozed_until "
        "FROM grow_errors e LEFT JOIN grow_units u ON u.id = e.unit_id "
        f"{where_sql} ORDER BY e.timestamp_utc DESC LIMIT ?"
    )
    params.append(limit)

    conn = sqlite3.connect(DB_FILE, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    return jsonify([
        {
            "id": r["id"],
            "unit_id": r["unit_id"],
            "unit_label": r["unit_label"],
            "timestamp_utc": r["timestamp_utc"],
            "severity": r["severity"],
            "kind": r["kind"],
            "message": r["message"],
            "subject_sensor": r["subject_sensor"],
            "details_json": r["details_json"],
            "resolved_at": r["resolved_at"],
            "snoozed_until": r["snoozed_until"],
        }
        for r in rows
    ])


@api_grow_errors_bp.route("/api/grow/errors/<int:error_id>", methods=["PATCH"])
@require_role("admin")
def patch_error(error_id):
    """Set/clear resolved_at and/or snoozed_until on a single grow_errors row.

    Body shape:
      {"resolved_at": "now"} | {"resolved_at": "<iso>"} | {"resolved_at": null}
      {"snoozed_until": "<iso>"} | {"snoozed_until": null}
      Combined: {"resolved_at": "now", "snoozed_until": "<iso>"}

    "now" sentinel resolves to a server-side UTC timestamp so the client
    doesn't have to know what time the server thinks it is. ISO8601 is
    accepted otherwise so e.g. an admin can backdate a resolution.
    Empty body / no recognised fields → 400 (don't silently no-op; the
    client expects to know its PATCH was meaningful).
    """
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not body:
        return jsonify({"error": "empty_body"}), 400

    set_clauses = []
    values = []

    # resolved_at: "now" | iso8601 | null
    if "resolved_at" in body:
        v = body["resolved_at"]
        if v is None:
            set_clauses.append("resolved_at = NULL")
        elif isinstance(v, str):
            if v == "now":
                set_clauses.append("resolved_at = ?")
                values.append(datetime.utcnow())
            else:
                try:
                    set_clauses.append("resolved_at = ?")
                    values.append(_parse_iso8601(v))
                except (TypeError, ValueError):
                    return jsonify({"error": "invalid_resolved_at"}), 400
        else:
            return jsonify({"error": "invalid_resolved_at"}), 400

    # snoozed_until: iso8601 | null
    if "snoozed_until" in body:
        v = body["snoozed_until"]
        if v is None:
            set_clauses.append("snoozed_until = NULL")
        elif isinstance(v, str):
            try:
                set_clauses.append("snoozed_until = ?")
                values.append(_parse_iso8601(v))
            except (TypeError, ValueError):
                return jsonify({"error": "invalid_snoozed_until"}), 400
        else:
            return jsonify({"error": "invalid_snoozed_until"}), 400

    if not set_clauses:
        return jsonify({"error": "empty_body"}), 400

    sql = "UPDATE grow_errors SET " + ", ".join(set_clauses) + " WHERE id=?"
    values.append(error_id)

    conn = sqlite3.connect(DB_FILE, timeout=10)
    try:
        cur = conn.execute(sql, values)
        if cur.rowcount == 0:
            return jsonify({"error": "error_not_found"}), 404
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True})
