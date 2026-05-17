"""Plant Grow Unit database schema. All grow_* tables created here.

Called from database.init_db.create_db() so table creation happens in the
same transaction as the existing MLSS schema.
"""

import secrets
from argon2 import PasswordHasher

_seed_hasher = PasswordHasher()


def _add_column_if_missing(cur, table, col_def):
    """ALTER TABLE … ADD COLUMN, skipped if the column already exists.

    The codebase's primary migration channel is the raw try/except ALTER
    list in ``database/init_db.py``. That works fine for the
    air-quality side of the schema where columns are added once and the
    catch-all `except` is fine. For grow_plant_profiles we prefer the
    PRAGMA-guarded path because:

      * It's co-located with the CREATE TABLE for the same table, so
        a future reviewer can see both the canonical column list AND
        the migration that brings older DBs up to that list in one
        place.
      * The PRAGMA lookup tells us *why* the ALTER was skipped (column
        already present) vs the try/except path which swallows any
        ALTER failure indiscriminately (e.g. a typo in the column
        definition would silently no-op).

    `col_def` is the full column DDL — e.g.
    ``"soil_temp_ideal_min_c REAL"`` — column name first whitespace-
    delimited word.
    """
    col_name = col_def.split()[0]
    cur.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cur.fetchall()}
    if col_name not in existing:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")


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
      watering_target_override    REAL,
      watering_kp_override        REAL,
      watering_ki_override        REAL,
      watering_kd_override        REAL,
      soak_window_min_override    INTEGER,
      pulse_min_s_override        REAL,
      pulse_max_s_override        REAL,
      -- Reserved for a future per-unit photo cadence editor. The firmware's
      -- LoopConfig.photo_interval_min is currently a hardcoded 30 (the
      -- photo-schedule editor exposes the *when* via photo_active_*_hour
      -- but not the cadence). Pre-Phase-4 audit (Flow 4 #1) flagged this
      -- as half-wired; we keep the column for forward-compat but no
      -- endpoint reads or writes it. Drop in a future migration if the
      -- decision settles on "cadence is invariant".
      photo_interval_min_override INTEGER,
      -- Photo capture schedule (Phase 4 polish):
      -- Both NULL => capture 24/7. Both set => capture only between
      -- start_hour (inclusive) and end_hour (exclusive), wall-clock UTC.
      -- Wraps over midnight when start > end (e.g. 22..6 = 22:00..06:00).
      -- Replaces the previous firmware-side hardcoded (6, 22) default
      -- whose assumption "no grow light => no useful photo" was wrong
      -- whenever ambient light was available (windows, room lamps).
      photo_active_start_hour     INTEGER,
      photo_active_end_hour       INTEGER,
      buffer_retention_days       INTEGER,
      last_seen_at                DATETIME,
      last_telemetry_at           DATETIME,
      -- Phase 3 Task 1: firmware-reported metadata. All nullable;
      -- populated by the firmware's capabilities/telemetry envelopes.
      firmware_version            TEXT,
      last_uptime_s               REAL,
      last_buffer_size            INTEGER,
      -- Buffer-inspection UI (Phase 3 follow-up): JSON-encoded
      -- snapshots of the firmware-side message buffer + photo buffer.
      -- Piggybacked on every Nth telemetry frame; persisted by
      -- handle_telemetry with omit-doesnt-clobber semantics so the
      -- Diagnostics tab keeps showing the last good summary between
      -- piggybacks. Both nullable — old firmware that doesn't emit
      -- the summaries leaves these NULL forever.
      last_buffer_summary_json    TEXT,
      last_photo_buffer_summary_json TEXT
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
      health       TEXT NOT NULL DEFAULT 'untested'
                     CHECK(health IN ('connected','untested','unresponsive','no_hardware')),
      last_seen_at DATETIME,
      PRIMARY KEY (unit_id, channel)
    );
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_grow_caps_unit_health "
        "ON grow_unit_capabilities(unit_id, health)"
    )

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
      telemetry_id             INTEGER REFERENCES grow_telemetry(id),
      UNIQUE(unit_id, taken_at)
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
      -- Plant-happiness thresholds (per plant_type + phase). Each
      -- dimension carves the value space into 5 zones via a 4-threshold
      -- ladder: critical_low / tolerated_low / ideal / tolerated_high /
      -- critical_high. All nullable — a row with any threshold NULL
      -- means "no happiness signal for that dimension on this plant +
      -- phase" and the API + UI fall through to the existing variant-
      -- based colouring. Added in a later migration; see
      -- _add_column_if_missing below for the on-existing-DB path.
      soil_temp_critical_min_c        REAL,
      soil_temp_ideal_min_c           REAL,
      soil_temp_ideal_max_c           REAL,
      soil_temp_critical_max_c        REAL,
      soil_moisture_critical_min_pct  REAL,
      soil_moisture_ideal_min_pct     REAL,
      soil_moisture_ideal_max_pct     REAL,
      soil_moisture_critical_max_pct  REAL,
      UNIQUE(plant_type, phase)
    );
    """)
    # Migration for already-deployed DBs whose grow_plant_profiles was
    # created before the 8 happiness-threshold columns existed.
    # CREATE TABLE IF NOT EXISTS doesn't ALTER, so we need explicit
    # column-add calls — guarded by a PRAGMA table_info lookup so this
    # is idempotent and safe to re-run on already-migrated DBs.
    for col_def in (
        "soil_temp_critical_min_c REAL",
        "soil_temp_ideal_min_c REAL",
        "soil_temp_ideal_max_c REAL",
        "soil_temp_critical_max_c REAL",
        "soil_moisture_critical_min_pct REAL",
        "soil_moisture_ideal_min_pct REAL",
        "soil_moisture_ideal_max_pct REAL",
        "soil_moisture_critical_max_pct REAL",
    ):
        _add_column_if_missing(cur, "grow_plant_profiles", col_def)

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
      id             INTEGER PRIMARY KEY AUTOINCREMENT,
      unit_id        INTEGER REFERENCES grow_units(id) ON DELETE CASCADE,
      timestamp_utc  DATETIME NOT NULL,
      severity       TEXT NOT NULL CHECK(severity IN ('info','warning','critical')),
      kind           TEXT NOT NULL,
      message        TEXT NOT NULL,
      details_json   TEXT,
      subject_sensor TEXT,  -- populated for sensor_* kinds; NULL otherwise
      resolved_at    DATETIME,
      -- Phase 3 Task 5: muted-until timestamp for snooze. NULL when not
      -- snoozed; rows where snoozed_until > now() render muted in the
      -- /grow/errors fleet-wide error log but are NOT filtered out
      -- server-side (admins can still un-snooze them).
      snoozed_until  DATETIME
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
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_grow_errors_recovery "
        "ON grow_errors(unit_id, kind, subject_sensor, resolved_at)"
    )

    # Phase 4 #7: operator-authored notes pinned to a timestamp on a
    # unit's history. Surfaces as markers on the moisture chart and
    # the photo-timelapse scrubber so an operator can write "started
    # blooming nutrients today" against the moment it happened and
    # have the chart show that context next to the soil-moisture
    # curve. RBAC: viewer reads, controller+admin write; only the
    # original author or an admin can edit/delete a given entry
    # (enforced in the route layer, not the schema, so admin override
    # works without a special-case column).
    cur.execute("""
    CREATE TABLE IF NOT EXISTS grow_journal_entries (
      id             INTEGER PRIMARY KEY AUTOINCREMENT,
      unit_id        INTEGER NOT NULL REFERENCES grow_units(id) ON DELETE CASCADE,
      timestamp_utc  DATETIME NOT NULL,  -- the moment the entry pertains to
      author         TEXT NOT NULL,      -- session["user"] of the writer
      body           TEXT NOT NULL,      -- free-form note (markdown not rendered)
      created_at     DATETIME NOT NULL,
      updated_at     DATETIME            -- NULL until first edit
    );
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_grow_journal_unit_time "
        "ON grow_journal_entries(unit_id, timestamp_utc DESC)"
    )

    # Phase 4 #8: time-lapse video render job queue. An operator picks
    # a range + framerate via the History tab; the row enters the
    # `queued` state and a background worker (mlss_monitor.grow.
    # timelapse_jobs) picks it up, calls ffmpeg against the unit's
    # grow_photos in date order, drops an MP4 under
    # data/timelapses/<unit>/<job_id>.mp4, and flips status to
    # complete (or failed with an error_message). No Celery/RQ — the
    # in-process daemon thread polls every 30s for v1.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS grow_timelapse_jobs (
      id             INTEGER PRIMARY KEY AUTOINCREMENT,
      unit_id        INTEGER NOT NULL REFERENCES grow_units(id) ON DELETE CASCADE,
      requested_by   TEXT NOT NULL,
      requested_at   DATETIME NOT NULL,
      range          TEXT NOT NULL,              -- '24h' / '7d' / '30d' / '90d' / 'all'
      fps            INTEGER NOT NULL DEFAULT 10,
      status         TEXT NOT NULL DEFAULT 'queued'
                       CHECK(status IN ('queued','running','complete','failed')),
      output_path    TEXT,                        -- relative to data/timelapses/
      error_message  TEXT,                        -- populated when status='failed'
      started_at     DATETIME,
      completed_at   DATETIME
    );
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_grow_timelapse_status "
        "ON grow_timelapse_jobs(status, requested_at)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_grow_timelapse_unit "
        "ON grow_timelapse_jobs(unit_id, requested_at DESC)"
    )

    _seed_grow_data(cur)


_SHIPPED_PROFILES = [
    # (plant_type, phase, target%, deadband, kp, ki, kd, min_pulse, max_pulse, soak, light_h)
    ("tomato",      "seedling",   60, 5, 0.3, 0, 0, 1, 4, 30, 16),
    ("tomato",      "vegetative", 55, 5, 0.4, 0, 0, 2, 8, 30, 16),
    ("tomato",      "flowering",  50, 5, 0.4, 0, 0, 2, 8, 60, 12),
    ("tomato",      "fruiting",   50, 5, 0.4, 0, 0, 2, 8, 60, 12),
    ("basil",       "vegetative", 60, 5, 0.4, 0, 0, 2, 6, 30, 14),
    ("lettuce",     "vegetative", 65, 5, 0.3, 0, 0, 2, 6, 30, 14),
    ("microgreens", "seedling",   70, 3, 0.3, 0, 0, 1, 4, 20, 16),
    ("pepper",      "vegetative", 55, 5, 0.4, 0, 0, 2, 8, 45, 16),
    # Chili (Capsicum annuum / chinense / etc.) — same Solanaceae family as
    # pepper + tomato. Tunables mirror pepper for vegetative; the fruiting
    # cycle wants slightly drier soil (chilis like a little stress for heat
    # development) and a longer soak window to discourage shallow roots.
    ("chili",       "seedling",   58, 5, 0.3, 0, 0, 1, 4, 45, 16),
    ("chili",       "vegetative", 55, 5, 0.4, 0, 0, 2, 8, 45, 16),
    ("chili",       "flowering",  50, 5, 0.4, 0, 0, 2, 8, 60, 14),
    ("chili",       "fruiting",   45, 5, 0.4, 0, 0, 2, 8, 90, 14),
    ("generic",     "seedling",   60, 5, 0.3, 0, 0, 1, 4, 45, 16),
    ("generic",     "vegetative", 55, 5, 0.4, 0, 0, 2, 8, 45, 16),
    ("generic",     "flowering",  50, 5, 0.4, 0, 0, 2, 8, 60, 12),
]

_SHIPPED_MEDIUMS = [
    ("soil",     200, 1500),
    ("coco",     250, 1700),
    ("rockwool", 300, 1900),
]


# Plant-happiness thresholds, keyed by (plant_type, phase). Format per
# dimension: (critical_min, ideal_min, ideal_max, critical_max). Values
# below critical_min => critical_low; below ideal_min => tolerated_low;
# between ideal_min and ideal_max (inclusive on both) => ideal; up to
# critical_max (inclusive) => tolerated_high; above critical_max =>
# critical_high. See _zone() in mlss_monitor.routes.api_grow_units for
# the exact algorithm.
#
# Values are the user-approved set — do not tune in-place without going
# back to that approval cycle. Markers like "= veg" in the original
# table have been expanded inline so the seed is fully explicit.
# Microgreens has no biological dormancy so the dormant row reuses the
# vegetative tuning rather than introducing arbitrary "cold storage"
# numbers.
THRESHOLD_SEEDS = {
    # (plant_type, phase): {
    #   "soil_temp":     (cmin, imin, imax, cmax)   °C
    #   "soil_moisture": (cmin, imin, imax, cmax)   %
    # }
    ("chili",       "seedling"):   {"soil_temp": (15, 24, 30, 35), "soil_moisture": (40, 50, 70, 85)},
    ("chili",       "vegetative"): {"soil_temp": (13, 21, 27, 32), "soil_moisture": (20, 35, 60, 85)},
    ("chili",       "flowering"):  {"soil_temp": (16, 21, 27, 32), "soil_moisture": (25, 40, 65, 85)},
    ("chili",       "fruiting"):   {"soil_temp": (16, 21, 27, 32), "soil_moisture": (30, 45, 70, 85)},
    ("chili",       "dormant"):    {"soil_temp": (5,  10, 18, 25), "soil_moisture": (10, 20, 40, 60)},

    # pepper mirrors chili in the same Solanaceae family.
    ("pepper",      "seedling"):   {"soil_temp": (15, 24, 30, 35), "soil_moisture": (40, 50, 70, 85)},
    ("pepper",      "vegetative"): {"soil_temp": (13, 21, 27, 32), "soil_moisture": (20, 35, 60, 85)},
    ("pepper",      "flowering"):  {"soil_temp": (16, 21, 27, 32), "soil_moisture": (25, 40, 65, 85)},
    ("pepper",      "fruiting"):   {"soil_temp": (16, 21, 27, 32), "soil_moisture": (30, 45, 70, 85)},
    ("pepper",      "dormant"):    {"soil_temp": (5,  10, 18, 25), "soil_moisture": (10, 20, 40, 60)},

    ("tomato",      "seedling"):   {"soil_temp": (13, 21, 27, 35), "soil_moisture": (40, 55, 75, 90)},
    ("tomato",      "vegetative"): {"soil_temp": (10, 18, 24, 32), "soil_moisture": (25, 40, 70, 85)},
    ("tomato",      "flowering"):  {"soil_temp": (13, 18, 24, 32), "soil_moisture": (30, 45, 70, 85)},
    ("tomato",      "fruiting"):   {"soil_temp": (13, 18, 24, 35), "soil_moisture": (35, 50, 75, 90)},
    ("tomato",      "dormant"):    {"soil_temp": (5,  10, 18, 25), "soil_moisture": (10, 20, 40, 60)},

    ("basil",       "seedling"):   {"soil_temp": (16, 21, 27, 32), "soil_moisture": (40, 50, 70, 85)},
    ("basil",       "vegetative"): {"soil_temp": (13, 21, 27, 32), "soil_moisture": (25, 40, 60, 80)},
    ("basil",       "flowering"):  {"soil_temp": (16, 21, 27, 32), "soil_moisture": (25, 40, 60, 80)},
    # basil-fruiting copies vegetative (= veg in source table).
    ("basil",       "fruiting"):   {"soil_temp": (13, 21, 27, 32), "soil_moisture": (25, 40, 60, 80)},
    ("basil",       "dormant"):    {"soil_temp": (10, 15, 20, 25), "soil_moisture": (10, 20, 40, 60)},

    ("lettuce",     "seedling"):   {"soil_temp": (5,  15, 21, 27), "soil_moisture": (50, 60, 80, 90)},
    ("lettuce",     "vegetative"): {"soil_temp": (5,  13, 21, 24), "soil_moisture": (30, 50, 70, 85)},
    # lettuce flowering + fruiting copy vegetative (= veg in source).
    ("lettuce",     "flowering"):  {"soil_temp": (5,  13, 21, 24), "soil_moisture": (30, 50, 70, 85)},
    ("lettuce",     "fruiting"):   {"soil_temp": (5,  13, 21, 24), "soil_moisture": (30, 50, 70, 85)},
    ("lettuce",     "dormant"):    {"soil_temp": (0,  5,  10, 15), "soil_moisture": (10, 20, 40, 60)},

    ("microgreens", "seedling"):   {"soil_temp": (10, 18, 24, 27), "soil_moisture": (60, 70, 85, 95)},
    ("microgreens", "vegetative"): {"soil_temp": (10, 18, 24, 27), "soil_moisture": (40, 60, 80, 90)},
    # Microgreens have no real flowering/fruiting/dormancy — copy vegetative.
    ("microgreens", "flowering"):  {"soil_temp": (10, 18, 24, 27), "soil_moisture": (40, 60, 80, 90)},
    ("microgreens", "fruiting"):   {"soil_temp": (10, 18, 24, 27), "soil_moisture": (40, 60, 80, 90)},
    ("microgreens", "dormant"):    {"soil_temp": (10, 18, 24, 27), "soil_moisture": (40, 60, 80, 90)},

    # "generic" is the per-phase fallback used when a unit's plant_type
    # has no seeded row. The values are a broad "most plants will be
    # ok" envelope — wider than any specific plant.
    ("generic",     "seedling"):   {"soil_temp": (10, 18, 26, 32), "soil_moisture": (35, 50, 75, 90)},
    ("generic",     "vegetative"): {"soil_temp": (10, 18, 26, 32), "soil_moisture": (20, 35, 65, 85)},
    ("generic",     "flowering"):  {"soil_temp": (10, 18, 26, 32), "soil_moisture": (25, 40, 65, 85)},
    ("generic",     "fruiting"):   {"soil_temp": (10, 18, 26, 32), "soil_moisture": (30, 45, 70, 85)},
    ("generic",     "dormant"):    {"soil_temp": (5,  10, 18, 25), "soil_moisture": (10, 20, 40, 60)},
}


def _seed_grow_data(cur):
    """Idempotent: only inserts rows that aren't already present."""
    # Plant profiles — INSERT OR IGNORE per row so adding a new entry to
    # _SHIPPED_PROFILES later (e.g. chili) lands on existing DBs without
    # clobbering any profile a user has edited (UNIQUE(plant_type, phase)
    # makes the conflict resolution per-row).
    for row in _SHIPPED_PROFILES:
        cur.execute(
            "INSERT OR IGNORE INTO grow_plant_profiles "
            "(plant_type, phase, target_moisture_pct, deadband_pct, "
            " kp, ki, kd, min_pulse_s, max_pulse_s, soak_window_min, "
            " default_light_hours, is_shipped) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
            row,
        )

    # Plant-happiness thresholds. Two-stage process:
    #   1. Ensure a base row exists for every (plant_type, phase) in
    #      THRESHOLD_SEEDS. Some pairs (e.g. *-dormant for every plant,
    #      lettuce-flowering, microgreens-fruiting) aren't in
    #      _SHIPPED_PROFILES because they had no PID/profile values to
    #      seed yet. INSERT OR IGNORE pulls the target_moisture_pct
    #      from the matching specific row when available, falling back
    #      to a sensible 50 % default for phases nobody has tuned yet.
    #   2. UPDATE the threshold columns on every row. Per the spec
    #      these columns are brand new — no operator has had a chance
    #      to edit them yet — so an unconditional UPDATE is safe and
    #      keeps fresh installs + existing-deployment migrations on
    #      the same code path.
    for (plant_type, phase), thresholds in THRESHOLD_SEEDS.items():
        # Default target_moisture_pct for newly-created base rows: a
        # mid-range 50 %. Phases that already have a profile row from
        # _SHIPPED_PROFILES skip this INSERT entirely (UNIQUE conflict
        # → IGNORE). The thresholds-only rows are populated only so
        # the API can SELECT them when a unit is on (e.g.) tomato-
        # dormant; they won't drive any watering decisions because
        # dormant units shouldn't be on a watering schedule anyway.
        cur.execute(
            "INSERT OR IGNORE INTO grow_plant_profiles "
            "(plant_type, phase, target_moisture_pct, is_shipped) "
            "VALUES (?, ?, ?, 1)",
            (plant_type, phase, 50),
        )
        st = thresholds["soil_temp"]
        sm = thresholds["soil_moisture"]
        cur.execute(
            "UPDATE grow_plant_profiles SET "
            " soil_temp_critical_min_c=?, soil_temp_ideal_min_c=?, "
            " soil_temp_ideal_max_c=?, soil_temp_critical_max_c=?, "
            " soil_moisture_critical_min_pct=?, soil_moisture_ideal_min_pct=?, "
            " soil_moisture_ideal_max_pct=?, soil_moisture_critical_max_pct=? "
            "WHERE plant_type=? AND phase=?",
            (*st, *sm, plant_type, phase),
        )

    # Medium calibration defaults
    for mt, dry, wet in _SHIPPED_MEDIUMS:
        cur.execute(
            "INSERT OR IGNORE INTO grow_medium_defaults (medium_type, dry_raw, wet_raw) "
            "VALUES (?, ?, ?)",
            (mt, dry, wet),
        )

    # app_settings keys
    defaults = {
        "grow_default_soak_window_min": "30",
        "grow_default_buffer_retention_days": "7",
        "grow_disk_warn_pct": "90",
        "grow_holiday_mode": "0",
        "grow_images_dir": "",  # empty = use env var or built-in default
        # Phase 3 Task 3: Diagnostics tab uses this to mark a sensor stale
        # when (now - last_seen_at) exceeds the threshold. Stored as a
        # string per the existing app_settings convention; the endpoint
        # casts to float and falls back to 5 on parse failure.
        "grow_sensor_stale_threshold_min": "5",
    }
    for k, v in defaults.items():
        cur.execute(
            "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)",
            (k, v),
        )

    # Enrollment key — generate once, argon2-hashed so verify_enrollment_key
    # (mlss_monitor.grow.auth) can validate it. argon2-cffi is available since
    # Task 3.1 added the dep to pyproject.toml.
    cur.execute("SELECT COUNT(*) FROM app_settings WHERE key='grow_enrollment_key_hash'")
    if cur.fetchone()[0] == 0:
        raw_key = secrets.token_urlsafe(32)
        key_hash = _seed_hasher.hash(raw_key)
        cur.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?)",
            ("grow_enrollment_key_hash", key_hash),
        )
        # Stash raw key so the install UI can show it once.
        cur.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
            ("grow_enrollment_key_raw_pending_reveal", raw_key),
        )
