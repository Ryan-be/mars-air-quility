"""Incidents REST API.

GET /api/incidents            — paginated list with optional filters
GET /api/incidents/<id>       — full incident detail with narrative + similar
GET /api/incidents/<id>/alert/<alert_id>  — raw inference JSON
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
    is_cross_incident,
)

log = logging.getLogger(__name__)
api_incidents_bp = Blueprint("api_incidents", __name__)

DB_FILE = config.get("DB_FILE", "data/sensor_data.db")

_SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}
_WINDOW_MAP = {
    "1h": 1, "6h": 6, "24h": 24, "7d": 168, "30d": 720,
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_conn():
    import sqlite3
    conn = sqlite3.connect(DB_FILE, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def _parse_window(window: str) -> datetime | None:
    hours = _WINDOW_MAP.get(window)
    if hours is None:
        return None
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)


def _incident_alert_count(conn, incident_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM incident_alerts WHERE incident_id = ?",
        (incident_id,)
    ).fetchone()
    return row[0] if row else 0


def _build_narrative(incident: dict, alerts: list[dict]) -> dict:
    """Template-based narrative — no LLM. Returns {observed, inferred, impact}."""
    severities = [a.get("severity", "info") for a in alerts]
    max_sev = max(severities, key=lambda s: _SEVERITY_ORDER.get(s, 0), default="info")
    unique_types = list({a["event_type"] for a in alerts})

    observed = (
        f"{len(alerts)} event(s) detected between "
        f"{str(incident.get('started_at', ''))[:16]} and {str(incident.get('ended_at', ''))[:16]}."
    )
    inferred = f"Dominant detection type(s): {', '.join(unique_types[:3])}."
    impact_map = {
        "critical": "Immediate attention required — critical air quality event.",
        "warning": "Elevated readings detected — monitor conditions closely.",
        "info": "Informational event — conditions within acceptable range.",
    }
    return {
        "observed": observed,
        "inferred": inferred,
        "impact": impact_map.get(max_sev, ""),
    }


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
    incidents = []
    for row in rows:
        d = dict(row)
        d["alert_count"] = _incident_alert_count(conn, d["id"])
        d.pop("signature", None)  # don't expose raw vector over the wire
        if q and q not in d.get("title", "").lower() and q not in d["id"].lower():
            continue
        incidents.append(d)

    conn.close()
    return jsonify({"incidents": incidents, "total": len(incidents)})


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

    narrative = _build_narrative(incident, alerts)
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


@api_incidents_bp.route("/api/incidents/<incident_id>/alert/<int:alert_id>")
def get_incident_alert(incident_id: str, alert_id: int):
    """Return full inference row JSON for a given alert within an incident."""
    conn = _get_conn()

    link = conn.execute(
        "SELECT 1 FROM incident_alerts WHERE incident_id = ? AND alert_id = ?",
        (incident_id, alert_id)
    ).fetchone()
    if link is None:
        conn.close()
        return jsonify({"error": "Alert not found in incident"}), 404

    row = conn.execute(
        "SELECT * FROM inferences WHERE id = ?", (alert_id,)
    ).fetchone()
    conn.close()

    if row is None:
        return jsonify({"error": "Inference not found"}), 404

    alert = dict(row)
    try:
        alert["evidence"] = json.loads(alert.get("evidence") or "{}")
    except Exception:  # pylint: disable=broad-except
        pass
    alert["detection_method"] = detection_method(alert["event_type"])
    return jsonify(alert)
