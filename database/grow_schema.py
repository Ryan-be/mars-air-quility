"""Plant Grow Unit database schema. All grow_* tables created here.

Called from database.init_db.create_db() so table creation happens in the
same transaction as the existing MLSS schema.
"""


def create_grow_schema(cur):
    """Create all grow_* tables. Idempotent (uses CREATE TABLE IF NOT EXISTS)."""
    cur.execute("""
    CREATE TABLE IF NOT EXISTS grow_units (
      id                          INTEGER PRIMARY KEY AUTOINCREMENT,
      hardware_serial             TEXT UNIQUE NOT NULL,
      label                       TEXT NOT NULL,
      description                 TEXT,
      sown_at                     DATETIME,
      enrolled_at                 DATETIME NOT NULL,
      bearer_token_hash           TEXT NOT NULL,
      is_active                   INTEGER NOT NULL DEFAULT 1,
      current_phase               TEXT NOT NULL DEFAULT 'vegetative'
                                    CHECK(current_phase IN
                                      ('seedling','vegetative','flowering','fruiting','dormant')),
      phase_set_by                TEXT NOT NULL DEFAULT 'user'
                                    CHECK(phase_set_by IN ('user','image_classifier')),
      phase_set_at                DATETIME NOT NULL,
      plant_type                  TEXT NOT NULL DEFAULT 'generic',
      medium_type                 TEXT NOT NULL DEFAULT 'soil'
                                    CHECK(medium_type IN ('soil','coco','rockwool','custom')),
      soil_dry_raw                INTEGER,
      soil_wet_raw                INTEGER,
      light_phase_override_json   TEXT,
      watering_target_override    REAL,
      watering_kp_override        REAL,
      watering_ki_override        REAL,
      watering_kd_override        REAL,
      soak_window_min_override    INTEGER,
      pulse_min_s_override        REAL,
      pulse_max_s_override        REAL,
      photo_interval_min_override INTEGER,
      buffer_retention_days       INTEGER,
      last_seen_at                DATETIME,
      last_telemetry_at           DATETIME,
      last_known_state_json       TEXT
    );
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_grow_units_active "
        "ON grow_units(is_active, last_seen_at DESC)"
    )

    cur.execute("""
    CREATE TABLE IF NOT EXISTS grow_unit_capabilities (
      unit_id      INTEGER NOT NULL REFERENCES grow_units(id) ON DELETE CASCADE,
      channel      TEXT NOT NULL,
      hardware     TEXT,
      is_required  INTEGER NOT NULL DEFAULT 0,
      unit_label   TEXT,
      installed_at DATETIME NOT NULL,
      details_json TEXT,
      PRIMARY KEY (unit_id, channel)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS grow_telemetry (
      id                  INTEGER PRIMARY KEY AUTOINCREMENT,
      unit_id             INTEGER NOT NULL REFERENCES grow_units(id),
      timestamp_utc       DATETIME NOT NULL,
      soil_moisture_raw   INTEGER NOT NULL,
      soil_moisture_pct   REAL,
      light_state         INTEGER NOT NULL,
      pump_state          INTEGER NOT NULL,
      soil_temp_c         REAL,
      ambient_lux         REAL,
      air_temp_c          REAL,
      air_humidity_pct    REAL,
      reservoir_level_pct REAL
    );
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_grow_telemetry_unit_time "
        "ON grow_telemetry(unit_id, timestamp_utc DESC)"
    )

    cur.execute("""
    CREATE TABLE IF NOT EXISTS grow_watering_events (
      id                  INTEGER PRIMARY KEY AUTOINCREMENT,
      unit_id             INTEGER NOT NULL REFERENCES grow_units(id),
      timestamp_utc       DATETIME NOT NULL,
      trigger             TEXT NOT NULL CHECK(trigger IN ('pid','manual','identify_test')),
      duration_s          REAL NOT NULL,
      soil_pct_before     REAL,
      soil_pct_after_5min REAL,
      triggered_by        TEXT,
      pid_error           REAL,
      pid_p_term          REAL,
      pid_i_term          REAL,
      pid_d_term          REAL
    );
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_grow_watering_unit_time "
        "ON grow_watering_events(unit_id, timestamp_utc DESC)"
    )

    cur.execute("""
    CREATE TABLE IF NOT EXISTS grow_photos (
      id                       INTEGER PRIMARY KEY AUTOINCREMENT,
      unit_id                  INTEGER NOT NULL REFERENCES grow_units(id) ON DELETE CASCADE,
      taken_at                 DATETIME NOT NULL,
      file_path                TEXT NOT NULL,
      width_px                 INTEGER NOT NULL,
      height_px                INTEGER NOT NULL,
      size_bytes               INTEGER NOT NULL,
      jpeg_quality             INTEGER,
      shutter_us               INTEGER,
      iso                      INTEGER,
      white_balance            TEXT,
      classified_phase         TEXT,
      classifier_confidence    REAL,
      classified_at            DATETIME,
      telemetry_id             INTEGER REFERENCES grow_telemetry(id)
    );
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_grow_photos_unit_time "
        "ON grow_photos(unit_id, taken_at DESC)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_grow_photos_telemetry "
        "ON grow_photos(telemetry_id)"
    )

    cur.execute("""
    CREATE TABLE IF NOT EXISTS grow_plant_profiles (
      id                    INTEGER PRIMARY KEY AUTOINCREMENT,
      plant_type            TEXT NOT NULL,
      phase                 TEXT NOT NULL,
      target_moisture_pct   REAL NOT NULL,
      deadband_pct          REAL NOT NULL DEFAULT 5,
      kp                    REAL NOT NULL DEFAULT 0.4,
      ki                    REAL NOT NULL DEFAULT 0,
      kd                    REAL NOT NULL DEFAULT 0,
      min_pulse_s           REAL NOT NULL DEFAULT 2,
      max_pulse_s           REAL NOT NULL DEFAULT 8,
      soak_window_min       INTEGER,
      default_light_hours   REAL NOT NULL DEFAULT 16,
      is_shipped            INTEGER NOT NULL DEFAULT 0,
      notes                 TEXT,
      UNIQUE(plant_type, phase)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS grow_light_windows (
      id           INTEGER PRIMARY KEY AUTOINCREMENT,
      unit_id      INTEGER NOT NULL REFERENCES grow_units(id) ON DELETE CASCADE,
      phase        TEXT NOT NULL,
      start_hh_mm  TEXT NOT NULL,
      end_hh_mm    TEXT NOT NULL,
      sort_order   INTEGER NOT NULL DEFAULT 0
    );
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_glw_unit_phase "
        "ON grow_light_windows(unit_id, phase)"
    )

    cur.execute("""
    CREATE TABLE IF NOT EXISTS grow_medium_defaults (
      medium_type TEXT PRIMARY KEY,
      dry_raw     INTEGER NOT NULL,
      wet_raw     INTEGER NOT NULL
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS grow_errors (
      id            INTEGER PRIMARY KEY AUTOINCREMENT,
      unit_id       INTEGER REFERENCES grow_units(id) ON DELETE CASCADE,
      timestamp_utc DATETIME NOT NULL,
      severity      TEXT NOT NULL CHECK(severity IN ('info','warning','critical')),
      kind          TEXT NOT NULL,
      message       TEXT NOT NULL,
      details_json  TEXT,
      resolved_at   DATETIME
    );
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_grow_errors_unit_time "
        "ON grow_errors(unit_id, timestamp_utc DESC)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_grow_errors_unresolved "
        "ON grow_errors(resolved_at) WHERE resolved_at IS NULL"
    )
