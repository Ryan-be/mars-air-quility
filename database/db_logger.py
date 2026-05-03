import json
import sqlite3
import statistics
from datetime import datetime, timedelta, timezone

from config import config


class _SafeJSONEncoder(json.JSONEncoder):
    def default(self, o):  # pylint: disable=arguments-renamed
        if isinstance(o, datetime):
            return o.isoformat()
        return super().default(o)

DB_FILE = config.get("DB_FILE", "data/sensor_data.db")


def _normalise_ts(ts: str | None) -> str | None:
    """Convert 'YYYY-MM-DD HH:MM:SS' or 'YYYY-MM-DDTHH:MM:SS.ffffff'
    → 'YYYY-MM-DDTHH:MM:SS[.ffffff]Z' (UTC ISO 8601 with Z suffix).
    No-ops if ts is already normalised or is None.
    """
    if ts is None:
        return None
    if ts.endswith("Z"):
        return ts
    return ts.replace(" ", "T") + "Z"


def _deep_to_str(obj):
    """Recursively convert datetime objects in a nested structure to ISO strings."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _deep_to_str(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_deep_to_str(item) for item in obj]
    return obj


# ---------------------------------------------------------------------------
# Detection method classification
# ---------------------------------------------------------------------------

_ML_EVENT_TYPES = frozenset({
    "anomaly_combustion_signature",
    "anomaly_particle_distribution",
    "anomaly_ventilation_quality",
    "anomaly_gas_relationship",
    "anomaly_thermal_moisture",
})

_STATISTICAL_SUFFIXES = frozenset({
    "tvoc", "eco2", "temperature", "humidity",
    "pm25", "pm1", "pm10", "co", "no2", "nh3",
})


def compute_detection_method(event_type: str) -> str:
    """Classify an inference event_type as 'ml', 'statistical', or 'rule'.

    'ml'          — multivariate composite River model
    'statistical' — per-channel River anomaly detector
    'rule'        — deterministic YAML threshold rule (default)
    """
    if event_type in _ML_EVENT_TYPES or event_type.startswith("ml_learned_"):
        return "ml"
    if event_type.startswith("anomaly_"):
        suffix = event_type[len("anomaly_"):]
        if suffix in _STATISTICAL_SUFFIXES:
            return "statistical"
    return "rule"


def _connect():
    """Open a SQLite connection with WAL mode and a 10-second busy timeout."""
    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=8000")
    return conn


def log_sensor_data(temp, hum, eco2, tvoc, annotation=None, fan_power_w=None, vpd_kpa=None,
                    pm1_0=None, pm2_5=None, pm10=None,
                    gas_co=None, gas_no2=None, gas_nh3=None):
    """
    Log sensor data into the SQLite database.

    :param temp: temperature in °C
    :param hum: relative humidity in %
    :param eco2: equivalent CO₂ in ppm
    :param tvoc: total VOC in ppb
    :param annotation: optional text annotation
    :param fan_power_w: current fan power consumption in watts (None if unavailable)
    :param vpd_kpa: vapour pressure deficit in kPa (None falls back to NULL in DB)
    :param pm1_0: PM1.0 in ug/m3 (None if unavailable)
    :param pm2_5: PM2.5 in ug/m3 (None if unavailable)
    :param pm10: PM10 in ug/m3 (None if unavailable)
    :param gas_co: CO reading from MICS6814 (None if unavailable)
    :param gas_no2: NO2 reading from MICS6814 (None if unavailable)
    :param gas_nh3: NH3 reading from MICS6814 (None if unavailable)
    """
    conn = _connect()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO sensor_data
            (timestamp, temperature, humidity, eco2, tvoc, annotation, fan_power_w, vpd_kpa,
             pm1_0, pm2_5, pm10, gas_co, gas_no2, gas_nh3)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (_normalise_ts(datetime.utcnow().isoformat()), temp, hum, eco2, tvoc, annotation, fan_power_w, vpd_kpa,
          pm1_0, pm2_5, pm10, gas_co, gas_no2, gas_nh3))

    conn.commit()
    conn.close()


def get_sensor_data():
    """
    Fetch all sensor data from the database, ordered by timestamp in descending order.
    :return:
    """
    conn = _connect()
    cur = conn.cursor()

    cur.execute("SELECT * FROM sensor_data ORDER BY timestamp DESC")
    rows = cur.fetchall()

    conn.close()
    return rows


def get_sensor_data_by_date(start_date, end_date):
    """
    Fetch sensor data within a specific date range.
    :param start_date:
    :param end_date:
    :return:
    """
    conn = _connect()
    cur = conn.cursor()

    cur.execute("""
        SELECT * FROM sensor_data
        WHERE timestamp BETWEEN ? AND ?
        ORDER BY timestamp DESC
    """, (start_date, end_date))

    rows = cur.fetchall()
    conn.close()
    return rows


def add_annotation(sensor_id, annotation):
    """
    Add an annotation to a sensor data entry.
    :param sensor_id:
    :param annotation:
    :return:
    """
    conn = _connect()
    cur = conn.cursor()

    cur.execute("""
        UPDATE sensor_data
        SET annotation = ?
        WHERE id = ?
    """, (annotation, sensor_id))

    conn.commit()
    conn.close()


def remove_annotation(sensor_id):
    """
    Remove an annotation from a sensor data entry.
    :param sensor_id:
    :return:
    """
    conn = _connect()
    cur = conn.cursor()

    cur.execute("""
        UPDATE sensor_data
        SET annotation = NULL
        WHERE id = ?
    """, (sensor_id,))

    conn.commit()
    conn.close()


def edit_annotation(sensor_id, new_annotation):
    """
    Edit an existing annotation for a sensor data entry.
    :param sensor_id:
    :param new_annotation:
    :return:
    """
    conn = _connect()
    cur = conn.cursor()

    cur.execute("""
        UPDATE sensor_data
        SET annotation = ?
        WHERE id = ?
    """, (new_annotation, sensor_id))

    conn.commit()
    conn.close()


def get_fan_settings():
    conn = _connect()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM fan_settings ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    conn.close()
    if row is None:
        return {
            "tvoc_min": 0, "tvoc_max": 500,
            "temp_min": 0.0, "temp_max": 20.0,
            "enabled": False,
            "temp_enabled": True, "tvoc_enabled": True,
            "humidity_enabled": False, "humidity_max": 70.0,
        }
    d = dict(row)
    d["enabled"] = bool(d.get("enabled", 0))
    d.setdefault("temp_enabled", 1)
    d.setdefault("tvoc_enabled", 1)
    d.setdefault("humidity_enabled", 0)
    d.setdefault("humidity_max", 70.0)
    d.setdefault("pm25_enabled", 0)
    d.setdefault("pm25_max", 25.0)
    d.setdefault("pm_stale_minutes", 10.0)
    d["temp_enabled"]     = bool(d["temp_enabled"])
    d["tvoc_enabled"]     = bool(d["tvoc_enabled"])
    d["humidity_enabled"] = bool(d["humidity_enabled"])
    d["pm25_enabled"]     = bool(d["pm25_enabled"])
    return d


def log_weather(temp, humidity, feels_like, wind_speed, weather_code, uv_index):
    """Store one hourly weather snapshot."""
    conn = _connect()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO weather_log (timestamp, temp, humidity, feels_like, wind_speed, weather_code, uv_index)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (datetime.utcnow().isoformat(), temp, humidity, feels_like, wind_speed, weather_code, uv_index))
    conn.commit()
    conn.close()


def get_latest_weather(max_age_minutes: int = 90):
    """Return the most recent weather row if it is newer than max_age_minutes, else None."""
    conn = _connect()
    cur = conn.cursor()
    since = (datetime.utcnow() - timedelta(minutes=max_age_minutes)).isoformat()
    cur.execute("""
        SELECT temp, humidity, feels_like, wind_speed, weather_code, uv_index, timestamp
        FROM weather_log
        WHERE timestamp >= ?
        ORDER BY timestamp DESC
        LIMIT 1
    """, (since,))
    row = cur.fetchone()
    conn.close()
    if row is None:
        return None
    return {
        "temp": row[0], "humidity": row[1], "feels_like": row[2],
        "wind_speed": row[3], "weather_code": row[4], "uv_index": row[5],
        "fetched_at": row[6],
    }


def get_weather_history(since_iso: str) -> list:
    """Return weather_log rows newer than since_iso, oldest first."""
    conn = _connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT timestamp, temp, humidity, feels_like, wind_speed, weather_code, uv_index
        FROM weather_log
        WHERE timestamp >= ?
        ORDER BY timestamp ASC
    """, (since_iso,))
    rows = cur.fetchall()
    conn.close()
    return [
        {
            "timestamp": r[0], "temp": r[1], "humidity": r[2],
            "feels_like": r[3], "wind_speed": r[4],
            "weather_code": r[5], "uv_index": r[6],
        }
        for r in rows
    ]


def cleanup_old_weather(days: int = 7):
    """Delete weather rows older than `days` days."""
    conn = _connect()
    cur = conn.cursor()
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    cur.execute("DELETE FROM weather_log WHERE timestamp < ?", (cutoff,))
    conn.commit()
    conn.close()


def get_location():
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT key, value FROM app_settings WHERE key IN ('location_lat','location_lon','location_name')")
    rows = {r[0]: r[1] for r in cur.fetchall()}
    conn.close()
    lat = rows.get("location_lat")
    lon = rows.get("location_lon")
    return {
        "lat": float(lat) if lat else None,
        "lon": float(lon) if lon else None,
        "name": rows.get("location_name", ""),
    }


def save_location(lat, lon, name=""):
    conn = _connect()
    cur = conn.cursor()
    upsert = (
        "INSERT INTO app_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
    )
    for key, val in [("location_lat", str(lat)), ("location_lon", str(lon)), ("location_name", name)]:
        cur.execute(upsert, (key, val))
    conn.commit()
    conn.close()


def get_unit_rate() -> float | None:
    """Return the energy unit rate in p/kWh, or None if not set."""
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT value FROM app_settings WHERE key = 'energy_unit_rate_pence'")
    row = cur.fetchone()
    conn.close()
    if row is None:
        return None
    try:
        return float(row[0])
    except (TypeError, ValueError):
        return None


def save_unit_rate(rate_pence: float) -> None:
    """Upsert the energy unit rate (p/kWh) in app_settings."""
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO app_settings (key, value) VALUES ('energy_unit_rate_pence', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(rate_pence),),
    )
    conn.commit()
    conn.close()


def update_fan_settings(tvoc_min, tvoc_max, temp_min, temp_max, enabled,
                        temp_enabled=True, tvoc_enabled=True,
                        humidity_enabled=False, humidity_max=70.0,
                        pm25_enabled=False, pm25_max=25.0,
                        pm_stale_minutes=10.0):
    conn = _connect()
    cur = conn.cursor()
    cur.execute("""
        UPDATE fan_settings
        SET tvoc_min = ?, tvoc_max = ?, temp_min = ?, temp_max = ?, enabled = ?,
            temp_enabled = ?, tvoc_enabled = ?, humidity_enabled = ?, humidity_max = ?,
            pm25_enabled = ?, pm25_max = ?, pm_stale_minutes = ?
        WHERE id = (SELECT MAX(id) FROM fan_settings)
    """, (tvoc_min, tvoc_max, temp_min, temp_max, int(enabled),
          int(temp_enabled), int(tvoc_enabled), int(humidity_enabled), humidity_max,
          int(pm25_enabled), pm25_max, pm_stale_minutes))
    conn.commit()
    conn.close()


def set_fan_enabled(enabled: bool):
    """Toggle only the master auto-control flag (used by the controls page)."""
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "UPDATE fan_settings SET enabled = ? WHERE id = (SELECT MAX(id) FROM fan_settings)",
        (int(enabled),),
    )
    conn.commit()
    conn.close()


# ── Inference CRUD ────────────────────────────────────────────────────────────

def save_inference(event_type, severity, title, description, action,
                   evidence, confidence, start_id=None, end_id=None,
                   annotation=None):
    conn = _connect()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO inferences
            (created_at, event_type, severity, title, description, action,
             evidence, confidence, sensor_data_start_id, sensor_data_end_id,
             annotation)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.utcnow().isoformat(), event_type, severity, title,
        description, action, json.dumps(_deep_to_str(evidence), cls=_SafeJSONEncoder) if evidence else None,
        confidence, start_id, end_id, annotation,
    ))
    inf_id = cur.lastrowid
    conn.commit()
    conn.close()

    # Broadcast to SSE subscribers
    try:
        from mlss_monitor import state  # pylint: disable=import-outside-toplevel
        if state.event_bus:
            _ev = evidence if isinstance(evidence, dict) else json.loads(evidence or "{}")
            _pub_payload = {
                "id": inf_id,
                "created_at": _normalise_ts(datetime.utcnow().isoformat()),
                "title": title,
                "event_type": event_type,
                "severity": severity,
                "attribution_source": _ev.get("attribution_source"),
                "attribution_confidence": _ev.get("attribution_confidence"),
                "detection_method": compute_detection_method(event_type),
            }
            state.event_bus.publish("inference_fired", _pub_payload)
    except Exception:
        pass  # SSE failure must never break inference saving

    return inf_id


def get_inferences(limit=50, include_dismissed=False,
                   start: "str | None" = None, end: "str | None" = None,
                   parse_evidence: bool = True):
    """Return inferences ordered by created_at DESC.

    Optional ``start``/``end`` (ISO-8601 strings, space or T separator) constrain
    the query to rows within that window at the database level, avoiding the
    need to fetch-then-filter in Python.
    """
    conn = _connect()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    def _to_db(ts: str) -> str:
        # The inferences table stores created_at as ISO 8601 with a T separator
        # (e.g. "2024-01-15T10:30:45.123456"), so we must keep the T here.
        # Only strip the trailing Z suffix so the string comparison works correctly.
        return ts.rstrip("Z")

    if start and end:
        s_db = _to_db(start)
        e_db = _to_db(end)
        if include_dismissed:
            cur.execute(
                "SELECT * FROM inferences WHERE created_at >= ? AND created_at <= ? "
                "ORDER BY created_at DESC LIMIT ?",
                (s_db, e_db, limit),
            )
        else:
            cur.execute(
                "SELECT * FROM inferences WHERE dismissed = 0 "
                "AND created_at >= ? AND created_at <= ? "
                "ORDER BY created_at DESC LIMIT ?",
                (s_db, e_db, limit),
            )
    elif include_dismissed:
        cur.execute(
            "SELECT * FROM inferences ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
    else:
        cur.execute(
            "SELECT * FROM inferences WHERE dismissed = 0 "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    for r in rows:
        r["created_at"] = _normalise_ts(r.get("created_at"))
        if parse_evidence and r.get("evidence"):
            try:
                r["evidence"] = json.loads(r["evidence"])
            except (json.JSONDecodeError, TypeError):
                pass
        r["detection_method"] = compute_detection_method(r.get("event_type", ""))
    return rows


def get_inference_by_id(inference_id: int) -> dict | None:
    """Return a single inference dict by ID, or None if not found."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM inferences WHERE id = ?", (inference_id,)).fetchone()
    conn.close()
    if row is None:
        return None
    d = dict(row)
    d["created_at"] = _normalise_ts(d.get("created_at"))
    if d.get("evidence"):
        try:
            d["evidence"] = json.loads(d["evidence"])
        except (json.JSONDecodeError, TypeError):
            pass
    d["detection_method"] = compute_detection_method(d.get("event_type", ""))
    return d


def get_distinct_attribution_sources() -> set:
    """Return the set of distinct attribution_source values stored in inference evidence."""
    conn = _connect()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT evidence FROM inferences WHERE evidence IS NOT NULL")
    sources = set()
    for (ev_str,) in cur.fetchall():
        if not ev_str:
            continue
        try:
            ev = json.loads(ev_str)
        except (json.JSONDecodeError, TypeError):
            continue
        src = ev.get("attribution_source")
        if src and isinstance(src, str):
            sources.add(src)
    conn.close()
    return sources


def update_inference_notes(inference_id, notes):
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "UPDATE inferences SET user_notes = ? WHERE id = ?",
        (notes, inference_id),
    )
    conn.commit()
    conn.close()


def dismiss_inference(inference_id):
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "UPDATE inferences SET dismissed = 1 WHERE id = ?",
        (inference_id,),
    )
    conn.commit()
    conn.close()


def get_inference_tags(inference_id):
    """Return list of tags for an inference."""
    conn = _connect()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT tag, confidence, created_at FROM event_tags WHERE inference_id = ? ORDER BY created_at DESC",
        (inference_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return [
        {"tag": r["tag"], "confidence": r["confidence"], "created_at": _normalise_ts(r["created_at"])}
        for r in rows
    ]


def add_inference_tag(inference_id, tag, confidence=1.0, *, allowed_tags=None):
    """Add a tag to an inference.

    Args:
        inference_id: The inference row id.
        tag: Tag string — must be a fingerprint ID (underscore form).
        confidence: User confidence 0–1.
        allowed_tags: Optional frozenset of valid tag IDs. If provided, raises
                      ValueError when tag is not in the set.
    """
    if allowed_tags is not None and tag not in allowed_tags:
        raise ValueError(f"Unknown tag: {tag!r}. Allowed: {sorted(allowed_tags)}")
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO event_tags (inference_id, tag, confidence, created_at) VALUES (?, ?, ?, ?)",
        (inference_id, tag, confidence, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    # Trigger ML training
    try:
        from mlss_monitor import state  # pylint: disable=import-outside-toplevel
        if state.detection_engine and state.detection_engine._attribution_engine:
            state.detection_engine._attribution_engine.train_on_tags()
    except Exception:
        pass


def remove_inference_tag(inference_id, tag):
    """Remove all rows matching (inference_id, tag) from event_tags.

    Idempotent — no error if nothing matches. Does NOT trigger ML
    retraining (training happens on add; removal just drops the
    supervised signal).
    """
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM event_tags WHERE inference_id = ? AND tag = ?",
        (inference_id, tag),
    )
    conn.commit()
    conn.close()


# ── Inference thresholds ──────────────────────────────────────────────────────

def get_thresholds():
    """Return all thresholds as a dict: key -> effective value (user override or default)."""
    conn = _connect()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT key, default_value, user_value FROM inference_thresholds")
    result = {}
    for r in cur.fetchall():
        result[r["key"]] = r["user_value"] if r["user_value"] is not None else r["default_value"]
    conn.close()
    return result


def get_all_thresholds():
    """Return full threshold rows for the settings UI."""
    conn = _connect()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM inference_thresholds ORDER BY key")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def update_threshold(key, user_value):
    """Set or clear a user override for a threshold. Pass None to reset to default."""
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "UPDATE inference_thresholds SET user_value = ? WHERE key = ?",
        (user_value, key),
    )
    conn.commit()
    conn.close()


def get_thresholds_for_evidence(keys):
    """Return threshold details for specific keys (for inference evidence)."""
    conn = _connect()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    placeholders = ",".join("?" for _ in keys)
    cur.execute(
        f"SELECT key, default_value, user_value, unit, label "
        f"FROM inference_thresholds WHERE key IN ({placeholders})",
        keys,
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return {
        r["key"]: {
            "label": r["label"],
            "value": r["user_value"] if r["user_value"] is not None else r["default_value"],
            "default": r["default_value"],
            "is_custom": r["user_value"] is not None,
            "unit": r["unit"],
        }
        for r in rows
    }


def get_recent_inference_by_type(event_type, hours=1):
    """Check if an inference of this type was created within the last N hours."""
    conn = _connect()
    cur = conn.cursor()
    since = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    cur.execute(
        "SELECT id FROM inferences WHERE event_type = ? AND created_at >= ? LIMIT 1",
        (event_type, since),
    )
    row = cur.fetchone()
    conn.close()
    return row is not None


_DB_COLUMNS = (
    ("tvoc",        "tvoc_ppb"),
    ("eco2",        "eco2_ppm"),
    ("temperature", "temperature_c"),
    ("humidity",    "humidity_pct"),
    ("pm1_0",       "pm1_ug_m3"),
    ("pm2_5",       "pm25_ug_m3"),
    ("pm10",        "pm10_ug_m3"),
    ("gas_co",      "co_ppb"),
    ("gas_no2",     "no2_ppb"),
    ("gas_nh3",     "nh3_ppb"),
)

# Mapping used by history query helpers and baseline computations.
# DB column name → NormalisedReading / API field name.
_DB_COL_TO_FIELD: dict[str, str] = dict(_DB_COLUMNS)

SENSOR_FIELDS: list[str] = [field for _, field in _DB_COLUMNS]


def get_24h_baselines() -> dict[str, float | None]:
    """Return 24-hour median for each sensor channel, keyed by NormalisedReading field name.

    Queries the last 24 hours of sensor_data. Returns None for any channel
    that has no readings in that window.
    """
    cols = ", ".join(col for col, _ in _DB_COLUMNS)
    cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()

    conn = None
    try:
        conn = _connect()
        rows = conn.execute(
            f"SELECT {cols} FROM sensor_data WHERE timestamp >= ? ORDER BY timestamp",
            (cutoff,),
        ).fetchall()
    finally:
        if conn:
            conn.close()

    result: dict[str, float | None] = {}
    for i, (_, nr_field) in enumerate(_DB_COLUMNS):
        values = [row[i] for row in rows if row[i] is not None]
        result[nr_field] = statistics.median(values) if values else None
    return result


# ── History range query helpers ───────────────────────────────────────────────

def get_sensor_data_range(start: str, end: str) -> list[dict]:
    """Return sensor_data rows (as dicts) for the given ISO-8601 time window.

    The ``sensor_data`` table stores timestamps in the form
    ``YYYY-MM-DDTHH:MM:SS.ffffffZ`` (see :func:`log_sensor_data`, which uses
    ``datetime.utcnow().isoformat()`` and :func:`_normalise_ts`).  We therefore
    normalise input bounds to the **T-separator** form so that SQLite's
    lexicographic string comparison matches correctly:

    * ``start`` keeps its T separator and *drops* any trailing ``Z``.  For a
      stored row ``2024-01-01T10:00:00.500000Z`` to be included when ``start``
      is ``2024-01-01T10:00:00`` (a prefix of the row), the bound must NOT have
      a ``Z`` (because ``'.' < 'Z'`` would otherwise exclude the fractional row).
    * ``end`` keeps its T separator and *adds* a trailing ``Z`` if missing.
      For a row stored exactly at the end second to be included, the bound must
      end in ``Z`` (or the row's longer microsecond suffix would compare as
      larger than the bound).

    Historically this function normalised bounds to a space separator, which
    produced empty results for any same-day narrow window because ``'T' > ' '``
    in ASCII caused stored rows to sort AFTER the end bound.  ``start`` and
    ``end`` may be supplied with either separator.
    """
    start_db = start.replace(" ", "T").rstrip("Z")
    end_db = end.replace(" ", "T")
    if not end_db.endswith("Z"):
        end_db += "Z"
    conn = _connect()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT timestamp, tvoc, eco2, temperature, humidity,
                  pm1_0, pm2_5, pm10, gas_co, gas_no2, gas_nh3
           FROM sensor_data WHERE timestamp >= ? AND timestamp <= ?
           ORDER BY timestamp ASC""",
        (start_db, end_db),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_hot_tier_range(start: str, end: str) -> list[dict]:
    """Return hot_tier rows (as dicts) for the given ISO-8601 time window.

    hot_tier stores timestamps with the T separator (no space), so the bounds
    are normalised to T-format (trailing Z stripped, space NOT replaced).
    """
    start_db = start.rstrip("Z")
    end_db = end.rstrip("Z")
    conn = _connect()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT timestamp, tvoc_ppb, eco2_ppm, temperature_c, humidity_pct,
                  pm25_ug_m3, co_ppb, no2_ppb, nh3_ppb
           FROM hot_tier WHERE timestamp >= ? AND timestamp <= ?
           ORDER BY timestamp ASC""",
        (start_db, end_db),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_pre_event_baselines(event_start: str) -> dict[str, float | None]:
    """Compute per-channel median over the 60-minute window ending at *event_start*.

    Returns a dict keyed by NormalisedReading / API field names
    (e.g. ``"tvoc_ppb"``, ``"temperature_c"``).  Any channel with no data in
    the window is mapped to ``None``.
    """
    try:
        ts = event_start.strip().replace(" ", "T")
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        try:
            start_dt = datetime.fromisoformat(ts)
        except ValueError:
            ts = ts[:19] + ts[19:].lstrip("0123456789.")
            if not ts.endswith("+00:00"):
                ts += "+00:00"
            start_dt = datetime.fromisoformat(ts)
        window_end = start_dt
        window_start = start_dt - timedelta(hours=1)
    except (ValueError, TypeError):
        return {f: None for f in SENSOR_FIELDS}

    baselines: dict[str, float | None] = {}
    try:
        conn = _connect()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            """
            SELECT tvoc, eco2, temperature, humidity,
                   pm1_0, pm2_5, pm10, gas_co, gas_no2, gas_nh3
            FROM sensor_data
            WHERE timestamp >= ? AND timestamp < ?
            ORDER BY timestamp
            """,
            # sensor_data timestamps use T-separator + Z suffix (see log_sensor_data).
            (window_start.strftime("%Y-%m-%dT%H:%M:%S"),
             window_end.strftime("%Y-%m-%dT%H:%M:%S")),
        )
        rows = cur.fetchall()
        conn.close()
    except Exception:
        return {f: None for f in SENSOR_FIELDS}

    if not rows:
        return {f: None for f in SENSOR_FIELDS}

    for db_col, field in _DB_COL_TO_FIELD.items():
        vals = [r[db_col] for r in rows if r[db_col] is not None]
        if vals:
            vals.sort()
            mid = len(vals) // 2
            baselines[field] = (vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2)
        else:
            baselines[field] = None

    return baselines


def get_baselines_7d_ago(window_start: str) -> dict[str, float | None]:
    """Return per-channel averages from the 24-hour window that ended 7 days before *window_start*.

    Returns an empty dict if no data is available for that period.
    Keys use the NormalisedReading / API field naming convention
    (e.g. ``"tvoc_ppb"``, ``"temperature_c"``).
    """
    # Build a DB column → API field lookup from _DB_COLUMNS.
    _db_to_api = dict(_DB_COLUMNS)
    db_cols = [col for col, _ in _DB_COLUMNS]

    try:
        ts = window_start.rstrip("Z")
        start_dt = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
        ago_end = start_dt - timedelta(days=7)
        ago_start = ago_end - timedelta(hours=24)
        # sensor_data timestamps use T-separator + Z suffix (see log_sensor_data).
        ago_start_db = ago_start.strftime("%Y-%m-%dT%H:%M:%S")
        ago_end_db = ago_end.strftime("%Y-%m-%dT%H:%M:%S")

        conn = _connect()
        try:
            cols_sql = ", ".join(f"AVG({c})" for c in db_cols)
            row = conn.execute(
                f"SELECT {cols_sql} FROM sensor_data WHERE timestamp >= ? AND timestamp < ?",
                (ago_start_db, ago_end_db),
            ).fetchone()
        finally:
            conn.close()

        if row is None or all(v is None for v in row):
            return {}
        return {_db_to_api[col]: row[i] for i, col in enumerate(db_cols)}
    except Exception:
        return {}
