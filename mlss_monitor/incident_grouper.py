"""Incident grouper — background thread that sessionises inferences into
incidents and persists them to SQLite.

Pure logic functions (sessionise, detection_method, make_incident_id) are
separated at the top so they can be unit-tested without a DB connection.
"""
from __future__ import annotations

import json
import logging
import queue
import sqlite3
import threading
from datetime import datetime
from statistics import correlation
from typing import Any

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

GAP_MINUTES = 30  # silence gap that starts a new incident
MIN_DATA_POINTS = 10  # minimum overlapping points for Pearson r

CROSS_INCIDENT_TYPES: frozenset[str] = frozenset({
    "hourly_summary",
    "daily_summary",
    "daily_pattern",
})
# event_types starting with this prefix are also cross-incident
_ANNOTATION_CONTEXT_PREFIX = "annotation_context_"

_ML_PREFIXES = ("anomaly_", "ml_learned_")
_STATISTICAL_TYPES = frozenset({"correlated_pollution", "sustained_poor_air"})
_SUMMARY_TYPES = frozenset({"hourly_summary", "daily_summary", "daily_pattern"})

# Sensor columns in hot_tier (10 channels)
_SENSOR_COLS: list[str] = [
    "tvoc_ppb", "eco2_ppm", "temperature_c", "humidity_pct",
    "pm1_ug_m3", "pm25_ug_m3", "pm10_ug_m3",
    "co_ppb", "no2_ppb", "nh3_ppb",
]

_SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}
_SEVERITY_LABEL = {"info": "Info", "warning": "Warning", "critical": "Critical"}

# Detection method one-hot order (indices 20-24)
_METHOD_ORDER = ["threshold", "ml", "fingerprint", "summary", "statistical"]

_SENSOR_KEYWORDS: dict[int, tuple[str, ...]] = {
    10: ("tvoc",),
    11: ("eco2", "co2"),
    12: ("temp",),
    13: ("humid", "hum"),
    14: ("pm1",),
    15: ("pm25", "pm2"),
    16: ("pm10",),
    17: ("co_",),
    18: ("no2",),
    19: ("nh3",),
}


# ── Pure logic ─────────────────────────────────────────────────────────────────

def detection_method(event_type: str) -> str:
    """Map an inferences.event_type to one of: ml | fingerprint | summary |
    statistical | threshold."""
    if event_type == "fingerprint_match":
        return "fingerprint"
    if any(event_type.startswith(p) for p in _ML_PREFIXES):
        return "ml"
    if event_type in _SUMMARY_TYPES or event_type.startswith(_ANNOTATION_CONTEXT_PREFIX):
        return "summary"
    if event_type in _STATISTICAL_TYPES:
        return "statistical"
    return "threshold"


def is_cross_incident(event_type: str) -> bool:
    """Return True for alert types that span / summarise multiple incidents."""
    return (event_type in CROSS_INCIDENT_TYPES
            or event_type.startswith(_ANNOTATION_CONTEXT_PREFIX))


def make_incident_id(ts: datetime) -> str:
    """Deterministic incident ID from the earliest alert timestamp."""
    return f"INC-{ts.strftime('%Y%m%d-%H%M')}"


def sessionise(
    alerts: list[dict[str, Any]],
    gap_minutes: int = GAP_MINUTES,
) -> list[list[dict[str, Any]]]:
    """Group alerts into sessions separated by a silence gap.

    IMPORTANT: Uses ``.total_seconds()``, not ``.seconds``.
    ``.seconds`` only returns the seconds component (0-59), so a 60-minute
    gap would appear as 0 seconds and be incorrectly merged.
    """
    if not alerts:
        return []

    sorted_alerts = sorted(
        alerts,
        key=lambda a: datetime.fromisoformat(a["created_at"]),
    )

    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = [sorted_alerts[0]]

    for alert in sorted_alerts[1:]:
        prev_ts = datetime.fromisoformat(current[-1]["created_at"])
        curr_ts = datetime.fromisoformat(alert["created_at"])
        gap_secs = (curr_ts - prev_ts).total_seconds()  # NOT .seconds
        if gap_secs > gap_minutes * 60:
            groups.append(current)
            current = []
        current.append(alert)

    groups.append(current)
    return groups


def compute_pearson_r(
    xs: list[float | None],
    ys: list[float | None],
) -> float | None:
    """Pearson r between two series, or None if < MIN_DATA_POINTS clean pairs.

    Uses stdlib ``statistics.correlation`` (Python 3.11+).
    Invariant: returns None for missing data — never 0.0.
    """
    clean = [
        (x, y) for x, y in zip(xs, ys)
        if x is not None and y is not None
    ]
    if len(clean) < MIN_DATA_POINTS:
        return None
    x_vals, y_vals = zip(*clean)
    try:
        return correlation(list(x_vals), list(y_vals))
    except Exception:  # pylint: disable=broad-except
        return None


def build_incident_similarity_vector(alerts: list[dict[str, Any]]) -> list[float]:
    """Build a 32-float incident-level vector for cosine similarity search.

    Unlike the live FeatureVector (per-reading sensor physics used by the
    detection engine), this summarises a *completed incident* at a high level
    so that past incidents can be compared against each other.

    Vector layout:
      0-9   : peak delta placeholders (0.0)
      10-19 : sensor presence flags (1.0 if event_type implies that sensor)
      20-24 : detection method one-hot (threshold/ml/fingerprint/summary/statistical)
      26-28 : severity one-hot (info=26, warning=27, critical=28)
      29    : incident duration in minutes
      30    : mean confidence
      31    : time-of-day bucket (0=night 0-6h, 1=morning 6-12h, 2=afternoon 12-18h, 3=evening 18-24h)
    """
    vec = [0.0] * 32

    if not alerts:
        return vec

    sorted_a = sorted(alerts, key=lambda a: a["created_at"])
    t_start = datetime.fromisoformat(sorted_a[0]["created_at"])
    t_end = datetime.fromisoformat(sorted_a[-1]["created_at"])

    # 29: duration in minutes
    vec[29] = float((t_end - t_start).total_seconds() / 60.0)

    # 30: mean confidence
    vec[30] = float(
        sum(a.get("confidence", 0.5) for a in alerts) / len(alerts)
    )

    # 31: time-of-day bucket based on start hour
    hour = t_start.hour
    if hour < 6:
        vec[31] = 0.0
    elif hour < 12:
        vec[31] = 1.0
    elif hour < 18:
        vec[31] = 2.0
    else:
        vec[31] = 3.0

    # 20-24: detection method one-hot (majority vote)
    method_counts: dict[str, int] = {}
    for a in alerts:
        m = detection_method(a.get("event_type", ""))
        method_counts[m] = method_counts.get(m, 0) + 1
    dominant = max(method_counts, key=method_counts.get)
    idx = _METHOD_ORDER.index(dominant) if dominant in _METHOD_ORDER else 0
    vec[20 + idx] = 1.0

    # 26-28: severity one-hot (info → index 26, warning → 27, critical → 28)
    sevs = [a.get("severity", "info") for a in alerts]
    if "critical" in sevs:
        vec[28] = 1.0
    elif "warning" in sevs:
        vec[27] = 1.0
    else:
        vec[26] = 1.0

    # 10-19: sensor presence flags (naive heuristic from event_type keywords)
    for a in alerts:
        et = a.get("event_type", "").lower()
        for vec_idx, keywords in _SENSOR_KEYWORDS.items():
            if any(kw in et for kw in keywords):
                vec[vec_idx] = 1.0

    return vec


def generate_incident_title(alerts: list[dict[str, Any]]) -> str:
    """Generate a human-readable incident title from the alert group."""
    if not alerts:
        return "Unknown Incident"

    max_sev = max(
        (a.get("severity", "info") for a in alerts),
        key=lambda s: _SEVERITY_ORDER.get(s, 0),
    )
    sev_label = _SEVERITY_LABEL.get(max_sev, "Info")

    top = sorted(
        alerts,
        key=lambda a: (
            -_SEVERITY_ORDER.get(a.get("severity", "info"), 0),
            a["created_at"],
        ),
    )[0]

    return f"{sev_label}: {top.get('title', 'Unknown event')}"


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length float vectors.

    Returns 0.0 if vectors have different lengths or are zero-length.
    """
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


# ── DB persistence ─────────────────────────────────────────────────────────────

def _load_all_inferences(db_file: str) -> list[dict[str, Any]]:
    """Load all non-dismissed inferences from SQLite."""
    conn = sqlite3.connect(db_file, timeout=15)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, created_at, event_type, severity, title, description, "
        "confidence FROM inferences WHERE dismissed = 0 ORDER BY created_at"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _fetch_hot_tier_column(
    db_file: str,
    t_start: datetime,
    t_end: datetime,
    col: str,
) -> list[float | None]:
    """Fetch a single hot_tier column within [t_start, t_end]."""
    conn = sqlite3.connect(db_file, timeout=15)
    rows = conn.execute(
        f"SELECT {col} FROM hot_tier "  # noqa: S608 — col is validated against _SENSOR_COLS
        "WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
        (t_start.isoformat(sep=" "), t_end.isoformat(sep=" ")),
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def _upsert_incident(
    cur: sqlite3.Cursor,
    incident_id: str,
    alerts: list[dict[str, Any]],
    db_file: str,
) -> None:
    """Write/update one incident and its related rows."""
    sorted_a = sorted(alerts, key=lambda a: a["created_at"])
    t_start = datetime.fromisoformat(sorted_a[0]["created_at"])
    t_end = datetime.fromisoformat(sorted_a[-1]["created_at"])

    max_sev = max(
        (a.get("severity", "info") for a in alerts),
        key=lambda s: _SEVERITY_ORDER.get(s, 0),
    )
    mean_conf = sum(a.get("confidence", 0.5) for a in alerts) / len(alerts)
    title = generate_incident_title(alerts)
    signature = json.dumps(build_incident_similarity_vector(alerts))

    cur.execute(
        "INSERT OR REPLACE INTO incidents "
        "(id, started_at, ended_at, max_severity, confidence, title, signature) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (incident_id, t_start.isoformat(sep=" "), t_end.isoformat(sep=" "),
         max_sev, mean_conf, title, signature),
    )

    # Rebuild incident_alerts for this incident
    cur.execute("DELETE FROM incident_alerts WHERE incident_id = ?", (incident_id,))
    for alert in alerts:
        primary = 0 if is_cross_incident(alert.get("event_type", "")) else 1
        cur.execute(
            "INSERT OR IGNORE INTO incident_alerts (incident_id, alert_id, is_primary) "
            "VALUES (?, ?, ?)",
            (incident_id, alert["id"], primary),
        )

    # Rebuild alert_signal_deps for primary alerts only
    primary_alerts = [a for a in alerts if not is_cross_incident(a.get("event_type", ""))]
    for alert in primary_alerts:
        cur.execute("DELETE FROM alert_signal_deps WHERE alert_id = ?", (alert["id"],))
        time_index = list(range(len(sorted_a)))  # proxy: ordinal position as "time"
        for col in _SENSOR_COLS:
            sensor_vals = _fetch_hot_tier_column(db_file, t_start, t_end, col)
            # Correlate sensor column against ordinal time index as a simple proxy
            r = compute_pearson_r(sensor_vals, time_index[:len(sensor_vals)])
            # r is None when < MIN_DATA_POINTS — stored as NULL, never as 0.0
            cur.execute(
                "INSERT OR IGNORE INTO alert_signal_deps "
                "(alert_id, sensor, r, lag_seconds) VALUES (?, ?, ?, ?)",
                (alert["id"], col, r, 0),
            )


def regroup_all(db_file: str) -> None:
    """Re-sessionise all inferences and upsert incidents into the DB.

    Idempotent: safe to call multiple times. Uses INSERT OR REPLACE so
    existing incidents are overwritten with fresh grouped data.
    """
    alerts = _load_all_inferences(db_file)
    groups = sessionise(alerts)

    conn = sqlite3.connect(db_file, timeout=15)
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")

    for group in groups:
        if not group:
            continue
        sorted_g = sorted(group, key=lambda a: a["created_at"])
        t_start = datetime.fromisoformat(sorted_g[0]["created_at"])
        incident_id = make_incident_id(t_start)
        _upsert_incident(cur, incident_id, group, db_file)

    conn.commit()
    conn.close()


# ── Background thread ──────────────────────────────────────────────────────────

_grouper_lock = threading.Lock()
_SAFETY_NET_INTERVAL = 60  # seconds — regroup even if no events arrive


def _safe_regroup(db_file: str) -> None:
    """Run regroup_all with a lock and swallow all exceptions."""
    with _grouper_lock:
        try:
            regroup_all(db_file)
            log.debug("Incident regroup complete")
        except Exception:  # pylint: disable=broad-except
            log.exception("Incident regroup failed")


class IncidentGrouper:
    """Manages the background grouper thread lifecycle.

    Subscribes to the EventBus and regrouping on every ``new_inference``
    event.  Also runs a 60-second safety-net regroup in case events are
    missed.  The thread is a daemon so it does not prevent app shutdown.
    """

    def __init__(self, db_file: str, event_bus=None):
        self.db_file = db_file
        self._event_bus = event_bus
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._sub_queue: queue.Queue | None = None

    def start(self) -> None:
        """Start the daemon grouper thread. Safe to call multiple times."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="incident-grouper",
            daemon=True,
        )
        self._thread.start()
        log.info("IncidentGrouper started")

    def stop(self) -> None:
        """Signal the thread to exit (used in tests / graceful shutdown)."""
        self._stop.set()

    def _loop(self) -> None:
        if self._event_bus is not None:
            self._sub_queue = self._event_bus.subscribe()

        # Initial regroup on startup
        _safe_regroup(self.db_file)

        while not self._stop.is_set():
            if self._sub_queue is not None:
                try:
                    msg = self._sub_queue.get(timeout=_SAFETY_NET_INTERVAL)
                    if msg.get("event") == "new_inference":
                        _safe_regroup(self.db_file)
                except queue.Empty:
                    # Safety net: regroup even if no events arrived
                    _safe_regroup(self.db_file)
            else:
                # No event bus — just run on the safety-net interval
                self._stop.wait(_SAFETY_NET_INTERVAL)
                if not self._stop.is_set():
                    _safe_regroup(self.db_file)

        if self._sub_queue is not None and self._event_bus is not None:
            self._event_bus.unsubscribe(self._sub_queue)
        log.info("IncidentGrouper stopped")


def start_grouper(db_file: str, event_bus=None) -> IncidentGrouper:
    """Create, start, and return an IncidentGrouper.

    Called once at app startup. The returned instance should be stored
    in ``mlss_monitor.state.incident_grouper``.
    """
    grouper = IncidentGrouper(db_file=db_file, event_bus=event_bus)
    grouper.start()
    return grouper
