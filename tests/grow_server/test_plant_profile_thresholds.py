"""Plant-happiness threshold columns on grow_plant_profiles.

Covers:
  * the 8 new threshold columns exist on grow_plant_profiles
  * the values in THRESHOLD_SEEDS land on the rows after create_db()
  * the migration helper (_add_column_if_missing) brings an older DB
    (one whose grow_plant_profiles predates the threshold columns) up
    to the current schema without losing existing data.
"""
import sqlite3

import pytest

from database.init_db import create_db


_THRESHOLD_COLS = {
    "soil_temp_critical_min_c",
    "soil_temp_ideal_min_c",
    "soil_temp_ideal_max_c",
    "soil_temp_critical_max_c",
    "soil_moisture_critical_min_pct",
    "soil_moisture_ideal_min_pct",
    "soil_moisture_ideal_max_pct",
    "soil_moisture_critical_max_pct",
}


@pytest.fixture
def db_path(monkeypatch, tmp_path):
    path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", path)
    create_db()
    return path


def _columns(db_path, table):
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    finally:
        conn.close()
    return {row[1] for row in rows}


def test_threshold_columns_exist(db_path):
    """All 8 happiness-threshold columns must be present after init."""
    cols = _columns(db_path, "grow_plant_profiles")
    missing = _THRESHOLD_COLS - cols
    assert not missing, f"missing threshold columns: {missing}"


def test_chili_vegetative_seeded(db_path):
    """Pick a representative (plant_type, phase) and assert the
    threshold tuple matches THRESHOLD_SEEDS verbatim."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT soil_temp_critical_min_c, soil_temp_ideal_min_c, "
        "       soil_temp_ideal_max_c, soil_temp_critical_max_c, "
        "       soil_moisture_critical_min_pct, soil_moisture_ideal_min_pct, "
        "       soil_moisture_ideal_max_pct, soil_moisture_critical_max_pct "
        "FROM grow_plant_profiles "
        "WHERE plant_type='chili' AND phase='vegetative'"
    ).fetchone()
    conn.close()
    assert row is not None
    # THRESHOLD_SEEDS[("chili","vegetative")] =
    #   soil_temp:    (13, 21, 27, 32)
    #   soil_moisture:(20, 35, 60, 85)
    assert (row["soil_temp_critical_min_c"], row["soil_temp_ideal_min_c"],
            row["soil_temp_ideal_max_c"], row["soil_temp_critical_max_c"]) \
        == (13, 21, 27, 32)
    assert (row["soil_moisture_critical_min_pct"],
            row["soil_moisture_ideal_min_pct"],
            row["soil_moisture_ideal_max_pct"],
            row["soil_moisture_critical_max_pct"]) \
        == (20, 35, 60, 85)


def test_tomato_fruiting_seeded(db_path):
    """Second representative — tomato-fruiting's wider critical_max
    on soil_temp (35 °C — heat-tolerance for ripening) is the kind of
    value that would silently get clobbered if the seed loop ever
    accidentally used a non-specific UPDATE."""
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT soil_temp_critical_max_c, soil_moisture_critical_max_pct "
        "FROM grow_plant_profiles "
        "WHERE plant_type='tomato' AND phase='fruiting'"
    ).fetchone()
    conn.close()
    assert row == (35, 90)


def test_generic_dormant_seeded(db_path):
    """Generic-dormant is the bottom of the fallback chain in the API
    (custom plant + custom phase → falls back to generic + phase).
    Confirm the row exists and has thresholds — without this, the API
    fallback returns no happiness signal at all for the most common
    'unknown plant_type' case."""
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT soil_temp_ideal_min_c, soil_moisture_ideal_max_pct "
        "FROM grow_plant_profiles "
        "WHERE plant_type='generic' AND phase='dormant'"
    ).fetchone()
    conn.close()
    assert row == (10, 40)


def test_migration_adds_columns_to_existing_db(tmp_path, monkeypatch):
    """Simulate an older DB whose grow_plant_profiles was created
    without the threshold columns, then verify a fresh create_db()
    run adds them and seeds values without losing the original row."""
    path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", path)

    # Build a stripped-down grow_plant_profiles WITHOUT the threshold
    # columns. We can't easily replay an "old" full schema dump from
    # source control here, so we hand-roll just the table this test
    # cares about + the bare minimum to let create_db() run end-to-end.
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE grow_plant_profiles (
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
        )
    """)
    # Seed a row the test user might already have customised. After
    # migration, target_moisture_pct must still be 77.
    conn.execute(
        "INSERT INTO grow_plant_profiles "
        "(plant_type, phase, target_moisture_pct, is_shipped) "
        "VALUES ('chili', 'vegetative', 77, 1)"
    )
    conn.commit()
    conn.close()

    # Confirm the table was built without the new columns.
    pre_cols = _columns(path, "grow_plant_profiles")
    assert _THRESHOLD_COLS.isdisjoint(pre_cols), (
        "pre-migration fixture leaked the new columns — adjust the "
        "fixture so this test actually exercises the ALTER path"
    )

    # Run the full create_db; this should ALTER the table and seed.
    create_db()

    post_cols = _columns(path, "grow_plant_profiles")
    assert _THRESHOLD_COLS <= post_cols, "ALTER didn't add all 8 columns"

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT target_moisture_pct, soil_temp_ideal_min_c "
        "FROM grow_plant_profiles "
        "WHERE plant_type='chili' AND phase='vegetative'"
    ).fetchone()
    conn.close()
    # Pre-migration user value preserved + new column populated from
    # THRESHOLD_SEEDS in the same migration pass.
    assert row["target_moisture_pct"] == 77
    assert row["soil_temp_ideal_min_c"] == 21


def test_dormant_rows_present_for_all_plants(db_path):
    """THRESHOLD_SEEDS has a *-dormant row for every plant_type.
    _SHIPPED_PROFILES does not — so the seed loop must materialise
    these rows on its own (otherwise the dormant-phase API lookup
    would silently miss for every plant)."""
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT plant_type FROM grow_plant_profiles WHERE phase='dormant'"
    ).fetchall()
    conn.close()
    plants = {r[0] for r in rows}
    expected = {"chili", "pepper", "tomato", "basil", "lettuce",
                "microgreens", "generic"}
    assert expected <= plants
