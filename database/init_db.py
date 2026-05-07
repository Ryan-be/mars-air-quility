import logging
import sqlite3

from config import config
from database.grow_schema import create_grow_schema

DB_FILE = config.get("DB_FILE", "data/sensor_data.db")

log = logging.getLogger(__name__)


def create_db():
    conn = sqlite3.connect(DB_FILE, timeout=15)
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")  # allow concurrent reads + writes

    cur.execute("""
    CREATE TABLE IF NOT EXISTS sensor_data (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp DATETIME NOT NULL,
        temperature REAL,
        humidity REAL,
        eco2 INTEGER,
        tvoc INTEGER,
        annotation TEXT
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS fan_settings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tvoc_min INTEGER,
        tvoc_max INTEGER,
        temp_min REAL,
        temp_max REAL,
        enabled INTEGER DEFAULT 0
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS app_settings (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS weather_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp DATETIME NOT NULL,
        temp REAL,
        humidity REAL,
        feels_like REAL,
        wind_speed REAL,
        weather_code INTEGER,
        uv_index REAL
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS inferences (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at DATETIME NOT NULL,
        event_type TEXT NOT NULL CHECK(
            event_type IN (
                'tvoc_spike', 'eco2_danger', 'eco2_elevated',
                'correlated_pollution', 'sustained_poor_air',
                'mould_risk',
                'pm1_spike', 'pm1_elevated',
                'pm25_spike', 'pm25_elevated',
                'pm10_spike', 'pm10_elevated',
                'temp_high', 'temp_low',
                'humidity_high', 'humidity_low',
                'vpd_low', 'vpd_high',
                'rapid_temp_change', 'rapid_humidity_change',
                'hourly_summary', 'daily_summary',
                'daily_pattern', 'overnight_buildup',
                'anomaly_combustion_signature',
                'anomaly_particle_distribution',
                'anomaly_ventilation_quality',
                'anomaly_gas_relationship',
                'anomaly_thermal_moisture'
            ) OR event_type LIKE 'annotation_context_%'
              OR event_type LIKE 'anomaly_%'
              OR event_type LIKE 'ml_learned_%'
              OR event_type LIKE 'fingerprint_match'
        ),
        severity TEXT NOT NULL DEFAULT 'info'
            CHECK(severity IN ('info', 'warning', 'critical')),
        title TEXT NOT NULL,
        description TEXT,
        action TEXT,
        evidence TEXT,
        confidence REAL NOT NULL DEFAULT 0.5,
        sensor_data_start_id INTEGER,
        sensor_data_end_id INTEGER,
        annotation TEXT,
        user_notes TEXT,
        dismissed INTEGER DEFAULT 0,
        -- Promoted-from-JSON typed columns (see
        -- mlss_monitor/inference_evidence_storage.py + JSON_STORAGE_AUDIT.md).
        -- The legacy ``evidence`` TEXT column above is kept for one
        -- release per DATABASE.md's deprecation policy.
        evidence_attribution_source TEXT,
        evidence_attribution_confidence REAL,
        evidence_runner_up_id TEXT,
        evidence_runner_up_confidence REAL,
        evidence_detection_method TEXT,
        evidence_extras TEXT
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS event_tags (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        inference_id INTEGER NOT NULL,
        tag TEXT NOT NULL,
        confidence REAL DEFAULT 1.0,
        created_at DATETIME NOT NULL,
        FOREIGN KEY (inference_id) REFERENCES inferences(id)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS inference_thresholds (
        key TEXT PRIMARY KEY,
        default_value REAL NOT NULL,
        user_value REAL,
        unit TEXT NOT NULL,
        label TEXT NOT NULL,
        description TEXT
    );
    """)

    # Seed default thresholds if table is empty
    cur.execute("SELECT COUNT(*) FROM inference_thresholds")
    if cur.fetchone()[0] == 0:
        _defaults = [
            ("tvoc_high",     500,  "ppb",   "TVOC High",
             "WHO 'high' threshold for total VOCs"),
            ("tvoc_moderate", 250,  "ppb",   "TVOC Moderate",
             "WHO 'good' ceiling — triggers spike detection"),
            ("eco2_cognitive", 1000, "ppm",  "eCO2 Cognitive",
             "CO2 level where cognitive impairment begins"),
            ("eco2_danger",   2000, "ppm",   "eCO2 Danger",
             "CO2 level causing headaches and drowsiness"),
            ("temp_high",     28.0, "°C",    "Temperature High",
             "Upper comfort zone boundary"),
            ("temp_low",      15.0, "°C",    "Temperature Low",
             "Lower comfort zone boundary"),
            ("hum_high",      70.0, "%",     "Humidity High",
             "Above this promotes mould and dust mites"),
            ("hum_low",       30.0, "%",     "Humidity Low",
             "Below this causes dry skin and irritation"),
            ("vpd_low",       0.4,  "kPa",  "VPD Low",
             "Below this air is too saturated for plants"),
            ("vpd_high",      1.6,  "kPa",  "VPD High",
             "Above this plants close stomata (stress)"),
            ("spike_factor",  2.0,  "x",    "Spike Factor",
             "Multiplier above rolling mean to detect spikes"),
            ("min_readings",  6,    "count", "Minimum Readings",
             "Data points required before analysis runs"),
            ("mould_hum",     70.0, "%",     "Mould Risk Humidity",
             "Sustained humidity promoting mould growth"),
            ("mould_temp",    20.0, "°C",    "Mould Risk Temp",
             "Warm temps accelerating mould growth"),
            ("mould_hours",   4,    "hrs",   "Mould Risk Duration",
             "Hours of sustained conditions before flagging"),
            ("pm1_high",       10.0, "µg/m³", "PM1 High",
             "Elevated ultrafine particle level (no formal WHO guideline; proxy threshold)"),
            ("pm25_moderate", 12.0, "µg/m³", "PM2.5 Moderate",
             "WHO 24-hr guideline — below this is 'good' air quality"),
            ("pm25_high",     35.0, "µg/m³", "PM2.5 High",
             "Unhealthy for sensitive groups above this level"),
            ("pm10_high",     50.0, "µg/m³", "PM10 High",
             "WHO 24-hr guideline for coarse particles"),
            ("pm_spike_factor", 3.0, "x",    "PM Spike Factor",
             "Multiplier above rolling mean to detect sudden PM spikes"),
        ]
        for key, default, unit, label, desc in _defaults:
            cur.execute(
                "INSERT INTO inference_thresholds (key, default_value, unit, label, description) "
                "VALUES (?, ?, ?, ?, ?)",
                (key, default, unit, label, desc),
            )

    # Ensure new thresholds are added to existing databases
    _new_thresholds = [
        ("mould_hum",   70.0, "%",   "Mould Risk Humidity",
         "Sustained humidity promoting mould growth"),
        ("mould_temp",  20.0, "°C",  "Mould Risk Temp",
         "Warm temps accelerating mould growth"),
        ("mould_hours", 4,    "hrs", "Mould Risk Duration",
         "Hours of sustained conditions before flagging"),
        ("pm1_high",       10.0, "µg/m³", "PM1 High",
         "Elevated ultrafine particle level (no formal WHO guideline; proxy threshold)"),
        ("pm25_moderate", 12.0, "µg/m³", "PM2.5 Moderate",
         "WHO 24-hr guideline — below this is 'good' air quality"),
        ("pm25_high",     35.0, "µg/m³", "PM2.5 High",
         "Unhealthy for sensitive groups above this level"),
        ("pm10_high",     50.0, "µg/m³", "PM10 High",
         "WHO 24-hr guideline for coarse particles"),
        ("pm_spike_factor", 3.0, "x",    "PM Spike Factor",
         "Multiplier above rolling mean to detect sudden PM spikes"),
    ]
    for key, default, unit, label, desc in _new_thresholds:
        cur.execute(
            "INSERT OR IGNORE INTO inference_thresholds (key, default_value, unit, label, description) "
            "VALUES (?, ?, ?, ?, ?)",
            (key, default, unit, label, desc),
        )

    # Migrations: add columns introduced after initial release
    for migration in [
        "ALTER TABLE sensor_data ADD COLUMN fan_power_w REAL",
        "ALTER TABLE sensor_data ADD COLUMN vpd_kpa REAL",
        "ALTER TABLE fan_settings ADD COLUMN temp_enabled INTEGER DEFAULT 1",
        "ALTER TABLE fan_settings ADD COLUMN tvoc_enabled INTEGER DEFAULT 1",
        "ALTER TABLE fan_settings ADD COLUMN humidity_enabled INTEGER DEFAULT 0",
        "ALTER TABLE fan_settings ADD COLUMN humidity_max REAL DEFAULT 70.0",
        "ALTER TABLE sensor_data ADD COLUMN pm1_0 REAL",
        "ALTER TABLE sensor_data ADD COLUMN pm2_5 REAL",
        "ALTER TABLE sensor_data ADD COLUMN pm10 REAL",
        "ALTER TABLE fan_settings ADD COLUMN pm25_enabled INTEGER DEFAULT 0",
        "ALTER TABLE fan_settings ADD COLUMN pm25_max REAL DEFAULT 25.0",
        "ALTER TABLE fan_settings ADD COLUMN pm_stale_minutes REAL DEFAULT 10.0",
        "ALTER TABLE sensor_data ADD COLUMN gas_co REAL",
        "ALTER TABLE sensor_data ADD COLUMN gas_no2 REAL",
        "ALTER TABLE sensor_data ADD COLUMN gas_nh3 REAL",
        "ALTER TABLE hot_tier ADD COLUMN pm1_ug_m3 REAL",
        "ALTER TABLE hot_tier ADD COLUMN pm10_ug_m3 REAL",
        # Phase 2 schema cleanup: promote runtime-mutable JSON to typed columns
        "ALTER TABLE grow_unit_capabilities ADD COLUMN health TEXT NOT NULL DEFAULT 'untested'",
        "ALTER TABLE grow_unit_capabilities ADD COLUMN last_seen_at DATETIME",
        # Note: SQLite doesn't support adding a CHECK constraint via ALTER. The
        # CHECK is enforced via app-level pydantic + a partial recreate would
        # require copying the table. Acceptable trade-off — pydantic enforces
        # at every WS boundary; the column just receives the validated string.
        "CREATE INDEX IF NOT EXISTS idx_grow_caps_unit_health "
        "ON grow_unit_capabilities(unit_id, health)",
        # Drop dead/redundant JSON cache columns. SQLite 3.35+ supports DROP
        # COLUMN; Pi OS Lite ships 3.40+. light_phase_override_json was
        # superseded by grow_light_windows in Phase 1; last_known_state_json
        # was a per-frame denormalised cache now fetched live from
        # grow_telemetry (already indexed by (unit_id, timestamp_utc DESC)).
        "ALTER TABLE grow_units DROP COLUMN light_phase_override_json",
        "ALTER TABLE grow_units DROP COLUMN last_known_state_json",
        # Phase 3 Task 1: firmware-reported metadata columns. All nullable;
        # populated by Task 2's firmware capabilities/telemetry envelopes.
        "ALTER TABLE grow_units ADD COLUMN firmware_version TEXT",
        "ALTER TABLE grow_units ADD COLUMN last_uptime_s REAL",
        "ALTER TABLE grow_units ADD COLUMN last_buffer_size INTEGER",
        # Phase 3 Task 5: snooze support on grow_errors. Nullable; rows
        # with snoozed_until > now() render muted client-side but are
        # NOT filtered server-side (admins can still un-snooze them).
        "ALTER TABLE grow_errors ADD COLUMN snoozed_until DATETIME",
        # Promotion of ``inferences.evidence`` (JSON-in-TEXT) → typed
        # columns + extras blob. The legacy ``evidence`` TEXT column
        # is retained for one release per DATABASE.md's deprecation
        # policy; see mlss_monitor/inference_evidence_storage.py and
        # docs/JSON_STORAGE_AUDIT.md.
        "ALTER TABLE inferences ADD COLUMN evidence_attribution_source TEXT",
        "ALTER TABLE inferences ADD COLUMN evidence_attribution_confidence REAL",
        "ALTER TABLE inferences ADD COLUMN evidence_runner_up_id TEXT",
        "ALTER TABLE inferences ADD COLUMN evidence_runner_up_confidence REAL",
        "ALTER TABLE inferences ADD COLUMN evidence_detection_method TEXT",
        "ALTER TABLE inferences ADD COLUMN evidence_extras TEXT",
    ]:
        try:
            cur.execute(migration)
        except Exception:  # pylint: disable=broad-except
            pass  # column already exists / already dropped

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        github_username TEXT    NOT NULL UNIQUE COLLATE NOCASE,
        display_name    TEXT    NOT NULL DEFAULT '',
        role            TEXT    NOT NULL DEFAULT 'viewer'
                            CHECK(role IN ('admin', 'controller', 'viewer')),
        created_at      DATETIME NOT NULL,
        last_login      DATETIME,
        is_active       INTEGER NOT NULL DEFAULT 1
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS login_log (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        github_username TEXT    NOT NULL,
        logged_in_at    DATETIME NOT NULL
    );
    """)

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_login_log_user "
        "ON login_log (github_username, logged_in_at DESC)"
    )

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_sensor_data_timestamp "
        "ON sensor_data (timestamp)"
    )

    cur.execute("SELECT COUNT(*) FROM fan_settings")
    if cur.fetchone()[0] == 0:
        cur.execute("""
        INSERT INTO fan_settings (tvoc_min, tvoc_max, temp_min, temp_max, enabled)
        VALUES (?, ?, ?, ?, ?)
        """, (0, 500, 0.0, 20.0, 0))

    cur.execute("""
    CREATE TABLE IF NOT EXISTS hot_tier (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT    NOT NULL,
        source    TEXT    NOT NULL,
        tvoc_ppb      REAL,
        eco2_ppm      REAL,
        temperature_c REAL,
        humidity_pct  REAL,
        pm1_ug_m3     REAL,
        pm25_ug_m3    REAL,
        pm10_ug_m3    REAL,
        co_ppb        REAL,
        no2_ppb       REAL,
        nh3_ppb       REAL
    );
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_hot_tier_timestamp ON hot_tier (timestamp);"
    )

    cur.execute("""
    CREATE TABLE IF NOT EXISTS incidents (
        id           TEXT PRIMARY KEY,
        started_at   TIMESTAMP NOT NULL,
        ended_at     TIMESTAMP NOT NULL,
        max_severity TEXT NOT NULL DEFAULT 'info'
                         CHECK(max_severity IN ('info', 'warning', 'critical')),
        confidence   REAL NOT NULL DEFAULT 0,
        title        TEXT NOT NULL,
        signature    TEXT NOT NULL DEFAULT '[]'
    );
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_incidents_started "
        "ON incidents (started_at DESC)"
    )

    cur.execute("""
    CREATE TABLE IF NOT EXISTS incident_alerts (
        incident_id TEXT    NOT NULL REFERENCES incidents(id),
        alert_id    INTEGER NOT NULL REFERENCES inferences(id),
        is_primary  INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY (incident_id, alert_id)
    );
    """)

    # Promotion of ``incidents.signature`` (JSON-in-TEXT) → typed
    # sub-table. The legacy column above is retained for one release
    # per DATABASE.md's deprecation policy; see
    # docs/JSON_STORAGE_AUDIT.md and mlss_monitor/incident_signature_storage.py.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS incident_signature_features (
        incident_id TEXT    NOT NULL,
        feature_idx INTEGER NOT NULL,
        value       REAL    NOT NULL,
        PRIMARY KEY (incident_id, feature_idx),
        FOREIGN KEY (incident_id) REFERENCES incidents(id) ON DELETE CASCADE
    );
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_isf_incident "
        "ON incident_signature_features(incident_id)"
    )

    cur.execute("""
    CREATE TABLE IF NOT EXISTS alert_signal_deps (
        alert_id     INTEGER NOT NULL REFERENCES inferences(id),
        sensor       TEXT    NOT NULL,
        r            REAL,
        lag_seconds  INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY  (alert_id, sensor)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS incident_splits (
        alert_id    INTEGER PRIMARY KEY REFERENCES inferences(id) ON DELETE CASCADE,
        created_by  TEXT,
        created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Plant Grow Unit tables (Phase 1)
    create_grow_schema(cur)

    conn.commit()
    conn.close()

    # Run historic-data migrations (idempotent — only touches rows that
    # haven't been migrated yet). Empty case is fast (filtered by IS NULL
    # / NOT EXISTS), so startup time on a fresh DB is unaffected.
    from database.migrations import run_all_migrations
    summary = run_all_migrations(DB_FILE)
    if any(v > 0 for v in summary.values()):
        log.info("data migrations complete: %s", summary)


if __name__ == "__main__":
    create_db()
    print("✅ SQLite database created at data/sensor_data.db")
