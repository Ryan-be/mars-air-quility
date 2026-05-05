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
