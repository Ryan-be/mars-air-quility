"""Incidents REST API.

GET /api/incidents            — paginated list with optional filters
GET /api/incidents/<id>       — full incident detail with narrative + similar
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request

from config import config
from mlss_monitor.incident_grouper import (
    cosine_similarity,
    detection_method,
    explain_similarity,
    is_cross_incident,
)
from mlss_monitor.incidents_narrative import build_narrative

log = logging.getLogger(__name__)
api_incidents_bp = Blueprint("api_incidents", __name__)

DB_FILE = config.get("DB_FILE", "data/sensor_data.db")

_SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}
_WINDOW_MAP = {
    # Range keys mirror the history/dashboard segmented-buttons.
    # 7d / 30d kept for backwards-compat with any external callers that
    # might still pass them; the UI only exposes the six below.
    "15m": 0.25,
    "1h": 1,
    "6h": 6,
    "12h": 12,
    "24h": 24,
    "14d": 336,
    "7d": 168,
    "30d": 720,
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_conn():
    import sqlite3
    conn = sqlite3.connect(DB_FILE, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def _parse_window(window: str) -> datetime | None:
    """Return the earliest datetime included by ``window``, or ``None`` if the
    key is not a known window. The caller decides whether ``None`` means
    "reject with 400" or "no time filter" — see ``list_incidents``.
    """
    hours = _WINDOW_MAP.get(window)
    if hours is None:
        return None
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)


def _alert_counts_by_incident(conn, incident_ids: list[str]) -> dict[str, int]:
    """Single GROUP BY query returning ``{incident_id: alert_count}``.
    Replaces the previous N-query loop (one SELECT COUNT per incident).
    """
    if not incident_ids:
        return {}
    placeholders = ",".join("?" * len(incident_ids))
    rows = conn.execute(
        f"SELECT incident_id, COUNT(*) AS n FROM incident_alerts "
        f"WHERE incident_id IN ({placeholders}) GROUP BY incident_id",
        incident_ids,
    ).fetchall()
    return {r["incident_id"]: r["n"] for r in rows}


def _find_similar(
    conn,
    incident_id: str,
    signature: list[float],
    top_n: int = 3,
) -> list[dict]:
    """Find similar past incidents using cosine similarity on signature vectors."""
    rows = conn.execute(
        "SELECT id, title, started_at, max_severity, confidence, signature "
        "FROM incidents WHERE id != ? ORDER BY started_at DESC LIMIT 100",
        (incident_id,)
    ).fetchall()

    scored = []
    for row in rows:
        try:
            other_sig = json.loads(row["signature"])
            score = cosine_similarity(signature, other_sig)
            if score >= 0.5:
                scored.append({
                    "id": row["id"],
                    "title": row["title"],
                    "started_at": row["started_at"],
                    "max_severity": row["max_severity"],
                    "confidence": row["confidence"],
                    "similarity": round(score, 3),
                    "why": explain_similarity(signature, other_sig),
                })
        except Exception:  # pylint: disable=broad-except
            continue

    scored.sort(key=lambda x: -x["similarity"])
    return scored[:top_n]


# ── Routes ────────────────────────────────────────────────────────────────────

@api_incidents_bp.route("/api/incidents")
def list_incidents():
    window = request.args.get("window", "24h")
    severity = request.args.get("severity", "all")
    q = request.args.get("q", "").strip().lower()
    limit = request.args.get("limit", 50, type=int)

    # Reject unknown windows with 400 rather than silently returning all rows.
    # Caller may pass any key that's in _WINDOW_MAP (incl. back-compat 7d/30d).
    if window not in _WINDOW_MAP:
        return jsonify({
            "error": f"Unknown window: {window!r}. "
                     f"Valid: {', '.join(sorted(_WINDOW_MAP.keys()))}"
        }), 400

    conn = _get_conn()
    since = _parse_window(window)

    conditions: list[str] = []
    params: list = []

    if since:
        conditions.append("started_at >= ?")
        params.append(since.isoformat(sep=" "))
    if severity and severity != "all":
        conditions.append("max_severity = ?")
        params.append(severity)

    query = "SELECT * FROM incidents"
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY started_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    # Apply the free-text filter first, THEN fetch alert counts so we don't
    # pay the GROUP BY cost for incidents we're about to drop.
    incidents: list[dict] = []
    for row in rows:
        d = dict(row)
        d.pop("signature", None)  # don't expose raw vector over the wire
        if q and q not in d.get("title", "").lower() and q not in d["id"].lower():
            continue
        incidents.append(d)

    # Single grouped query for alert counts — replaces per-incident SELECT.
    count_by_id = _alert_counts_by_incident(conn, [i["id"] for i in incidents])
    for inc in incidents:
        inc["alert_count"] = count_by_id.get(inc["id"], 0)

    counts = {"critical": 0, "warning": 0, "info": 0}
    for inc in incidents:
        sev = inc.get("max_severity", "info")
        if sev in counts:
            counts[sev] += 1

    # Top 3 sensors + 24-bucket hour-of-day histogram across this window.
    inc_ids = [i["id"] for i in incidents]
    top_sensors: list[dict] = []
    hour_histogram: list[int] = [0] * 24

    if inc_ids:
        placeholders = ",".join("?" * len(inc_ids))
        sensor_rows = conn.execute(
            f"SELECT d.sensor, COUNT(*) AS n FROM alert_signal_deps d "
            f"JOIN incident_alerts ia ON ia.alert_id = d.alert_id "
            f"WHERE ia.incident_id IN ({placeholders}) "
            f"GROUP BY d.sensor ORDER BY n DESC LIMIT 3",
            inc_ids,
        ).fetchall()
        top_sensors = [{"sensor": r["sensor"], "n": r["n"]} for r in sensor_rows]

        # NOTE: started_at is stored in UTC (datetime.now(timezone.utc) at
        # insert time) so the extracted hour here is the UTC hour. The UI
        # label on the histogram says "(UTC)" explicitly so operators know
        # not to read it as local time.
        for inc in incidents:
            started = inc.get("started_at", "")
            if len(started) >= 13:
                try:
                    hour = int(started[11:13])
                    hour_histogram[hour] += 1
                except ValueError:
                    pass

    conn.close()
    return jsonify({
        "incidents": incidents,
        "total": len(incidents),
        "counts": counts,
        "summary": {
            "top_sensors": top_sensors,
            "hour_histogram": hour_histogram,
        },
    })


@api_incidents_bp.route("/api/incidents/<incident_id>")
def get_incident(incident_id: str):
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM incidents WHERE id = ?", (incident_id,)
    ).fetchone()
    if row is None:
        conn.close()
        return jsonify({"error": "Incident not found"}), 404

    incident = dict(row)
    try:
        signature = json.loads(incident.get("signature", "[]"))
    except Exception:  # pylint: disable=broad-except
        signature = []

    # Load alerts with signal deps
    alert_rows = conn.execute(
        "SELECT i.id, i.created_at, i.event_type, i.severity, i.title, "
        "i.description, i.confidence, ia.is_primary "
        "FROM inferences i "
        "JOIN incident_alerts ia ON ia.alert_id = i.id "
        "WHERE ia.incident_id = ? ORDER BY i.created_at",
        (incident_id,)
    ).fetchall()

    alerts = []
    for ar in alert_rows:
        a = dict(ar)
        a["detection_method"] = detection_method(a["event_type"])
        a["is_cross_incident"] = bool(is_cross_incident(a["event_type"]))

        dep_rows = conn.execute(
            "SELECT sensor, r, lag_seconds FROM alert_signal_deps WHERE alert_id = ?",
            (a["id"],)
        ).fetchall()
        a["signal_deps"] = [dict(d) for d in dep_rows]
        alerts.append(a)

    causal_sequence = [
        {
            "id": a["id"],
            "title": a.get("title", ""),
            "event_type": a["event_type"],
            "severity": a["severity"],
            "created_at": a["created_at"],
        }
        for a in alerts if a["is_primary"]
    ]

    narrative = build_narrative(incident, alerts)
    similar = _find_similar(conn, incident_id, signature)

    incident.pop("signature", None)
    conn.close()

    return jsonify({
        **incident,
        "alerts": alerts,
        "causal_sequence": causal_sequence,
        "narrative": narrative,
        "similar": similar,
    })
