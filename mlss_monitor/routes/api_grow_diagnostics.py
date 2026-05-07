"""GET /api/grow/units/<id>/diagnostics — consolidated payload for Diagnostics tab.

Single fetch surfaces everything the upcoming Diagnostics tab needs so the
frontend doesn't have to chain four round-trips on every panel open:

  * firmware_version / uptime_s / buffer_size — from grow_units (Phase 3
    Task 1 added these columns; the WS handler keeps them current)
  * connection_log    — last 20 online/offline rows from grow_errors so
    the operator can see the recent connect/disconnect cadence
  * sensor_sanity     — per-capability staleness derived from
    grow_unit_capabilities.last_seen_at vs the configurable
    app_settings.grow_sensor_stale_threshold_min (default 5 minutes)
  * open_errors       — unresolved grow_errors EXCLUDING the meta-event
    online/offline rows (those live in connection_log; mixing them here
    would double-render every disconnect as both "open error" and
    "connection event")

Read-only — viewer-readable. The Diagnostics tab is observability, not
write surface.
"""
import sqlite3
from datetime import datetime
from flask import Blueprint, jsonify

from database.init_db import DB_FILE
from mlss_monitor.rbac import require_role

api_grow_diagnostics_bp = Blueprint("api_grow_diagnostics", __name__)

_CONNECTION_LOG_LIMIT = 20
_DEFAULT_STALE_THRESHOLD_MIN = 5


def _get_stale_threshold(conn) -> float:
    """Read the sensor-stale threshold (minutes) from app_settings.

    Falls back to ``_DEFAULT_STALE_THRESHOLD_MIN`` when the row is missing
    or the stored string can't be parsed as a float — a malformed setting
    must not break the diagnostics endpoint, ops can fix the value later.
    """
    row = conn.execute(
        "SELECT value FROM app_settings "
        "WHERE key='grow_sensor_stale_threshold_min'"
    ).fetchone()
    if row is None:
        return _DEFAULT_STALE_THRESHOLD_MIN
    try:
        return float(row[0])
    except (TypeError, ValueError):
        return _DEFAULT_STALE_THRESHOLD_MIN


@api_grow_diagnostics_bp.route(
    "/api/grow/units/<int:unit_id>/diagnostics", methods=["GET"]
)
@require_role("viewer", "controller", "admin")
def get_diagnostics(unit_id):
    conn = sqlite3.connect(DB_FILE, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        unit_row = conn.execute(
            "SELECT firmware_version, last_uptime_s, last_buffer_size "
            "FROM grow_units WHERE id=?",
            (unit_id,),
        ).fetchone()
        if unit_row is None:
            return jsonify({"error": "unit_not_found"}), 404

        threshold = _get_stale_threshold(conn)
        now = datetime.utcnow()

        # Connection log — last 20 online/offline rows, newest first.
        # Ordered by id (autoincrement) DESC rather than timestamp_utc so
        # rows inserted within the same microsecond keep deterministic
        # order for the UI.
        conn_rows = conn.execute(
            "SELECT id, timestamp_utc, kind, resolved_at FROM grow_errors "
            "WHERE unit_id=? AND kind IN ('online', 'offline') "
            "ORDER BY id DESC LIMIT ?",
            (unit_id, _CONNECTION_LOG_LIMIT),
        ).fetchall()
        connection_log = [
            {
                "id": r["id"],
                "timestamp_utc": r["timestamp_utc"],
                "kind": r["kind"],
                "resolved_at": r["resolved_at"],
            }
            for r in conn_rows
        ]

        # Sensor sanity — staleness derived from last_seen_at vs threshold.
        # NULL last_seen_at means we've never seen this sensor → is_stale=True
        # (the operator deserves to know it's never reported, not silently
        # treated as fresh).
        cap_rows = conn.execute(
            "SELECT channel, last_seen_at FROM grow_unit_capabilities "
            "WHERE unit_id=? ORDER BY channel",
            (unit_id,),
        ).fetchall()
        sensor_sanity = []
        for r in cap_rows:
            last_seen = r["last_seen_at"]
            minutes_ago = None
            is_stale = True
            if last_seen is not None:
                if isinstance(last_seen, str):
                    last_seen_dt = datetime.fromisoformat(last_seen)
                else:
                    last_seen_dt = last_seen
                minutes_ago = (now - last_seen_dt).total_seconds() / 60
                is_stale = minutes_ago > threshold
            sensor_sanity.append({
                "channel": r["channel"],
                "last_seen_at": last_seen,
                "minutes_ago": minutes_ago,
                "is_stale": is_stale,
                "stale_threshold_min": threshold,
            })

        # Open errors — exclude online/offline meta-events (those go in
        # connection_log; mixing them here would double-render every
        # disconnect). Newest first so the UI can show the most recent
        # issue at the top of the panel.
        err_rows = conn.execute(
            "SELECT id, timestamp_utc, severity, kind, message, subject_sensor "
            "FROM grow_errors WHERE unit_id=? AND resolved_at IS NULL "
            "AND kind NOT IN ('online', 'offline') "
            "ORDER BY timestamp_utc DESC",
            (unit_id,),
        ).fetchall()
        open_errors = [
            {
                "id": r["id"],
                "timestamp_utc": r["timestamp_utc"],
                "severity": r["severity"],
                "kind": r["kind"],
                "message": r["message"],
                "subject_sensor": r["subject_sensor"],
            }
            for r in err_rows
        ]

        return jsonify({
            "firmware_version": unit_row["firmware_version"],
            "uptime_s": unit_row["last_uptime_s"],
            "buffer_size": unit_row["last_buffer_size"],
            "connection_log": connection_log,
            "sensor_sanity": sensor_sanity,
            "open_errors": open_errors,
        })
    finally:
        conn.close()
