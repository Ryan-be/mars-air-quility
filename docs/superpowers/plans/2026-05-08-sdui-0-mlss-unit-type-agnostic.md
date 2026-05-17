# SDUI Sub-project ⓪: MLSS becomes unit-type-agnostic — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename + generalise MLSS's grow-specific plumbing (tables, routes, templates, fleet view, +Add Unit modal, Plant Profiles gate) so the codebase isn't grow-specific. Zero user-visible change; foundation for SDUI sub-projects ①–⑧.

**Architecture:** Three layered additions:
1. **Compatibility-view DB rename**: new canonical names (`units`, `unit_capabilities`, `unit_photos`, `unit_errors`) are created as `CREATE VIEW … AS SELECT … FROM grow_*` so all existing SELECT/INSERT code keeps working unchanged. Writers continue against the legacy tables; readers can use either name. This lets us cut over routes/templates incrementally without a single big-bang migration. The physical rename (DROP VIEW + ALTER TABLE RENAME) happens at the end once nothing references the legacy names.
2. **Additive columns + new tables**: `unit_type` (default `'grow'`), `unit_roles`, `unit_types`, `unit_audit_log` — all behind idempotent `ALTER TABLE … ADD COLUMN` / `CREATE TABLE IF NOT EXISTS` so the migration converges on re-run.
3. **Generalised routes/templates with aliases**: `/api/units/<id>/…` and `/units/<id>` become canonical; the `/api/grow/units/<id>/…` and `/grow/<id>` paths stay registered as redirects/aliases for one minor release.

**Tech Stack:** Same as existing — Flask + SQLite + vanilla ES modules + pydantic; pytest for Python tests; `node --test` for JS tests under `tests/js/`.

**Estimated effort:** 2-3 engineer days. Roughly 20 tasks, mostly mechanical, organised into seven phases.

**Critical scoping decision (call locked here):** `static/js/grow/` and `templates/grow_*.html` for the unit *detail* page stay named `grow_*` during ⓪. SDUI sub-projects ②/③/④ will rewrite those modules wholesale (the bespoke JS is being deleted, replaced by the SDUI renderer). Renaming them during ⓪ would create churn that ④ instantly undoes. The fleet page (`grow_fleet.html`) IS renamed because it gains generalised-fleet behaviour (group-by-unit-type) in this sub-project and so is touched anyway.

---

## File Structure — what gets created, modified, renamed

### Created
- `database/unit_schema.py` — new tables + views for the generalised schema (`unit_roles`, `unit_types`, `unit_audit_log`, compatibility views). Sits beside `grow_schema.py` and is called from `init_db.create_db()`.
- `mlss_monitor/routes/api_units.py` — new canonical `/api/units/<id>/…` endpoints. v0 implementation is a thin layer that calls into the existing `api_grow_units` functions; deprecated alias routes live at `api_grow_units` and redirect to `api_units`.
- `mlss_monitor/units/__init__.py`, `mlss_monitor/units/registry.py` — helpers for reading `unit_types` + `unit_roles`.
- `templates/unit_fleet.html` — replaces `grow_fleet.html`; renders all unit types grouped by `unit_type` with role-filter chips. `grow_fleet.html` becomes a 1-line `{% include "unit_fleet.html" %}` stub for one release.
- `templates/_unit_subnav.html` — generalised version of `_grow_subnav.html` (still grow-only in content at ⓪'s end; structure ready for future unit types).
- `static/js/units/unit_fleet.mjs` — replaces `static/js/grow/fleet.mjs` for the fleet view. Adds `groupByUnitType()` + `roleFilterChips()`. Imports the existing `grow-card.mjs` unchanged.
- `static/js/units/add-unit-modal.mjs` — wraps the existing `static/js/grow/components/add-unit-modal.mjs` with a "unit type picker" step (only `grow` available initially). The wrapper imports + delegates to the existing modal once the type is selected.
- `tests/units/` — new test directory for unit-type-agnostic tests (mirrors `tests/grow_server/` layout). `tests/units/test_unit_schema.py`, `tests/units/test_unit_audit_log.py`, `tests/units/test_units_api.py`, `tests/units/test_unit_fleet_page.py`, `tests/units/test_unit_types_registry.py`, `tests/units/test_unit_roles.py`, `tests/units/test_plant_profiles_gating.py`, `tests/units/test_legacy_route_redirects.py`.
- `tests/js/test_unit_fleet.mjs`, `tests/js/test_add_unit_modal_with_type_picker.mjs`.

### Modified
- `database/init_db.py` — call `create_unit_schema(cur)` after `create_grow_schema(cur)`; add idempotent ALTERs for `unit_type` column on `grow_units` + indexes.
- `mlss_monitor/routes/__init__.py` — register `api_units_bp`.
- `mlss_monitor/routes/pages.py` — add `/units` (canonical fleet), `/units/<id>` (canonical detail), redirect `/grow` → `/units?type=grow`, redirect `/grow/<id>` → `/units/<id>`. The legacy `/grow*` page routes stay registered (302 redirects).
- `templates/base.html` — top-nav "Grow" label stays (it's still grow-only content); URLs updated to use `pages.unit_fleet` / `pages.unit_detail` endpoint names via `url_for`. Backwards-compat shim aliases the old endpoint names.
- `templates/grow_settings.html` — gate the plant-profiles section behind a server-passed flag `show_plant_profiles=True` (the page already only runs for admin, so server-side we set the flag based on whether any unit has `unit_type='grow'`).

### Renamed (logical rename via aliases, then physical at end)
- `grow_units` table → `units` (via compat view through Tasks 2-18, physical rename in Task 19).
- `grow_unit_capabilities` → `unit_capabilities` (same pattern).
- `grow_photos` → `unit_photos` (same pattern).
- `grow_errors` → `unit_errors` (same pattern).
- `templates/grow_fleet.html` → `templates/unit_fleet.html` (the legacy filename stays as a 1-line stub).

### NOT renamed (deferred to SDUI sub-projects ②/③/④)
- `static/js/grow/components/*.mjs` (28 component modules — the SDUI renderer replaces nearly all of them in ②/③).
- `templates/grow_unit_detail.html` (replaced wholesale by the SDUI renderer in ④).
- `templates/grow_errors.html`, `templates/grow_settings.html` (touched only for the plant-profiles gating).
- `grow_watering_events`, `grow_plant_profiles`, `grow_light_windows`, `grow_medium_defaults`, `grow_journal_entries`, `grow_timelapse_jobs` (all action-log / grow-specific configuration tables — fine to stay typed as the spec notes).

---

## Phase 1 — Compatibility-view schema rename (zero behaviour change)

### Task 1: Add `unit_type` column to `grow_units`

**Files:**
- Modify: `database/init_db.py`
- Test: `tests/units/test_unit_schema.py` (new)

- [ ] **Step 1: Create the test file with a failing test**

Write `tests/units/test_unit_schema.py`:

```python
"""Schema migration tests for SDUI sub-project ⓪: unit-type-agnostic refactor."""
import sqlite3
from database.init_db import create_db


def _columns(db_path, table):
    conn = sqlite3.connect(db_path)
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    conn.close()
    return {row[1]: row[2] for row in rows}


def test_grow_units_has_unit_type_column_defaulting_to_grow(monkeypatch, tmp_path):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)
    create_db()
    cols = _columns(db_path, "grow_units")
    assert "unit_type" in cols
    assert cols["unit_type"].upper() == "TEXT"

    # Seed a row WITHOUT explicit unit_type — the default must populate.
    conn = sqlite3.connect(db_path)
    from datetime import datetime
    now = datetime.utcnow()
    conn.execute(
        "INSERT INTO grow_units (hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("hw-test", "Test Unit", now, "hash", now),
    )
    conn.commit()
    row = conn.execute(
        "SELECT unit_type FROM grow_units WHERE hardware_serial='hw-test'"
    ).fetchone()
    conn.close()
    assert row[0] == "grow"
```

Also create `tests/units/__init__.py` (empty file) so pytest discovers the package.

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/units/test_unit_schema.py::test_grow_units_has_unit_type_column_defaulting_to_grow -v`
Expected: FAIL — column `unit_type` does not exist on `grow_units`.

- [ ] **Step 3: Add the migration**

In `database/init_db.py`, append to the migrations list (around line 290, before the closing `]:`):

```python
# SDUI sub-project ⓪: unit-type-agnostic refactor. Every existing
# grow_units row is implicitly a grow unit. Default 'grow' makes the
# migration idempotent and means INSERTs that pre-date this column
# keep working without modification.
"ALTER TABLE grow_units ADD COLUMN unit_type TEXT NOT NULL DEFAULT 'grow'",
"CREATE INDEX IF NOT EXISTS idx_grow_units_type ON grow_units(unit_type)",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/units/test_unit_schema.py::test_grow_units_has_unit_type_column_defaulting_to_grow -v`
Expected: PASS.

Also run the existing schema test to confirm no regression:
Run: `poetry run pytest tests/grow_server/test_grow_schema_units.py -v`
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add database/init_db.py tests/units/__init__.py tests/units/test_unit_schema.py
git commit -m "SDUI ⓪ Task 1: add unit_type column to grow_units (defaults to 'grow')"
```

---

### Task 2: Create `unit_roles` table (many-to-many tag store)

**Files:**
- Create: `database/unit_schema.py`
- Modify: `database/init_db.py`
- Test: `tests/units/test_unit_roles.py` (new)

- [ ] **Step 1: Write the failing test**

Append to a new `tests/units/test_unit_roles.py`:

```python
"""unit_roles many-to-many tag table."""
import sqlite3
from datetime import datetime
from database.init_db import create_db


def _columns(db_path, table):
    conn = sqlite3.connect(db_path)
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    conn.close()
    return {row[1]: row[2] for row in rows}


def test_unit_roles_table_exists(monkeypatch, tmp_path):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)
    create_db()
    cols = _columns(db_path, "unit_roles")
    assert "unit_id" in cols
    assert "role" in cols
    assert "assigned_by" in cols
    assert "assigned_at" in cols


def test_unit_roles_allows_multiple_roles_per_unit(monkeypatch, tmp_path):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)
    create_db()
    now = datetime.utcnow()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (?, ?, ?, ?, ?, ?)",
        (10, "hw-roles", "U10", now, "h", now),
    )
    conn.execute(
        "INSERT INTO unit_roles (unit_id, role, assigned_by, assigned_at) "
        "VALUES (?, ?, ?, ?)",
        (10, "kitchen", "user:wolf", now),
    )
    conn.execute(
        "INSERT INTO unit_roles (unit_id, role, assigned_by, assigned_at) "
        "VALUES (?, ?, ?, ?)",
        (10, "co_located_with_weather_1", "fusion:rain_throttle", now),
    )
    conn.commit()
    rows = conn.execute(
        "SELECT role FROM unit_roles WHERE unit_id=10 ORDER BY role"
    ).fetchall()
    conn.close()
    assert [r[0] for r in rows] == [
        "co_located_with_weather_1", "kitchen"
    ]


def test_unit_roles_primary_key_prevents_duplicate_role(monkeypatch, tmp_path):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)
    create_db()
    now = datetime.utcnow()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (?, ?, ?, ?, ?, ?)",
        (11, "hw-dupe", "U11", now, "h", now),
    )
    conn.execute(
        "INSERT INTO unit_roles (unit_id, role, assigned_by, assigned_at) "
        "VALUES (?, ?, ?, ?)",
        (11, "kitchen", "user:wolf", now),
    )
    try:
        conn.execute(
            "INSERT INTO unit_roles (unit_id, role, assigned_by, assigned_at) "
            "VALUES (?, ?, ?, ?)",
            (11, "kitchen", "user:wolf", now),
        )
        conn.commit()
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    conn.close()
    assert raised, "duplicate (unit_id, role) should be rejected"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/units/test_unit_roles.py -v`
Expected: FAIL with "no such table: unit_roles".

- [ ] **Step 3: Create `database/unit_schema.py`**

```python
"""Unit-type-agnostic schema additions for SDUI sub-project ⓪.

Created alongside grow_schema.py so the existing grow tables keep
their CREATE-TABLE pattern unchanged. Called from init_db.create_db()
after create_grow_schema so foreign keys to grow_units (and later, to
the renamed `units` table) work.
"""


def create_unit_schema(cur):
    """Create unit-type-agnostic tables. Idempotent."""
    # Many-to-many tag store. assigned_by distinguishes
    # operator-assigned roles ("user:wolf") from auto-assigned ones
    # ("fusion:rain_throttle") — the fusion rule engine in a later
    # sub-project adds rows here when its trigger fires.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS unit_roles (
      unit_id     INTEGER NOT NULL REFERENCES grow_units(id) ON DELETE CASCADE,
      role        TEXT NOT NULL,
      assigned_by TEXT NOT NULL,
      assigned_at DATETIME NOT NULL,
      PRIMARY KEY (unit_id, role)
    );
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_unit_roles_role "
        "ON unit_roles(role)"
    )
```

- [ ] **Step 4: Wire `create_unit_schema` into `init_db.py`**

Modify `database/init_db.py` after the `create_grow_schema(cur)` call (around line 418):

```python
    # Plant Grow Unit tables (Phase 1)
    create_grow_schema(cur)

    # SDUI sub-project ⓪: unit-type-agnostic plumbing
    from database.unit_schema import create_unit_schema
    create_unit_schema(cur)

    conn.commit()
    conn.close()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/units/test_unit_roles.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add database/unit_schema.py database/init_db.py tests/units/test_unit_roles.py
git commit -m "SDUI ⓪ Task 2: add unit_roles many-to-many tag table"
```

---

### Task 3: Create `unit_types` registry table

**Files:**
- Modify: `database/unit_schema.py`
- Test: `tests/units/test_unit_types_registry.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/units/test_unit_types_registry.py`:

```python
"""unit_types registry — one row per declared unit type."""
import sqlite3
from datetime import datetime
from database.init_db import create_db


def _columns(db_path, table):
    conn = sqlite3.connect(db_path)
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    conn.close()
    return {row[1]: row[2] for row in rows}


def test_unit_types_table_exists(monkeypatch, tmp_path):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)
    create_db()
    cols = _columns(db_path, "unit_types")
    assert "unit_type" in cols
    assert "last_declaration_json" in cols
    assert "first_seen_at" in cols
    assert "last_seen_at" in cols


def test_unit_types_seeds_grow_row(monkeypatch, tmp_path):
    """Fresh DB has a single 'grow' row in unit_types so existing
    grow units are discoverable through the registry even before any
    boot frame arrives."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)
    create_db()
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT unit_type FROM unit_types").fetchall()
    conn.close()
    assert [r[0] for r in rows] == ["grow"]


def test_unit_types_primary_key_is_unit_type(monkeypatch, tmp_path):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)
    create_db()
    now = datetime.utcnow()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO unit_types (unit_type, first_seen_at, last_seen_at) "
            "VALUES (?, ?, ?)",
            ("grow", now, now),
        )
        conn.commit()
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    conn.close()
    assert raised, "duplicate unit_type primary key should be rejected"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/units/test_unit_types_registry.py -v`
Expected: FAIL with "no such table: unit_types".

- [ ] **Step 3: Extend `database/unit_schema.py`**

Append to `create_unit_schema`:

```python
    # Registry of every unit type that has ever booted. Populated by
    # handle_capabilities (in a later sub-project) when a boot frame
    # arrives; seeded with 'grow' here so the Settings → Units page
    # has a discoverable row even before any unit boots.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS unit_types (
      unit_type             TEXT PRIMARY KEY,
      last_declaration_json TEXT,
      first_seen_at         DATETIME NOT NULL,
      last_seen_at          DATETIME NOT NULL
    );
    """)
    # Seed the 'grow' row idempotently so a fresh DB has at least one
    # known unit type. INSERT OR IGNORE so running create_db() twice
    # doesn't bump first_seen_at.
    from datetime import datetime as _dt
    _now = _dt.utcnow()
    cur.execute(
        "INSERT OR IGNORE INTO unit_types "
        "(unit_type, first_seen_at, last_seen_at) VALUES (?, ?, ?)",
        ("grow", _now, _now),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/units/test_unit_types_registry.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add database/unit_schema.py tests/units/test_unit_types_registry.py
git commit -m "SDUI ⓪ Task 3: add unit_types registry table seeded with 'grow'"
```

---

### Task 4: Create `unit_audit_log` table

**Files:**
- Modify: `database/unit_schema.py`
- Test: `tests/units/test_unit_audit_log.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/units/test_unit_audit_log.py`:

```python
"""unit_audit_log — action invocations, distinct from unit_errors alerts.

Fields per the SDUI spec sub-project ⓪ section:
  id, ts, unit_id, user, role, action_name, args_json, result,
  jwt_signature, source (∈ {'mlss', 'pi-fallback-replay'}).
"""
import sqlite3
from datetime import datetime
from database.init_db import create_db


def _columns(db_path, table):
    conn = sqlite3.connect(db_path)
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    conn.close()
    return {row[1]: row[2] for row in rows}


def test_unit_audit_log_has_expected_columns(monkeypatch, tmp_path):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)
    create_db()
    cols = _columns(db_path, "unit_audit_log")
    assert "id" in cols
    assert "ts" in cols
    assert "unit_id" in cols
    assert "user" in cols
    assert "role" in cols
    assert "action_name" in cols
    assert "args_json" in cols
    assert "result" in cols
    assert "jwt_signature" in cols
    assert "source" in cols


def test_unit_audit_log_source_check_constraint(monkeypatch, tmp_path):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)
    create_db()
    now = datetime.utcnow()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (?, ?, ?, ?, ?, ?)",
        (20, "hw-audit", "U20", now, "h", now),
    )
    # Valid source values must succeed.
    for source in ("mlss", "pi-fallback-replay"):
        conn.execute(
            "INSERT INTO unit_audit_log "
            "(ts, unit_id, user, role, action_name, args_json, "
            " result, jwt_signature, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (now, 20, "u", "admin", "water_now", '{"d":5}', "ok", "sig", source),
        )
    conn.commit()
    # Invalid source must fail.
    try:
        conn.execute(
            "INSERT INTO unit_audit_log "
            "(ts, unit_id, user, role, action_name, args_json, "
            " result, jwt_signature, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (now, 20, "u", "admin", "water_now", '{"d":5}', "ok", "sig", "bad"),
        )
        conn.commit()
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    conn.close()
    assert raised, "source='bad' should violate CHECK constraint"


def test_unit_audit_log_has_index_on_unit_id_ts(monkeypatch, tmp_path):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)
    create_db()
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='index' AND tbl_name='unit_audit_log'"
    ).fetchall()
    conn.close()
    names = {r[0] for r in rows}
    assert "idx_unit_audit_log_unit_time" in names
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/units/test_unit_audit_log.py -v`
Expected: FAIL — no such table.

- [ ] **Step 3: Extend `database/unit_schema.py`**

Append to `create_unit_schema`:

```python
    # Action audit log — distinct from unit_errors (which captures
    # firmware-emitted alerts). Audit rows have RBAC + JWT context for
    # non-repudiation; source distinguishes a normal MLSS-routed
    # action from one replayed from a Pi's pending_audit queue after
    # a tactical-fallback episode (later sub-project).
    cur.execute("""
    CREATE TABLE IF NOT EXISTS unit_audit_log (
      id            INTEGER PRIMARY KEY AUTOINCREMENT,
      ts            DATETIME NOT NULL,
      unit_id       INTEGER NOT NULL REFERENCES grow_units(id) ON DELETE CASCADE,
      user          TEXT NOT NULL,
      role          TEXT NOT NULL,
      action_name   TEXT NOT NULL,
      args_json     TEXT NOT NULL,
      result        TEXT,
      jwt_signature TEXT NOT NULL,
      source        TEXT NOT NULL
                      CHECK(source IN ('mlss', 'pi-fallback-replay'))
    );
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_unit_audit_log_unit_time "
        "ON unit_audit_log(unit_id, ts DESC)"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/units/test_unit_audit_log.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add database/unit_schema.py tests/units/test_unit_audit_log.py
git commit -m "SDUI ⓪ Task 4: add unit_audit_log table with source CHECK constraint"
```

---

### Task 5: Compatibility views — `units`, `unit_capabilities`, `unit_photos`, `unit_errors`

**Files:**
- Modify: `database/unit_schema.py`
- Test: `tests/units/test_unit_schema.py` (extend)

The views are read-only proxies for the existing grow_* tables. Writers continue against the legacy names; readers can use either name. This is the trick that lets us rename routes/templates without touching every SELECT in the codebase first.

- [ ] **Step 1: Add the failing test**

Append to `tests/units/test_unit_schema.py`:

```python
def test_units_view_proxies_grow_units(monkeypatch, tmp_path):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)
    create_db()
    from datetime import datetime
    now = datetime.utcnow()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (?, ?, ?, ?, ?, ?)",
        (100, "hw-view", "ViewUnit", now, "h", now),
    )
    conn.commit()
    rows = conn.execute(
        "SELECT id, label, unit_type FROM units WHERE id=100"
    ).fetchall()
    conn.close()
    assert rows == [(100, "ViewUnit", "grow")]


def test_unit_capabilities_view_proxies_grow_unit_capabilities(
    monkeypatch, tmp_path,
):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)
    create_db()
    from datetime import datetime
    now = datetime.utcnow()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (?, ?, ?, ?, ?, ?)",
        (101, "hw-cap", "CapUnit", now, "h", now),
    )
    conn.execute(
        "INSERT INTO grow_unit_capabilities "
        "(unit_id, channel, is_required, installed_at) "
        "VALUES (?, ?, ?, ?)",
        (101, "soil_moisture", 1, now),
    )
    conn.commit()
    rows = conn.execute(
        "SELECT unit_id, channel FROM unit_capabilities WHERE unit_id=101"
    ).fetchall()
    conn.close()
    assert rows == [(101, "soil_moisture")]


def test_unit_photos_view_proxies_grow_photos(monkeypatch, tmp_path):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)
    create_db()
    from datetime import datetime
    now = datetime.utcnow()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (?, ?, ?, ?, ?, ?)",
        (102, "hw-photo", "PhotoUnit", now, "h", now),
    )
    conn.execute(
        "INSERT INTO grow_photos "
        "(unit_id, taken_at, file_path, width_px, height_px, size_bytes) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (102, now, "/tmp/p.jpg", 640, 480, 12345),
    )
    conn.commit()
    rows = conn.execute(
        "SELECT unit_id, file_path FROM unit_photos WHERE unit_id=102"
    ).fetchall()
    conn.close()
    assert rows == [(102, "/tmp/p.jpg")]


def test_unit_errors_view_proxies_grow_errors(monkeypatch, tmp_path):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)
    create_db()
    from datetime import datetime
    now = datetime.utcnow()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (?, ?, ?, ?, ?, ?)",
        (103, "hw-err", "ErrUnit", now, "h", now),
    )
    conn.execute(
        "INSERT INTO grow_errors "
        "(unit_id, timestamp_utc, severity, kind, message) "
        "VALUES (?, ?, ?, ?, ?)",
        (103, now, "warning", "offline", "unit offline"),
    )
    conn.commit()
    rows = conn.execute(
        "SELECT unit_id, kind FROM unit_errors WHERE unit_id=103"
    ).fetchall()
    conn.close()
    assert rows == [(103, "offline")]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/units/test_unit_schema.py -v -k view`
Expected: FAIL — "no such table: units".

- [ ] **Step 3: Add the views to `database/unit_schema.py`**

Append to `create_unit_schema`:

```python
    # ── Compatibility views ────────────────────────────────────────────
    # Read-only proxies for the existing grow_* tables. Writers continue
    # to target grow_*; readers can use either name. The physical RENAME
    # happens in Task 19 once nothing in the codebase references grow_*
    # any more. Views are DROP-then-CREATE so re-running create_db() on
    # a partially-migrated DB converges (CREATE VIEW IF NOT EXISTS is a
    # no-op even if the underlying table shape changed, which would
    # leave a stale projection).
    for view in (
        "units", "unit_capabilities", "unit_photos", "unit_errors",
    ):
        cur.execute(f"DROP VIEW IF EXISTS {view}")
    cur.execute("CREATE VIEW units AS SELECT * FROM grow_units")
    cur.execute(
        "CREATE VIEW unit_capabilities AS SELECT * FROM grow_unit_capabilities"
    )
    cur.execute("CREATE VIEW unit_photos AS SELECT * FROM grow_photos")
    cur.execute("CREATE VIEW unit_errors AS SELECT * FROM grow_errors")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/units/test_unit_schema.py -v`
Expected: all view tests pass.

Run the full grow_server test suite to confirm no regression:
Run: `poetry run pytest tests/grow_server/ -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add database/unit_schema.py tests/units/test_unit_schema.py
git commit -m "SDUI ⓪ Task 5: add compatibility views (units/unit_capabilities/unit_photos/unit_errors)"
```

---

## Phase 2 — Canonical `/api/units/<id>/…` route layer

### Task 6: Add `api_units_bp` blueprint with the GET endpoints

**Files:**
- Create: `mlss_monitor/routes/api_units.py`
- Modify: `mlss_monitor/routes/__init__.py`
- Test: `tests/units/test_units_api.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/units/test_units_api.py`:

```python
"""GET /api/units (list) and /api/units/<id> (detail) — canonical names.

These wrap the existing api_grow_units handlers so behaviour is
identical; the test exists to lock in the URL surface for the SDUI
sub-projects that follow.
"""
import sqlite3
from datetime import datetime

import pytest


@pytest.fixture
def client(monkeypatch, tmp_path):
    db_path = str(tmp_path / "test.db")
    import database.init_db as init_db
    init_db.DB_FILE = db_path
    monkeypatch.setattr("mlss_monitor.grow.auth.DB_FILE", db_path)
    init_db.create_db()

    now = datetime.utcnow()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at, last_seen_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (1, "hw-1", "Tomato 1", now, "h", now, now),
    )
    conn.commit()
    conn.close()

    from flask import Flask
    from mlss_monitor.routes.api_units import api_units_bp
    from mlss_monitor.routes.api_grow_units import api_grow_units_bp
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_units.DB_FILE", db_path
    )
    monkeypatch.setattr(
        "mlss_monitor.grow.health_watchdog.DB_FILE", db_path
    )
    app = Flask(__name__)
    app.register_blueprint(api_grow_units_bp)
    app.register_blueprint(api_units_bp)
    return app.test_client()


def test_api_units_list_returns_same_shape_as_grow_units(client):
    r_grow = client.get("/api/grow/units")
    r_canonical = client.get("/api/units")
    assert r_grow.status_code == 200
    assert r_canonical.status_code == 200
    assert r_grow.get_json() == r_canonical.get_json()


def test_api_units_detail_returns_same_shape_as_grow_units(client):
    r_grow = client.get("/api/grow/units/1")
    r_canonical = client.get("/api/units/1")
    assert r_grow.status_code == 200
    assert r_canonical.status_code == 200
    assert r_grow.get_json() == r_canonical.get_json()


def test_api_units_list_can_filter_by_unit_type(client):
    """?type=grow returns all units; ?type=weather returns none (no
    weather units exist yet, but the filter must work so non-grow
    fleets in later sub-projects don't see grow units)."""
    r_all = client.get("/api/units?type=grow")
    r_none = client.get("/api/units?type=weather")
    assert r_all.status_code == 200
    assert r_none.status_code == 200
    assert len(r_all.get_json()["units"]) == 1
    assert len(r_none.get_json()["units"]) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/units/test_units_api.py -v`
Expected: FAIL — `api_units` module doesn't exist.

- [ ] **Step 3: Create `mlss_monitor/routes/api_units.py`**

```python
"""Canonical /api/units/<id>/… endpoints.

These delegate to the existing /api/grow/units/<id>/… handlers — the
grow path stays as a deprecated alias for one minor release per the
SDUI sub-project ⓪ deprecation cadence (see PLAN_DEPRECATION below).

PLAN_DEPRECATION: the /api/grow/units/<id>/… alias is removed in the
release AFTER all grow firmware is known to be on a version that uses
/api/units/<id>/… directly. Today (2026-05-08) no firmware calls these
endpoints (firmware uses WS for actions), but third-party tooling
might; hence one release of overlap.
"""
import sqlite3
from flask import Blueprint, jsonify, request

from database.init_db import DB_FILE
from mlss_monitor.routes.api_grow_units import (
    _list_units_impl,
    _get_unit_impl,
)

api_units_bp = Blueprint("api_units", __name__)


@api_units_bp.route("/api/units", methods=["GET"])
def list_units():
    """List all units, optionally filtered by ?type=<unit_type>."""
    unit_type = request.args.get("type")
    return _list_units_impl(unit_type=unit_type)


@api_units_bp.route("/api/units/<int:unit_id>", methods=["GET"])
def get_unit(unit_id):
    return _get_unit_impl(unit_id)
```

This means `api_grow_units.py` needs to expose the implementations as `_list_units_impl` and `_get_unit_impl`. Refactor the existing route bodies into module-level helpers:

In `mlss_monitor/routes/api_grow_units.py`, find the existing `list_units` route handler. Extract its body into a helper:

```python
def _list_units_impl(unit_type: str | None = None):
    """Internal: list units, optionally filtered by unit_type.

    Used by both /api/grow/units (no filter) and /api/units (optional
    ?type=… filter). When unit_type is None, returns all units.
    """
    # ... existing body of the route, but add a WHERE clause when
    # unit_type is provided ...
```

Then the existing route becomes a one-liner:

```python
@api_grow_units_bp.route("/api/grow/units", methods=["GET"])
def list_units():
    # Legacy path — kept as alias for one release. New code should
    # call /api/units instead.
    return _list_units_impl(unit_type="grow")
```

Do the same for `get_unit`. Inside `_list_units_impl`, the SQL becomes:

```python
sql = (
    "SELECT id, hardware_serial, label, ... unit_type "
    "FROM units "  # ← uses the new compatibility view from Task 5
    "WHERE is_active=1 "
)
params: list = []
if unit_type is not None:
    sql += "AND unit_type=? "
    params.append(unit_type)
sql += "ORDER BY label"
rows = conn.execute(sql, params).fetchall()
```

- [ ] **Step 4: Register the blueprint**

Modify `mlss_monitor/routes/__init__.py`:

```python
from .api_units import api_units_bp
```

And in `register_routes`:

```python
    app.register_blueprint(api_units_bp)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/units/test_units_api.py -v`
Expected: PASS (3 tests).

Run the existing grow_units API tests to confirm the refactor didn't break the legacy path:
Run: `poetry run pytest tests/grow_server/test_grow_units_api.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add mlss_monitor/routes/api_units.py mlss_monitor/routes/api_grow_units.py mlss_monitor/routes/__init__.py tests/units/test_units_api.py
git commit -m "SDUI ⓪ Task 6: add canonical /api/units list+detail routes with ?type filter"
```

---

### Task 7: Alias the POST/action endpoints under `/api/units/<id>/…`

**Files:**
- Modify: `mlss_monitor/routes/api_units.py`
- Test: `tests/units/test_units_api.py` (extend)

The action endpoints (identify, water-now, snap-photo, light-toggle, rotate-token, peek-once, soft-delete, clear-buffer, wipe-photos) all stay as thin redirects/aliases that call the existing handlers. SDUI sub-project ②/③ replaces them with a generic `/api/units/<id>/actions/<name>` — for now, ⓪ just makes the canonical-path version work.

- [ ] **Step 1: Write the failing test**

Append to `tests/units/test_units_api.py`:

```python
def test_api_units_action_endpoints_alias_grow_paths(client, monkeypatch):
    """Each /api/grow/units/<id>/<action> POST has an /api/units/<id>/<action>
    twin that behaves identically. We mock the WS push so the test
    doesn't need a real firmware connection — both routes go through
    the same WS-push helper, so a passing call on one is a passing
    call on the other."""
    import mlss_monitor.routes.api_grow_units as agu
    pushed = []
    monkeypatch.setattr(
        agu, "_push_command_async",
        lambda unit_id, cmd: pushed.append((unit_id, cmd)) or True,
    )
    # Stamp admin session on the test client (RBAC gate).
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user_role"] = "admin"
        sess["user"] = "test"

    # Identify — both routes succeed.
    r1 = client.post("/api/grow/units/1/identify",
                     headers={"Origin": "http://localhost"})
    r2 = client.post("/api/units/1/identify",
                     headers={"Origin": "http://localhost"})
    assert r1.status_code == r2.status_code
    assert len(pushed) == 2

    # Water-now — both routes succeed.
    pushed.clear()
    r3 = client.post("/api/grow/units/1/water-now",
                     json={"duration_s": 5},
                     headers={"Origin": "http://localhost"})
    r4 = client.post("/api/units/1/water-now",
                     json={"duration_s": 5},
                     headers={"Origin": "http://localhost"})
    assert r3.status_code == r4.status_code
    assert len(pushed) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/units/test_units_api.py::test_api_units_action_endpoints_alias_grow_paths -v`
Expected: FAIL — 404 on /api/units/1/identify.

- [ ] **Step 3: Add the alias routes to `api_units.py`**

```python
from mlss_monitor.routes.api_grow_units import (
    identify as _grow_identify,
    water_now as _grow_water_now,
    snap_photo as _grow_snap_photo,
    light_toggle as _grow_light_toggle,
    rotate_token as _grow_rotate_token,
    peek_token_once as _grow_peek_token_once,
    soft_delete_unit as _grow_soft_delete_unit,
    clear_buffer as _grow_clear_buffer,
    wipe_photos as _grow_wipe_photos,
)


@api_units_bp.route("/api/units/<int:unit_id>/identify", methods=["POST"])
def identify(unit_id):
    return _grow_identify(unit_id)


@api_units_bp.route("/api/units/<int:unit_id>/water-now", methods=["POST"])
def water_now(unit_id):
    return _grow_water_now(unit_id)


@api_units_bp.route("/api/units/<int:unit_id>/snap-photo", methods=["POST"])
def snap_photo(unit_id):
    return _grow_snap_photo(unit_id)


@api_units_bp.route("/api/units/<int:unit_id>/light-toggle", methods=["POST"])
def light_toggle(unit_id):
    return _grow_light_toggle(unit_id)


@api_units_bp.route(
    "/api/units/<int:unit_id>/rotate-token", methods=["POST"]
)
def rotate_token(unit_id):
    return _grow_rotate_token(unit_id)


@api_units_bp.route(
    "/api/units/<int:unit_id>/token/peek-once", methods=["GET"]
)
def peek_token_once(unit_id):
    return _grow_peek_token_once(unit_id)


@api_units_bp.route("/api/units/<int:unit_id>", methods=["DELETE"])
def soft_delete_unit(unit_id):
    return _grow_soft_delete_unit(unit_id)


@api_units_bp.route(
    "/api/units/<int:unit_id>/clear-buffer", methods=["POST"]
)
def clear_buffer(unit_id):
    return _grow_clear_buffer(unit_id)


@api_units_bp.route("/api/units/<int:unit_id>/photos", methods=["DELETE"])
def wipe_photos(unit_id):
    return _grow_wipe_photos(unit_id)
```

Note: the function names in `api_grow_units.py` may not match exactly — read the current view-function names and adjust the imports above. If a route currently uses a one-shot lambda or anonymous handler, refactor it to a named function first.

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/units/test_units_api.py -v`
Expected: all pass.

Run the existing grow tests to confirm no regression:
Run: `poetry run pytest tests/grow_server/ -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add mlss_monitor/routes/api_units.py mlss_monitor/routes/api_grow_units.py tests/units/test_units_api.py
git commit -m "SDUI ⓪ Task 7: alias action endpoints under /api/units/<id>/… (identify, water-now, etc.)"
```

---

## Phase 3 — Pages, templates, and route aliases

### Task 8: Page route `/units` + `/units/<id>` with `/grow` redirects

**Files:**
- Modify: `mlss_monitor/routes/pages.py`
- Test: `tests/units/test_unit_fleet_page.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/units/test_unit_fleet_page.py`:

```python
"""/units (fleet) and /units/<id> (detail) page routes + /grow redirects."""
import tempfile

import pytest


@pytest.fixture
def client(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    init_db.create_db()
    from mlss_monitor.app import app
    app.config["TESTING"] = True
    return app.test_client()


def _set_session(c, *, logged_in=True, role="admin"):
    with c.session_transaction() as sess:
        sess["logged_in"] = logged_in
        sess["user_role"] = role


def test_units_route_returns_200(client):
    _set_session(client, role="viewer")
    r = client.get("/units")
    assert r.status_code == 200


def test_grow_redirects_to_units_filtered_by_type_grow(client):
    """Legacy /grow → 302 to /units?type=grow so old bookmarks resolve
    to the new canonical fleet view (which renders only grow units
    under that filter)."""
    _set_session(client, role="viewer")
    r = client.get("/grow", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/units?type=grow")


def test_grow_id_redirects_to_units_id(client):
    """Legacy /grow/<int:id> → 302 to /units/<int:id>."""
    _set_session(client, role="viewer")
    r = client.get("/grow/42", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/units/42")


def test_units_id_returns_200_using_existing_detail_template(client):
    """The /units/<id> route renders the same template as /grow/<id>
    did. SDUI sub-project ④ rewrites that template; ⓪ just adds the
    canonical URL."""
    _set_session(client, role="viewer")
    r = client.get("/units/1")
    assert r.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/units/test_unit_fleet_page.py -v`
Expected: FAIL — 404 on /units.

- [ ] **Step 3: Add the canonical routes + redirects in `mlss_monitor/routes/pages.py`**

Add new route handlers:

```python
@pages_bp.route("/units")
def unit_fleet():
    """Canonical fleet view. Renders unit_fleet.html which groups
    units by unit_type. Optionally narrowed by ?type=<unit_type>.

    The grow-specific /grow URL stays registered as a 302 redirect
    so existing bookmarks/links keep working — see grow_fleet_redirect
    below.
    """
    return render_template(
        "unit_fleet.html",
        storage_status=get_storage_status(),
        current_role=session.get("user_role", "viewer"),
        filter_type=request.args.get("type"),
    )


@pages_bp.route("/units/<int:unit_id>")
def unit_detail(unit_id):
    """Canonical unit detail page. Today this still renders
    grow_unit_detail.html (which only supports unit_type='grow'); SDUI
    sub-project ④ replaces it with a generic SDUI-driven shell."""
    return render_template(
        "grow_unit_detail.html",
        unit_id=unit_id,
        current_role=session.get("user_role", "viewer"),
        current_user=session.get("user", ""),
    )
```

Modify the existing `grow_fleet` route to be a redirect:

```python
@pages_bp.route("/grow")
def grow_fleet_redirect():
    """Legacy URL — 302 to /units?type=grow. Kept for one release so
    bookmarks resolve; remove in the release after sub-project ⓪
    ships per PLAN_DEPRECATION in api_units.py."""
    return redirect(url_for("pages.unit_fleet") + "?type=grow", code=302)


@pages_bp.route("/grow/<int:unit_id>")
def grow_unit_detail_redirect(unit_id):
    return redirect(url_for("pages.unit_detail", unit_id=unit_id), code=302)
```

Important: `request` must be imported. Check the existing imports at the top of `pages.py` and add `request` to the `from flask import` line if it's not already there.

Endpoint-name compatibility: the existing `_grow_subnav.html` and other templates use `url_for('pages.grow_fleet')` and `url_for('pages.grow_unit_detail')`. The rename above breaks those calls. Two options:
1. Update the templates to use `pages.unit_fleet` / `pages.unit_detail` (Task 11 does this for the fleet sub-nav).
2. Keep the old endpoint NAMES alive as aliases on the redirect routes — but Flask's `@app.route` doesn't natively support endpoint aliasing per-call. Simpler: keep the OLD endpoint names attached to the redirect functions:

```python
@pages_bp.route("/grow", endpoint="grow_fleet")  # OLD endpoint name kept
def grow_fleet_redirect():
    return redirect(url_for("pages.unit_fleet") + "?type=grow", code=302)


@pages_bp.route("/grow/<int:unit_id>", endpoint="grow_unit_detail")  # OLD
def grow_unit_detail_redirect(unit_id):
    return redirect(url_for("pages.unit_detail", unit_id=unit_id), code=302)
```

This way `url_for('pages.grow_fleet')` still resolves (to /grow → redirect to /units?type=grow) and existing templates that haven't been touched keep working. The grow_fleet endpoint NAME is the deprecation cadence — to be removed in the release after ⓪ ships.

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/units/test_unit_fleet_page.py -v`
Expected: all pass.

Run the existing `tests/grow_server/test_grow_pages.py` to confirm the redirect doesn't break the grow page tests (they may need updating — the tests assert `r.status_code == 200` for `/grow`, which is now 302):

Run: `poetry run pytest tests/grow_server/test_grow_pages.py -v`
Expected: FAIL on `test_grow_route_returns_200` etc.

Update those tests to follow redirects:

```python
def test_grow_route_returns_200(client):
    r = client.get("/grow", follow_redirects=True)
    assert r.status_code == 200
```

Apply the same `follow_redirects=True` fix wherever a `/grow` GET expects 200.

Re-run:
Run: `poetry run pytest tests/grow_server/test_grow_pages.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add mlss_monitor/routes/pages.py tests/units/test_unit_fleet_page.py tests/grow_server/test_grow_pages.py
git commit -m "SDUI ⓪ Task 8: add /units + /units/<id> pages with /grow → 302 redirects"
```

---

### Task 9: Create `unit_fleet.html` template + JS module that groups by `unit_type`

**Files:**
- Create: `templates/unit_fleet.html`
- Create: `static/js/units/unit_fleet.mjs`
- Modify: `templates/grow_fleet.html` (becomes a 1-line stub)
- Test: `tests/units/test_unit_fleet_page.py` (extend)
- Test: `tests/js/test_unit_fleet.mjs` (new)

- [ ] **Step 1: Write the failing test (Python side)**

Append to `tests/units/test_unit_fleet_page.py`:

```python
def test_unit_fleet_page_shows_grow_section_when_only_grow_units_exist(client):
    _set_session(client, role="viewer")
    r = client.get("/units")
    body = r.data.decode("utf-8")
    # The new fleet view groups by unit_type. With only grow units,
    # exactly one collapsible <section data-unit-type="grow"> appears.
    assert 'data-unit-type="grow"' in body
    # The grid host for the grow section.
    assert 'data-units-grid="grow"' in body


def test_unit_fleet_loads_unit_fleet_js_module(client):
    _set_session(client, role="viewer")
    r = client.get("/units")
    body = r.data.decode("utf-8")
    assert "/static/js/units/unit_fleet.mjs" in body


def test_unit_fleet_renders_role_filter_chip_host(client):
    _set_session(client, role="viewer")
    r = client.get("/units")
    body = r.data.decode("utf-8")
    assert 'id="unit-role-filter"' in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/units/test_unit_fleet_page.py -v`
Expected: 3 failures.

- [ ] **Step 3: Create `templates/unit_fleet.html`**

```html
{% extends "base.html" %}
{% block title %}MLSS · Units{% endblock %}
{% block extra_css %}
  <link rel="stylesheet" href="{{ url_for('static', filename='css/grow.css') }}">
{% endblock %}

{% block content %}
  {% include "_unit_subnav.html" %}
  <script>document.body.dataset.role = "{{ current_role }}";</script>
  {% if storage_status and storage_status.is_warning %}
  <div class="storage-warning" role="alert">
    ⚠️ Photo storage is at {{ "%.1f"|format(storage_status.used_pct) }}%
    ({{ (storage_status.used_bytes / 1024**3) | round(1) }} GB of
     {{ (storage_status.total_bytes / 1024**3) | round(1) }} GB).
     Consider archiving old photos under {{ storage_status.images_dir }}.
  </div>
  {% endif %}

  <section class="unit-fleet" id="unit-fleet" data-filter-type="{{ filter_type or '' }}">
    <header class="grow-pageheader">
      <div class="grow-summary" id="grow-summary"></div>
      {% if current_role == 'admin' %}
      <button class="px-btn primary" id="grow-add-btn">+ Add Unit</button>
      {% endif %}
    </header>

    <div id="unit-role-filter">
      <!-- Role-filter chips, populated by unit_fleet.mjs -->
    </div>

    <div id="grow-filter">
      <!-- Existing phase/plant-type/sort filter row, populated by fleet.mjs helpers. -->
    </div>

    {# One <section> per unit_type. Today only 'grow' exists; the
       template is structured to accept more sections in later
       sub-projects without further changes. #}
    <section class="unit-type-section" data-unit-type="grow">
      <h2 class="unit-type-heading">Grow units</h2>
      <div class="grow-grid" id="grow-grid" data-units-grid="grow">
        <!-- Populated by unit_fleet.mjs -->
      </div>
    </section>
  </section>
{% endblock %}

{% block scripts %}
  <script type="module" src="{{ url_for('static', filename='js/units/unit_fleet.mjs') }}"></script>
{% endblock %}
```

- [ ] **Step 4: Create `templates/_unit_subnav.html`**

Copy `_grow_subnav.html` and update endpoint names to be neutral, but keep the legacy aliases registered so existing templates still work:

```html
{# Unit sub-nav — three pills rendered at /units, /units/<id>, /grow/errors,
   /grow/settings. As of SDUI sub-project ⓪ only grow units exist; the
   Errors + Settings pills still point at the grow-specific pages.
   Sub-project ⑧ generalises them.
#}
{% set _ep = request.endpoint %}
{% set _on_fleet = _ep in ("pages.unit_fleet", "pages.unit_detail",
                            "pages.grow_fleet", "pages.grow_unit_detail") %}
{% set _on_errors = _ep == "pages.grow_errors_page" %}
{% set _on_settings = _ep == "pages.grow_settings_page" %}
<nav class="grow-subnav" aria-label="Unit sections">
  <a href="{{ url_for('pages.unit_fleet') }}"
     class="subnav-pill{{ ' active' if _on_fleet else '' }}">
    Fleet
  </a>
  <a href="{{ url_for('pages.grow_errors_page') }}"
     class="subnav-pill{{ ' active' if _on_errors else '' }}">
    Errors
  </a>
  {% if session_role == 'admin' %}
  <a href="{{ url_for('pages.grow_settings_page') }}"
     class="subnav-pill{{ ' active' if _on_settings else '' }}">
    Settings
  </a>
  {% endif %}
</nav>
```

- [ ] **Step 5: Replace `templates/grow_fleet.html` content with a stub that {% include %}s the new template**

```html
{# Legacy template — kept so {% extends "grow_fleet.html" %} (if any
   downstream tests reference it) still resolves. The page route now
   passes a `filter_type='grow'` argument to unit_fleet.html which
   the new fleet view honours by filtering its API call. Remove in
   the release after SDUI sub-project ⓪. #}
{% include "unit_fleet.html" %}
```

- [ ] **Step 6: Create `static/js/units/unit_fleet.mjs`**

```javascript
/**
 * /units page — fleet view, generalised version of the old
 * static/js/grow/fleet.mjs.
 *
 * Differences from the legacy fleet.mjs:
 *   1. Reads optional `data-filter-type` from #unit-fleet and adds it
 *      to the /api/units?type=… call.
 *   2. Renders a per-unit-type collapsible section. Today only 'grow'
 *      exists so it's effectively one section, but the structure is
 *      ready for ⑤'s second unit type.
 *   3. Renders a role-filter chip row above the cards. Today the chips
 *      are populated from unit_roles assignments only (none yet, so
 *      the row is empty); structure is ready for later sub-projects.
 *
 * Imports the existing components (grow-card, empty-state, fleet-filter-row,
 * add-unit-modal) unchanged — they're grow-shaped, and refactoring them is
 * out of scope for sub-project ⓪.
 */
import { renderGrowCard } from "../grow/components/grow-card.mjs";
import { renderEmptyState } from "../grow/components/empty-state.mjs";
import {
  renderFleetFilterRow, applyFilters,
} from "../grow/components/fleet-filter-row.mjs";
import { openAddUnitModalWithTypePicker } from "./add-unit-modal.mjs";


const STATE = {
  units: [],
  filter: { phases: [], statuses: [], plant_types: [], sort: "label" },
};


function _filterType(doc) {
  const el = doc.getElementById("unit-fleet");
  return (el && el.dataset && el.dataset.filterType) || "";
}


async function fetchUnits(doc) {
  const type = _filterType(doc);
  const url = type ? `/api/units?type=${encodeURIComponent(type)}`
                   : "/api/units";
  const r = await fetch(url);
  if (!r.ok) throw new Error(`fetch failed: ${r.status}`);
  return (await r.json()).units;
}


function groupByUnitType(units) {
  const grouped = new Map();
  for (const u of units) {
    const t = u.unit_type || "grow";
    if (!grouped.has(t)) grouped.set(t, []);
    grouped.get(t).push(u);
  }
  return grouped;
}


function uniqueRoles(units) {
  const seen = new Set();
  for (const u of units) {
    for (const r of (u.roles || [])) seen.add(r);
  }
  return Array.from(seen).sort();
}


function renderRoleFilterChips(units, doc) {
  const host = doc.getElementById("unit-role-filter");
  if (!host) return;
  host.innerHTML = "";
  const roles = uniqueRoles(units);
  if (roles.length === 0) return;  // empty row when no roles assigned
  for (const role of roles) {
    const chip = doc.createElement("button");
    chip.type = "button";
    chip.className = "role-chip";
    chip.dataset.role = role;
    chip.textContent = role;
    host.appendChild(chip);
  }
}


export function renderFleet({ units, ownerDocument }) {
  const doc = ownerDocument || document;
  const grouped = groupByUnitType(units);
  // For ⓪ we only have a 'grow' section; the template provides it.
  const growGrid = doc.querySelector('[data-units-grid="grow"]');
  if (growGrid) {
    growGrid.innerHTML = "";
    const growUnits = grouped.get("grow") || [];
    if (growUnits.length === 0) {
      growGrid.appendChild(renderEmptyState({
        enrollmentKey: null,
        mlssHost: (typeof window !== "undefined" && window.location)
          ? window.location.hostname : "",
      }));
    } else {
      for (const u of applyFilters(growUnits, STATE.filter)) {
        growGrid.appendChild(renderGrowCard(u, { ownerDocument: doc }));
      }
    }
  }
  renderRoleFilterChips(units, doc);
}


// Boot — only run if mounted on a real page with the fleet host.
if (typeof document !== "undefined"
    && document.getElementById("unit-fleet")) {
  async function _refresh() {
    try {
      const units = await fetchUnits(document);
      STATE.units = units;
      renderFleet({ units, ownerDocument: document });
    } catch (e) {
      console.error("unit_fleet refresh failed:", e);
    }
  }
  _refresh();
  setInterval(_refresh, 5000);

  const addBtn = document.getElementById("grow-add-btn");
  if (addBtn) {
    addBtn.addEventListener("click", () =>
      openAddUnitModalWithTypePicker({ ownerDocument: document })
    );
  }
}
```

- [ ] **Step 7: Create `static/js/units/add-unit-modal.mjs` (type-picker wrapper)**

```javascript
/**
 * Add-unit modal with a unit-type picker step.
 *
 * Today only 'grow' is available — the picker renders a single radio
 * + auto-selects it, then forwards to the existing grow-only
 * static/js/grow/components/add-unit-modal.mjs's openAddUnitModal.
 *
 * When SDUI sub-project ⑤ ships the second unit type, this module is
 * the place that gains a real picker; the downstream modal stays the
 * same shape (it just shows a different install one-liner per type).
 */
import { openAddUnitModal } from "../grow/components/add-unit-modal.mjs";


// Registry of unit types the operator can add. Populated by
// /api/unit-types in a later sub-project; hardcoded to grow for ⓪.
const _UNIT_TYPES = [
  { value: "grow", label: "Grow unit",
    description: "Plant grow unit — Pi Zero + soil sensor + pump." },
];


export function openAddUnitModalWithTypePicker(opts = {}) {
  const doc = opts.ownerDocument || document;
  if (_UNIT_TYPES.length === 1) {
    // Single type — skip the picker.
    return openAddUnitModal({ ...opts, ownerDocument: doc });
  }

  // Stub for later: render a radio group, then delegate. Today this
  // branch is unreachable since _UNIT_TYPES.length === 1.
  return openAddUnitModal({ ...opts, ownerDocument: doc });
}
```

- [ ] **Step 8: Run Python tests**

Run: `poetry run pytest tests/units/test_unit_fleet_page.py -v`
Expected: all pass.

- [ ] **Step 9: Write the JS test**

Create `tests/js/test_unit_fleet.mjs`:

```javascript
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";


// Build a minimal DOM that matches templates/unit_fleet.html's host
// scaffold so the rendering helpers have somewhere to mount.
function _buildDom() {
  const dom = new JSDOM(`
    <!DOCTYPE html>
    <html><body>
      <section id="unit-fleet" data-filter-type="">
        <div id="grow-summary"></div>
        <div id="unit-role-filter"></div>
        <div id="grow-filter"></div>
        <section class="unit-type-section" data-unit-type="grow">
          <div class="grow-grid" id="grow-grid" data-units-grid="grow"></div>
        </section>
      </section>
    </body></html>
  `);
  return dom;
}


test("renderFleet groups units by unit_type", async () => {
  const dom = _buildDom();
  const { window } = dom;
  // Stub the components imported by unit_fleet.mjs so we don't need
  // the real Plotly-dependent ones in a JS-only test.
  // (Use a Vitest-style mock pattern — node --test doesn't have a
  // built-in mocker, so we use a module-import shim.)
  const { renderFleet } = await import("../../static/js/units/unit_fleet.mjs");
  renderFleet({
    units: [
      { id: 1, unit_type: "grow", label: "Tomato",
        status: "online", roles: [] },
    ],
    ownerDocument: window.document,
  });
  const grid = window.document.querySelector('[data-units-grid="grow"]');
  // grow-card renders something with the unit's label.
  assert.match(grid.innerHTML, /Tomato/);
});


test("renderFleet renders role chips when units have roles", async () => {
  const dom = _buildDom();
  const { window } = dom;
  const { renderFleet } = await import("../../static/js/units/unit_fleet.mjs");
  renderFleet({
    units: [
      { id: 1, unit_type: "grow", label: "T", status: "online",
        roles: ["kitchen", "south_window"] },
      { id: 2, unit_type: "grow", label: "B", status: "online",
        roles: ["kitchen"] },
    ],
    ownerDocument: window.document,
  });
  const chips = window.document.querySelectorAll(
    "#unit-role-filter .role-chip"
  );
  assert.equal(chips.length, 2);
  assert.equal(chips[0].dataset.role, "kitchen");
  assert.equal(chips[1].dataset.role, "south_window");
});


test("renderFleet renders no role chips when no roles assigned", async () => {
  const dom = _buildDom();
  const { window } = dom;
  const { renderFleet } = await import("../../static/js/units/unit_fleet.mjs");
  renderFleet({
    units: [
      { id: 1, unit_type: "grow", label: "T", status: "online", roles: [] },
    ],
    ownerDocument: window.document,
  });
  const chips = window.document.querySelectorAll(
    "#unit-role-filter .role-chip"
  );
  assert.equal(chips.length, 0);
});
```

Note: `node --test` cannot import absolute paths the same way Jest does. The tests rely on the existing JS test pattern in `tests/js/` — check `tests/js/test_grow_card.mjs` for the import-path style used in this repo and match it.

- [ ] **Step 10: Run JS tests**

Run: `npm run test:js -- --test-name-pattern "renderFleet"`
Expected: all pass.

- [ ] **Step 11: Commit**

```bash
git add templates/unit_fleet.html templates/_unit_subnav.html templates/grow_fleet.html static/js/units/unit_fleet.mjs static/js/units/add-unit-modal.mjs tests/units/test_unit_fleet_page.py tests/js/test_unit_fleet.mjs
git commit -m "SDUI ⓪ Task 9: unit_fleet.html + unit_fleet.mjs (group by unit_type, role chips, type-picker stub)"
```

---

### Task 10: API: include `unit_type` and `roles` in the `/api/units` response

**Files:**
- Modify: `mlss_monitor/routes/api_grow_units.py` (`_list_units_impl`, `_get_unit_impl`)
- Test: `tests/units/test_units_api.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/units/test_units_api.py`:

```python
def test_api_units_list_includes_unit_type(client):
    r = client.get("/api/units")
    units = r.get_json()["units"]
    assert len(units) >= 1
    assert all("unit_type" in u for u in units)
    assert all(u["unit_type"] == "grow" for u in units)


def test_api_units_list_includes_roles_as_list(client):
    """roles defaults to an empty list when no rows in unit_roles."""
    r = client.get("/api/units")
    units = r.get_json()["units"]
    assert all(isinstance(u["roles"], list) for u in units)
    assert all(u["roles"] == [] for u in units)


def test_api_units_list_includes_assigned_roles(client, monkeypatch):
    """Once a role is assigned via unit_roles, the GET response
    includes it."""
    import sqlite3
    from datetime import datetime
    import database.init_db as init_db
    conn = sqlite3.connect(init_db.DB_FILE)
    now = datetime.utcnow()
    conn.execute(
        "INSERT INTO unit_roles (unit_id, role, assigned_by, assigned_at) "
        "VALUES (?, ?, ?, ?)",
        (1, "kitchen", "user:wolf", now),
    )
    conn.commit()
    conn.close()
    r = client.get("/api/units/1")
    body = r.get_json()
    assert "kitchen" in body["roles"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/units/test_units_api.py -v -k "unit_type or roles"`
Expected: 3 failures.

- [ ] **Step 3: Update `_list_units_impl` and `_get_unit_impl`**

In `mlss_monitor/routes/api_grow_units.py`:

```python
def _fetch_roles(conn, unit_id: int) -> list[str]:
    """Return the list of roles assigned to a unit, sorted."""
    rows = conn.execute(
        "SELECT role FROM unit_roles WHERE unit_id=? ORDER BY role",
        (unit_id,),
    ).fetchall()
    return [r[0] for r in rows]
```

In `_list_units_impl`, after the existing SELECT, augment each row dict:

```python
for u in units:
    u["unit_type"] = u.get("unit_type") or "grow"
    u["roles"] = _fetch_roles(conn, u["id"])
```

Same in `_get_unit_impl`:

```python
unit["unit_type"] = unit.get("unit_type") or "grow"
unit["roles"] = _fetch_roles(conn, unit["id"])
```

Make sure the existing SELECT projection includes `unit_type` from the `grow_units` table (which it now has from Task 1).

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/units/test_units_api.py -v`
Expected: all pass.

Run the existing grow_units tests to confirm shape compatibility:
Run: `poetry run pytest tests/grow_server/test_grow_units_api.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add mlss_monitor/routes/api_grow_units.py tests/units/test_units_api.py
git commit -m "SDUI ⓪ Task 10: include unit_type + roles in /api/units list+detail responses"
```

---

### Task 11: Update top-nav + sub-nav to use canonical endpoint names

**Files:**
- Modify: `templates/base.html`
- Modify: `templates/grow_unit_detail.html` (use _unit_subnav)
- Modify: `templates/grow_errors.html` (use _unit_subnav)
- Modify: `templates/grow_settings.html` (use _unit_subnav)
- Test: `tests/units/test_unit_fleet_page.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/units/test_unit_fleet_page.py`:

```python
def test_top_nav_uses_canonical_units_url(client):
    _set_session(client, role="viewer")
    r = client.get("/")
    body = r.data.decode("utf-8")
    # The top-nav link to the fleet view points at /units, not /grow.
    nav_start = body.find('class="tab-nav"')
    nav_end = body.find("</nav>", nav_start)
    nav_block = body[nav_start:nav_end]
    assert 'href="/units"' in nav_block or 'href="/units?type=grow"' in nav_block
    # The old /grow link should not appear in the top nav.
    assert 'href="/grow"' not in nav_block
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/units/test_unit_fleet_page.py::test_top_nav_uses_canonical_units_url -v`
Expected: FAIL — top nav still uses /grow.

- [ ] **Step 3: Update `templates/base.html`**

Find the top-nav line that links to /grow. Change it from:

```html
<a href="{{ url_for('pages.grow_fleet') }}">Grow</a>
```

to:

```html
<a href="{{ url_for('pages.unit_fleet') }}">Grow</a>
```

(The visible label stays "Grow" because today only grow units exist; the underlying URL is now canonical.)

- [ ] **Step 4: Update the three grow page templates to use `_unit_subnav.html`**

In `templates/grow_unit_detail.html`, `templates/grow_errors.html`, `templates/grow_settings.html`, replace:

```html
{% include "_grow_subnav.html" %}
```

with:

```html
{% include "_unit_subnav.html" %}
```

The old `_grow_subnav.html` stays in place for one release for any downstream code that includes it directly; it can be removed in the release after ⓪.

- [ ] **Step 5: Run all tests**

Run: `poetry run pytest tests/units/ tests/grow_server/test_grow_pages.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add templates/base.html templates/grow_unit_detail.html templates/grow_errors.html templates/grow_settings.html tests/units/test_unit_fleet_page.py
git commit -m "SDUI ⓪ Task 11: switch top-nav + sub-nav includes to canonical pages.unit_fleet endpoint"
```

---

## Phase 4 — Plant profiles gating + +Add Unit type awareness

### Task 12: Server-side flag `show_plant_profiles` on `/grow/settings`

**Files:**
- Modify: `mlss_monitor/routes/pages.py`
- Modify: `templates/grow_settings.html`
- Modify: `static/js/grow/settings.mjs`
- Test: `tests/units/test_plant_profiles_gating.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/units/test_plant_profiles_gating.py`:

```python
"""Settings → Plant profiles section only renders when a grow unit exists.

Today the section is hardcoded into grow_settings.html — sub-project
⓪ adds a server-side flag so the renderer can hide it when no
unit_type='grow' rows exist. The flag is true when any unit's
unit_type is 'grow'; false otherwise (purely defensive — today every
unit is grow).
"""
import sqlite3
import tempfile

import pytest


@pytest.fixture
def client(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    init_db.create_db()
    from mlss_monitor.app import app
    app.config["TESTING"] = True
    return app.test_client()


def _set_admin(c):
    with c.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user_role"] = "admin"


def test_plant_profiles_visible_when_grow_unit_exists(client):
    """Insert a grow unit, hit the settings page, plant-profiles section
    is rendered."""
    from datetime import datetime
    import database.init_db as init_db
    now = datetime.utcnow()
    conn = sqlite3.connect(init_db.DB_FILE)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at, unit_type) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (1, "hw-grow", "G", now, "h", now, "grow"),
    )
    conn.commit()
    conn.close()
    _set_admin(client)
    r = client.get("/grow/settings")
    body = r.data.decode("utf-8")
    assert "grow-settings-profiles" in body


def test_plant_profiles_hidden_when_no_grow_unit(client):
    """No grow units exist → the plant-profiles section is omitted
    from the rendered HTML."""
    _set_admin(client)
    r = client.get("/grow/settings")
    body = r.data.decode("utf-8")
    assert "grow-settings-profiles" not in body


def test_plant_profiles_visible_template_flag_passed(client, monkeypatch):
    """Verify the template receives show_plant_profiles=True when a
    grow unit is present (regression guard for the gating mechanism)."""
    from datetime import datetime
    import database.init_db as init_db
    now = datetime.utcnow()
    conn = sqlite3.connect(init_db.DB_FILE)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at, unit_type) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (2, "hw-grow2", "G2", now, "h", now, "grow"),
    )
    conn.commit()
    conn.close()
    _set_admin(client)
    captured = {}
    real_render = None

    def _wrap_render(template_name, **ctx):
        captured["ctx"] = ctx
        return real_render(template_name, **ctx)

    import mlss_monitor.routes.pages as pages_mod
    real_render = pages_mod.render_template
    monkeypatch.setattr(pages_mod, "render_template", _wrap_render)
    r = client.get("/grow/settings")
    assert r.status_code == 200
    assert captured["ctx"].get("show_plant_profiles") is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/units/test_plant_profiles_gating.py -v`
Expected: 2-3 failures.

- [ ] **Step 3: Update `grow_settings_page` in `mlss_monitor/routes/pages.py`**

```python
@pages_bp.route("/grow/settings")
@require_role("admin")
def grow_settings_page():
    """Grow → Settings. Admin-only.

    SDUI sub-project ⓪: pass `show_plant_profiles` to the template so
    the plant-profile editor section only renders when at least one
    grow unit exists. Hardcoded-True today felt unnecessary, but the
    moment a non-grow unit type exists (sub-project ⑤) we want the
    section to disappear if no grow units are present — otherwise the
    operator sees a profile editor for a unit type they don't own.
    """
    import sqlite3
    from database.init_db import DB_FILE
    conn = sqlite3.connect(DB_FILE)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM units WHERE unit_type='grow' "
            "AND is_active=1"
        ).fetchone()
    finally:
        conn.close()
    show_plant_profiles = (row[0] > 0) if row else False
    return render_template(
        "grow_settings.html",
        storage_status=get_storage_status(),
        show_plant_profiles=show_plant_profiles,
    )
```

- [ ] **Step 4: Gate the plant-profiles section in `templates/grow_settings.html`**

```html
    <div class="settings-sections">
      <section id="grow-settings-key" class="settings-section"></section>
      {% if show_plant_profiles %}
      <section id="grow-settings-profiles" class="settings-section"></section>
      {% endif %}
      <section id="grow-settings-holiday" class="settings-section"></section>
    </div>
```

- [ ] **Step 5: Make the JS settings module no-op when the host is absent**

In `static/js/grow/settings.mjs`, ensure the plant-profiles mount call (look for code that queries `#grow-settings-profiles`) is wrapped in `if (host) { … }`:

```javascript
const profilesHost = doc.getElementById("grow-settings-profiles");
if (profilesHost) {
  // existing mount logic unchanged
}
```

This is defensive — without it, the JS would throw a null-deref when the section is gated off.

- [ ] **Step 6: Run tests**

Run: `poetry run pytest tests/units/test_plant_profiles_gating.py -v`
Expected: all pass.

Run the existing grow_settings tests:
Run: `poetry run pytest tests/grow_server/test_grow_settings_page.py -v`
Expected: all pass. If `test_grow_settings_page` asserts the plant-profiles section appears unconditionally, update it to seed a grow unit first.

- [ ] **Step 7: Commit**

```bash
git add mlss_monitor/routes/pages.py templates/grow_settings.html static/js/grow/settings.mjs tests/units/test_plant_profiles_gating.py
git commit -m "SDUI ⓪ Task 12: gate plant-profiles editor on existence of unit_type='grow' units"
```

---

### Task 13: +Add Unit modal — wire fleet page to type-picker wrapper

**Files:**
- Modify: `static/js/units/unit_fleet.mjs` (done in Task 9 — verify wiring)
- Modify: `templates/unit_fleet.html` (already wired in Task 9)
- Test: `tests/js/test_add_unit_modal_with_type_picker.mjs` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/js/test_add_unit_modal_with_type_picker.mjs`:

```javascript
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";


function _dom() {
  const dom = new JSDOM(`<!DOCTYPE html><html><body></body></html>`);
  dom.window.document.body.dataset.role = "admin";
  return dom;
}


test("openAddUnitModalWithTypePicker delegates to grow modal when only one type", async () => {
  const dom = _dom();
  const { window } = dom;
  globalThis.window = window;
  globalThis.document = window.document;
  globalThis.navigator = window.navigator;
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ key: "test-key" }) });

  const { openAddUnitModalWithTypePicker } = await import(
    "../../static/js/units/add-unit-modal.mjs"
  );
  const handle = openAddUnitModalWithTypePicker({
    ownerDocument: window.document,
    mlssHost: "test-host",
  });
  // The grow modal mounts an .add-unit-overlay element on the body.
  const overlay = window.document.querySelector(".add-unit-overlay");
  assert.ok(overlay, "grow modal overlay should be mounted");
  handle.close();
});
```

- [ ] **Step 2: Run test to verify it fails (or imports correctly)**

Run: `npm run test:js -- --test-name-pattern "openAddUnitModalWithTypePicker"`
Expected: PASS (the wrapper from Task 9 already delegates correctly). If FAIL, fix `static/js/units/add-unit-modal.mjs` to ensure the delegation path works.

- [ ] **Step 3: Commit**

```bash
git add tests/js/test_add_unit_modal_with_type_picker.mjs
git commit -m "SDUI ⓪ Task 13: add JS test covering single-type-picker delegation to grow modal"
```

---

## Phase 5 — Legacy alias deprecation guards

### Task 14: Tests for the deprecation cadence — `/api/grow/units/<id>/…` aliases still work

**Files:**
- Test: `tests/units/test_legacy_route_redirects.py` (new)

The legacy routes already exist in `api_grow_units.py`; this task adds tests that pin the deprecation contract so removal in the next release is a known breaking change.

- [ ] **Step 1: Write the test**

Create `tests/units/test_legacy_route_redirects.py`:

```python
"""Deprecation contract: /api/grow/units/<id>/… aliases remain working
for one release after SDUI sub-project ⓪ ships.

These tests pin the behaviour so removal in the next release is an
intentional breaking change (the test file must be deleted at the
same time as the legacy routes).
"""
import sqlite3
from datetime import datetime

import pytest


@pytest.fixture
def client(monkeypatch, tmp_path):
    db_path = str(tmp_path / "test.db")
    import database.init_db as init_db
    init_db.DB_FILE = db_path
    monkeypatch.setattr("mlss_monitor.grow.auth.DB_FILE", db_path)
    init_db.create_db()
    now = datetime.utcnow()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at, last_seen_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (1, "hw-1", "U1", now, "h", now, now),
    )
    conn.commit()
    conn.close()
    from flask import Flask
    from mlss_monitor.routes.api_grow_units import api_grow_units_bp
    from mlss_monitor.routes.api_units import api_units_bp
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_units.DB_FILE", db_path
    )
    monkeypatch.setattr(
        "mlss_monitor.grow.health_watchdog.DB_FILE", db_path
    )
    app = Flask(__name__)
    app.register_blueprint(api_grow_units_bp)
    app.register_blueprint(api_units_bp)
    return app.test_client()


def test_legacy_grow_units_list_still_returns_200(client):
    r = client.get("/api/grow/units")
    assert r.status_code == 200


def test_legacy_grow_units_detail_still_returns_200(client):
    r = client.get("/api/grow/units/1")
    assert r.status_code == 200


def test_legacy_grow_page_returns_302_to_units(client):
    """The page route is a redirect (it changed in Task 8); the API
    routes stay as aliases (Tasks 6+7) because firmware/tooling may
    expect 200s, not 302s."""
    # Page redirect is tested in tests/units/test_unit_fleet_page.py;
    # this file is API-focused. Stub to keep the test count obvious.
    assert True
```

- [ ] **Step 2: Run tests**

Run: `poetry run pytest tests/units/test_legacy_route_redirects.py -v`
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add tests/units/test_legacy_route_redirects.py
git commit -m "SDUI ⓪ Task 14: pin /api/grow/units/<id> deprecation contract"
```

---

## Phase 6 — `handle_capabilities` writes `unit_type` + `unit_types` registry

### Task 15: Extend `handle_capabilities` to upsert `unit_types` and accept `unit_type` from boot frame

**Files:**
- Modify: `mlss_monitor/grow/handlers.py` (`handle_capabilities`)
- Modify: `contracts/src/mlss_contracts/ws_messages.py` (`CapabilitiesPayload`)
- Test: `tests/grow_server/test_handler_capabilities.py` (extend)
- Test: `tests/contracts/test_ws_messages_other.py` (extend)

This task touches contracts (since `CapabilitiesPayload` gets an optional `unit_type` field) and the server-side handler (which now writes to `unit_types`). Per the spec, `unit_type` defaults to `'grow'` when absent so old firmware still works.

- [ ] **Step 1: Write the failing test (server-side)**

Append to `tests/grow_server/test_handler_capabilities.py`:

```python
def test_handle_capabilities_writes_to_unit_types_registry(
    monkeypatch, tmp_path,
):
    """First boot frame for a unit_type registers that type."""
    import sqlite3
    from datetime import datetime
    db_path = str(tmp_path / "test.db")
    import database.init_db as init_db
    init_db.DB_FILE = db_path
    init_db.create_db()
    monkeypatch.setattr(
        "mlss_monitor.grow.handlers.DB_FILE", db_path
    )

    from mlss_monitor.grow.handlers import handle_capabilities
    now = datetime.utcnow()
    # Seed the unit row a capabilities frame attaches to.
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (?, ?, ?, ?, ?, ?)",
        (1, "hw-x", "X", now, "h", now),
    )
    conn.commit()
    conn.close()

    handle_capabilities(1, now, {
        "capabilities": [
            {"channel": "soil_moisture", "hardware": "test",
             "is_required": True, "unit_label": "raw", "health": "connected"},
        ],
        "firmware_version": "1.0.0",
        "hardware_serial": "hw-x",
        "unit_type": "grow",
    })

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT unit_type FROM unit_types WHERE unit_type='grow'"
    ).fetchall()
    assert rows == [("grow",)]
    last_seen = conn.execute(
        "SELECT last_seen_at FROM unit_types WHERE unit_type='grow'"
    ).fetchone()
    conn.close()
    assert last_seen[0] is not None


def test_handle_capabilities_defaults_unit_type_to_grow_when_absent(
    monkeypatch, tmp_path,
):
    """Old firmware (no unit_type in the payload) is treated as
    unit_type='grow' — same default the migration uses on the
    grow_units row."""
    import sqlite3
    from datetime import datetime
    db_path = str(tmp_path / "test.db")
    import database.init_db as init_db
    init_db.DB_FILE = db_path
    init_db.create_db()
    monkeypatch.setattr(
        "mlss_monitor.grow.handlers.DB_FILE", db_path
    )
    from mlss_monitor.grow.handlers import handle_capabilities
    now = datetime.utcnow()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (?, ?, ?, ?, ?, ?)",
        (2, "hw-y", "Y", now, "h", now),
    )
    conn.commit()
    conn.close()

    # No unit_type in payload — pre-⓪ firmware.
    handle_capabilities(2, now, {
        "capabilities": [],
        "firmware_version": "0.9.0",
        "hardware_serial": "hw-y",
    })

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT unit_type FROM grow_units WHERE id=2"
    ).fetchone()
    conn.close()
    assert row[0] == "grow"
```

- [ ] **Step 2: Write the failing contracts test**

Append to `tests/contracts/test_ws_messages_other.py` (or wherever `CapabilitiesPayload` tests live — find via `grep -l CapabilitiesPayload tests/contracts/`):

```python
def test_capabilities_payload_accepts_optional_unit_type():
    from mlss_contracts.ws_messages import CapabilitiesPayload
    # Without unit_type — defaults to None; server treats as 'grow'.
    p = CapabilitiesPayload(
        capabilities=[], firmware_version="1.0.0", hardware_serial="hw",
    )
    assert getattr(p, "unit_type", None) is None
    # With unit_type provided.
    p2 = CapabilitiesPayload(
        capabilities=[], firmware_version="1.0.0", hardware_serial="hw",
        unit_type="grow",
    )
    assert p2.unit_type == "grow"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `poetry run pytest tests/grow_server/test_handler_capabilities.py tests/contracts/test_ws_messages_other.py -v -k "unit_type"`
Expected: 3 failures.

- [ ] **Step 4: Add the field to `CapabilitiesPayload`**

In `contracts/src/mlss_contracts/ws_messages.py`:

```python
class CapabilitiesPayload(BaseModel):
    """Sent by unit on WS handshake; declares all detected sensors and actuators."""
    capabilities: list[Capability]
    firmware_version: str
    hardware_serial: str
    uptime_s: float | None = None
    # SDUI sub-project ⓪: unit_type lets MLSS group + filter units by
    # category. Optional for backward compat with firmware too old to
    # emit it; server defaults to 'grow' in that case.
    unit_type: str | None = None
```

- [ ] **Step 5: Extend `handle_capabilities`**

In `mlss_monitor/grow/handlers.py`, find the existing `handle_capabilities` (read the file with `Read` to find the exact location). At the start of the function, defaulting:

```python
def handle_capabilities(unit_id: int, ts: datetime, payload: dict) -> None:
    unit_type = payload.get("unit_type") or "grow"
    # ... existing INSERT/UPDATE for grow_unit_capabilities ...
    conn = sqlite3.connect(DB_FILE, timeout=10)
    try:
        # Update the unit's unit_type if the firmware reports one.
        conn.execute(
            "UPDATE grow_units SET unit_type=? WHERE id=?",
            (unit_type, unit_id),
        )
        # UPSERT into unit_types registry.
        conn.execute(
            "INSERT INTO unit_types "
            "(unit_type, first_seen_at, last_seen_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(unit_type) DO UPDATE SET last_seen_at=excluded.last_seen_at",
            (unit_type, ts, ts),
        )
        # ... existing UPSERT for grow_unit_capabilities ...
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `poetry run pytest tests/grow_server/test_handler_capabilities.py tests/contracts/ -v`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add contracts/src/mlss_contracts/ws_messages.py mlss_monitor/grow/handlers.py tests/grow_server/test_handler_capabilities.py tests/contracts/test_ws_messages_other.py
git commit -m "SDUI ⓪ Task 15: handle_capabilities writes unit_types registry + accepts optional unit_type"
```

---

## Phase 7 — End-to-end verification + physical schema rename

### Task 16: End-to-end test — fresh DB vs migrated DB produce same shape

**Files:**
- Test: `tests/units/test_migration_e2e.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/units/test_migration_e2e.py`:

```python
"""End-to-end: a freshly-created DB and a pre-⓪-snapshot migrated to
the new schema have IDENTICAL shapes (tables, columns, indexes, views).

This is the safety net for the migration. If a pre-⓪ DB ever fails
to converge on the same shape as a fresh DB, the next sub-project
would silently see schema drift.
"""
import sqlite3


def _schema_snapshot(db_path: str) -> dict:
    """Return a dict {object_name: sql} for every CREATE statement
    in the database, sorted by name. Comparable across DBs."""
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    ).fetchall()
    conn.close()
    return {f"{t}:{n}": (sql or "").strip() for (t, n, sql) in rows}


def test_fresh_db_has_canonical_schema(monkeypatch, tmp_path):
    db_path = str(tmp_path / "fresh.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)
    import database.init_db as init_db
    init_db.create_db()
    snap = _schema_snapshot(db_path)
    # Compatibility views must exist.
    assert "view:units" in snap
    assert "view:unit_capabilities" in snap
    assert "view:unit_photos" in snap
    assert "view:unit_errors" in snap
    # New tables must exist.
    assert "table:unit_roles" in snap
    assert "table:unit_types" in snap
    assert "table:unit_audit_log" in snap


def test_running_create_db_twice_is_a_noop(monkeypatch, tmp_path):
    db_path = str(tmp_path / "twice.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)
    import database.init_db as init_db
    init_db.create_db()
    snap1 = _schema_snapshot(db_path)
    init_db.create_db()
    snap2 = _schema_snapshot(db_path)
    assert snap1 == snap2, "second create_db() must produce identical schema"


def test_migrated_db_converges_to_canonical(monkeypatch, tmp_path):
    """Simulate a pre-⓪ DB by stripping the new columns/tables/views,
    then run create_db() and verify the schema matches fresh."""
    fresh_path = str(tmp_path / "fresh.db")
    migrated_path = str(tmp_path / "migrated.db")
    monkeypatch.setattr("database.init_db.DB_FILE", fresh_path)
    import database.init_db as init_db
    init_db.create_db()
    fresh_snap = _schema_snapshot(fresh_path)

    # Build a "pre-⓪" DB by copying fresh then removing the new things.
    import shutil
    shutil.copy(fresh_path, migrated_path)
    conn = sqlite3.connect(migrated_path)
    for view in ("units", "unit_capabilities", "unit_photos", "unit_errors"):
        conn.execute(f"DROP VIEW IF EXISTS {view}")
    for tbl in ("unit_roles", "unit_types", "unit_audit_log"):
        conn.execute(f"DROP TABLE IF EXISTS {tbl}")
    # Drop unit_type column by recreating the table without it. SQLite
    # 3.35+ supports ALTER TABLE DROP COLUMN.
    try:
        conn.execute("ALTER TABLE grow_units DROP COLUMN unit_type")
    except Exception:
        pass
    conn.commit()
    conn.close()

    # Now migrate by running create_db() again.
    monkeypatch.setattr("database.init_db.DB_FILE", migrated_path)
    init_db.create_db()
    migrated_snap = _schema_snapshot(migrated_path)

    assert migrated_snap == fresh_snap, (
        "migrated schema must match fresh — drift detected"
    )
```

- [ ] **Step 2: Run tests**

Run: `poetry run pytest tests/units/test_migration_e2e.py -v`
Expected: PASS (the migrations from Tasks 1-5 should already converge).
If any fail, the migration list in `init_db.py` or `unit_schema.py` is non-idempotent — fix and re-run.

- [ ] **Step 3: Commit**

```bash
git add tests/units/test_migration_e2e.py
git commit -m "SDUI ⓪ Task 16: e2e test — fresh + migrated DBs produce identical schema"
```

---

### Task 17: Smoke test — full app boots, fleet renders, no broken imports

**Files:**
- Test: `tests/units/test_full_app_smoke.py` (new)

- [ ] **Step 1: Write the test**

Create `tests/units/test_full_app_smoke.py`:

```python
"""Smoke test: full Flask app boots, renders /units, /units/<id>,
/grow → 302 to /units?type=grow, and all the API endpoints respond
without 5xx errors.

This catches accidental import-time breakages in the rename — e.g.
a route that imports a removed function, or a template that
references an undefined URL.
"""
import sqlite3
import tempfile
from datetime import datetime

import pytest


@pytest.fixture
def client(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    init_db.create_db()
    now = datetime.utcnow()
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at, last_seen_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (1, "hw-1", "U1", now, "h", now, now),
    )
    conn.commit()
    conn.close()
    from mlss_monitor.app import app
    app.config["TESTING"] = True
    return app.test_client()


def _set_admin(c):
    with c.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user_role"] = "admin"
        sess["user"] = "smoke"


@pytest.mark.parametrize("path,expected_status", [
    ("/units", 200),
    ("/units/1", 200),
    ("/grow", 302),
    ("/grow/1", 302),
    ("/api/units", 200),
    ("/api/units/1", 200),
    ("/api/grow/units", 200),
    ("/api/grow/units/1", 200),
    ("/grow/settings", 200),
    ("/grow/errors", 200),
])
def test_route_returns_expected_status(client, path, expected_status):
    _set_admin(client)
    r = client.get(path, follow_redirects=False)
    assert r.status_code == expected_status, (
        f"{path}: expected {expected_status}, got {r.status_code} "
        f"({r.data[:200]})"
    )
```

- [ ] **Step 2: Run tests**

Run: `poetry run pytest tests/units/test_full_app_smoke.py -v`
Expected: all pass.

If any 500s, read the response body — it'll point at the broken import / template / endpoint.

- [ ] **Step 3: Commit**

```bash
git add tests/units/test_full_app_smoke.py
git commit -m "SDUI ⓪ Task 17: smoke test covering all renamed + aliased routes"
```

---

### Task 18: Full test-suite green sweep

**Files:** none (test-run-only task)

- [ ] **Step 1: Run the full test suite**

Run: `poetry run pytest -v`
Expected: all pass.

Run: `npm run test:js`
Expected: all pass.

If anything fails, address it before moving to Task 19.

- [ ] **Step 2: Commit (if any fixes were needed)**

If you found and fixed something:

```bash
git add <files>
git commit -m "SDUI ⓪ Task 18: fix <issue> uncovered in full-suite sweep"
```

If everything passed, no commit needed.

---

### Task 19: Decision — defer physical schema rename to a future sub-project

**Files:** this plan (mark as decision in the "Done" checklist).

The compatibility views from Task 5 mean nothing forces a physical `ALTER TABLE … RENAME` during ⓪. The risk/reward tilt:
- Physical rename now: one more migration, more code paths to keep idempotent, but a cleaner schema diagram.
- Physical rename later (sub-project ① or a dedicated cleanup task): zero added risk during ⓪.

**Decision**: defer the physical rename. Sub-project ① (telemetry storage refactor) is the next consumer; if it wants to ALTER `grow_units` → `units` while it's already rewriting `grow_telemetry` → `unit_telemetry`, that's the natural moment. The compatibility views are a stable bridge until then.

No code change required for this task — it's a documented decision so future engineers don't wonder why grow_* tables are still there at ⓪'s end.

- [ ] **Step 1: Add a note to `database/unit_schema.py` documenting the decision**

Append after the `create_unit_schema(cur)` function definition:

```python
# Why the grow_* tables still exist after SDUI sub-project ⓪
# ─────────────────────────────────────────────────────────────
# The compatibility views (units, unit_capabilities, unit_photos,
# unit_errors) are read-only proxies for the existing grow_* tables.
# Every reader in the codebase has been (or can be) cut over to the
# new names; writers still target grow_*. A physical ALTER TABLE
# RENAME was deliberately deferred to sub-project ① (telemetry
# storage refactor), which is going to rewrite grow_telemetry anyway
# — bundling the rename with that work avoids two migrations on
# top of the same data.
```

- [ ] **Step 2: Commit**

```bash
git add database/unit_schema.py
git commit -m "SDUI ⓪ Task 19: document decision to defer grow_* → physical rename to sub-project ①"
```

---

### Task 20: Update top-level docs to mention the canonical paths

**Files:**
- Modify: `docs/PLANT_GROW_UNIT_USAGE.md`
- Modify: `docs/PLANT_GROW_UNIT_ARCHITECTURE.md`
- Modify: `docs/DATABASE.md`

- [ ] **Step 1: Read the existing docs to know what to update**

Use the `Read` tool on each file to find sections that reference `/api/grow/units/<id>/` or `/grow` URLs.

- [ ] **Step 2: Add a "Canonical URLs (SDUI sub-project ⓪)" subsection to each doc**

Example for `PLANT_GROW_UNIT_USAGE.md`:

```markdown
## Canonical URLs (SDUI sub-project ⓪)

As of 2026-05-XX (release notes), the canonical paths for unit-related
URLs are:

| Old (deprecated) | New (canonical) |
|---|---|
| `GET /grow` | `GET /units` (or `GET /units?type=grow`) |
| `GET /grow/<id>` | `GET /units/<id>` |
| `GET /api/grow/units` | `GET /api/units` |
| `GET /api/grow/units/<id>` | `GET /api/units/<id>` |
| `POST /api/grow/units/<id>/water-now` | `POST /api/units/<id>/water-now` |

The deprecated paths still work for one release; tooling and bookmarks
should migrate to the canonical names. New unit types (planned in SDUI
sub-project ⑤) will use the canonical names exclusively.
```

For `DATABASE.md`, add a note mentioning the compatibility views:

```markdown
### Compatibility views (SDUI sub-project ⓪)

The grow_* tables still exist physically; the unit-type-agnostic
codebase reads through these views:

| View | Underlying table |
|---|---|
| `units` | `grow_units` |
| `unit_capabilities` | `grow_unit_capabilities` |
| `unit_photos` | `grow_photos` |
| `unit_errors` | `grow_errors` |

Plus three new tables: `unit_roles`, `unit_types`, `unit_audit_log`.

Sub-project ① will physically rename the underlying tables.
```

- [ ] **Step 3: Commit**

```bash
git add docs/PLANT_GROW_UNIT_USAGE.md docs/PLANT_GROW_UNIT_ARCHITECTURE.md docs/DATABASE.md
git commit -m "SDUI ⓪ Task 20: document canonical /units paths + compatibility views"
```

---

## Done? — Acceptance checklist

After every task above is committed, this sub-project is done iff:

- [ ] `poetry run pytest tests/units/ -v` is fully green (≥ 30 tests).
- [ ] `poetry run pytest tests/grow_server/ -v` is fully green (no regression).
- [ ] `poetry run pytest tests/contracts/ -v` is fully green (CapabilitiesPayload now accepts optional `unit_type`).
- [ ] `poetry run pytest` (whole suite) is fully green.
- [ ] `npm run test:js` is fully green.
- [ ] Hitting `GET /units` (logged in) returns 200 and renders the new fleet view with all existing grow units in a "Grow units" section.
- [ ] Hitting `GET /grow` returns a 302 to `/units?type=grow`.
- [ ] Hitting `GET /api/units` returns the same JSON shape as `GET /api/grow/units`, plus a `unit_type` field on every unit and a `roles` array (empty by default).
- [ ] Hitting `GET /api/units?type=weather` returns `{"units": []}`.
- [ ] `grow_units.unit_type` is `'grow'` for every existing row in the deployed DB; a fresh DB has `unit_types` seeded with `'grow'`.
- [ ] `unit_roles`, `unit_types`, `unit_audit_log` tables exist.
- [ ] Compatibility views `units`, `unit_capabilities`, `unit_photos`, `unit_errors` all return the same rows as their underlying `grow_*` table.
- [ ] The "+ Add Unit" button on `/units` still opens the same enrollment-key modal as it did on `/grow` (delegation through the type-picker wrapper).
- [ ] Settings → Plant profiles section is hidden when no `unit_type='grow'` units exist (smoke test against an empty DB).
- [ ] No `grow_*` identifier remains in the canonical route names (`api_units.py`, `pages.unit_fleet`, `pages.unit_detail`, `templates/unit_fleet.html`, `templates/_unit_subnav.html`, `static/js/units/`).
- [ ] Docs in `docs/PLANT_GROW_UNIT_USAGE.md`, `docs/PLANT_GROW_UNIT_ARCHITECTURE.md`, `docs/DATABASE.md` mention the canonical URLs + compatibility views.
- [ ] Branch is pushed.

---

## Out of scope for ⓪ (handled by later sub-projects)

- Physical table rename `grow_units` → `units` — deferred to sub-project ① (telemetry refactor).
- Replacing `templates/grow_unit_detail.html` with an SDUI-renderer-driven shell — sub-project ④.
- Renaming `static/js/grow/components/*` — sub-project ②/③/④ (the SDUI renderer replaces most of these wholesale).
- `unit_audit_log` writers (the generic `/api/units/<id>/actions/<name>` endpoint) — sub-projects ②/③.
- Real second unit type — sub-project ⑤.
- `widget_vocabulary_version` validation on capabilities frame — sub-projects ②/③.
- JWT auth + Pi-local fallback — sub-projects ⑥/⑦.
- `unit_telemetry` schemaless table — sub-project ①.
- Auto-generated wide views per unit type — sub-project ①.

---

## Notes for the implementing engineer

1. **Read tasks in order.** Each task assumes the prior tasks' commits exist. The migrations + view definitions especially: Task 5 depends on Task 1's column.
2. **Idempotency is the bargain you keep with the live DB.** Every migration must be safe to re-run. The `try/except` pattern around ALTER statements in `init_db.py` is the convention; the `CREATE TABLE IF NOT EXISTS` / `CREATE VIEW … (DROP first)` patterns in `unit_schema.py` are the rest.
3. **When in doubt about the legacy endpoint NAMES**, keep them registered as aliases. Flask's `endpoint=` kwarg lets you keep `url_for('pages.grow_fleet')` working even after the route function is renamed.
4. **The +Add Unit modal type-picker is a stub today.** It MUST behave identically to the old single-type modal (which goes straight to the enrollment-key reveal) when only one type is registered. Sub-project ⑤ extends it.
5. **The Pi firmware does NOT need to be updated for ⓪.** Firmware uses WS, not HTTP; the canonical `/api/units/<id>/` paths are alias-only for now. Firmware emits the same capabilities payload as today (server defaults `unit_type` to `'grow'`).
6. **`templates/grow_unit_detail.html` deliberately stays grow-named.** Sub-project ④ replaces it wholesale with the SDUI renderer — renaming it during ⓪ would create churn that ④ instantly undoes. The detail page route name is canonical (`pages.unit_detail`); only the template file path is still grow-shaped.
