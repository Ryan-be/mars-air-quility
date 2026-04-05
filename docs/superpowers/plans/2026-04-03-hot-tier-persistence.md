# Hot-Tier Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist the 60-minute hot tier to SQLite so the in-memory ring buffer is fully restored when the app restarts, eliminating the cold-window where all detection features are None.

**Architecture:** A new `hot_tier` SQLite table mirrors every `NormalisedReading` pushed to the deque. On startup `HotTier.__init__` loads the last 60 minutes of rows from the DB, pre-filling the deque before the first sensor read. A cleardown job runs every 60s to DELETE rows older than 60 minutes, keeping the table ≤ 3600 rows permanently. The `HotTier` class accepts an optional `db_file` path so it can be unit-tested with a temp DB without touching the real one.

**Tech Stack:** Python 3.11, SQLite via stdlib `sqlite3`, existing `database/init_db.py` schema pattern, `pytest`.

---

## Context (read before touching any file)

**Branch:** `claude/zealous-hugle`

**Key existing files:**
- `mlss_monitor/hot_tier.py` — `HotTier` class with `push()`, `snapshot()`, `latest()`, `last_n()`, `last_minutes()`. Uses `deque(maxlen=3600)`. No DB interaction yet.
- `mlss_monitor/data_sources/base.py` — `NormalisedReading` dataclass + `SENSOR_FIELDS` tuple. Fields: `timestamp`, `source`, `tvoc_ppb`, `eco2_ppm`, `temperature_c`, `humidity_pct`, `pm25_ug_m3`, `co_ppb`, `no2_ppb`, `nh3_ppb`.
- `database/init_db.py` — `create_db()` function that runs `CREATE TABLE IF NOT EXISTS` for all tables. This is where the new `hot_tier` table schema goes.
- `database/db_logger.py` — `DB_FILE = config.get("DB_FILE", "data/sensor_data.db")`. All functions use this module-level variable. Follow the same pattern.
- `mlss_monitor/app.py` — `hot_tier = HotTier(maxlen=3600)` at module level (line ~190). `_sensor_read_loop()` calls `hot_tier.push(merge_readings(readings))` every second. `_background_log()` has a `_CYCLE_60S` block — the cleardown job goes here.
- `tests/conftest.py` — `_patch_db(path)` patches `dbi.DB_FILE`, `dbl.DB_FILE`, `udb.DB_FILE`. The `db` fixture creates a temp DB and calls `dbi.create_db()`. The new `hot_tier.py` import of `DB_FILE` must also be patchable.
- `tests/test_hot_tier.py` — existing 8 tests. All construct `HotTier(maxlen=N)` with no DB. These tests must keep passing — the DB is optional.

**`NormalisedReading` field → SQLite column mapping:**
```
timestamp     → TEXT  (ISO 8601, e.g. "2026-04-03T14:23:01.123456+00:00")
source        → TEXT
tvoc_ppb      → REAL
eco2_ppm      → REAL
temperature_c → REAL
humidity_pct  → REAL
pm25_ug_m3    → REAL
co_ppb        → REAL
no2_ppb       → REAL
nh3_ppb       → REAL
```

**Design constraint:** `HotTier` must remain usable with no DB (pass `db_file=None`) for all existing tests. When `db_file=None`, all DB operations are no-ops.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `database/init_db.py` | Add `hot_tier` table to `create_db()` |
| Modify | `mlss_monitor/hot_tier.py` | Accept `db_file` param; write on push; load on init; expose `prune_old()` |
| Modify | `mlss_monitor/app.py` | Pass `db_file` to `HotTier`; call `hot_tier.prune_old()` every 60s |
| Modify | `tests/conftest.py` | Patch `hot_tier.DB_FILE` in `_patch_db()` |
| Create | `tests/test_hot_tier_persistence.py` | New tests for DB write, reload, pruning |

---

## Task 1: Add `hot_tier` table to `database/init_db.py`

**Files:**
- Modify: `database/init_db.py`

- [ ] **Step 1.1: Write the failing test**

There is no existing test that verifies the `hot_tier` table exists. Add a quick check to `tests/test_hot_tier_persistence.py` (which does not exist yet — create it):

```python
"""Tests for HotTier SQLite persistence: write, reload, prune."""
from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mlss_monitor.data_sources.base import NormalisedReading
from mlss_monitor.hot_tier import HotTier


def _reading(tvoc: float = 100.0, seconds_ago: int = 0) -> NormalisedReading:
    return NormalisedReading(
        timestamp=datetime.now(timezone.utc) - timedelta(seconds=seconds_ago),
        source="test",
        tvoc_ppb=tvoc,
        temperature_c=22.0,
        humidity_pct=50.0,
    )


# ── Schema ────────────────────────────────────────────────────────────────────

def test_hot_tier_table_created_by_create_db(tmp_path):
    """create_db() must create the hot_tier table."""
    import database.init_db as dbi
    db_path = str(tmp_path / "test.db")
    original = dbi.DB_FILE
    dbi.DB_FILE = db_path
    try:
        dbi.create_db()
        conn = sqlite3.connect(db_path)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        conn.close()
        assert "hot_tier" in tables
    finally:
        dbi.DB_FILE = original
```

- [ ] **Step 1.2: Run test to verify it fails**

```bash
python -m pytest tests/test_hot_tier_persistence.py::test_hot_tier_table_created_by_create_db -v
```
Expected: FAIL — `hot_tier` table not in sqlite_master.

- [ ] **Step 1.3: Add `hot_tier` table to `create_db()` in `database/init_db.py`**

Open `database/init_db.py` and add this block inside `create_db()`, after the existing `CREATE TABLE IF NOT EXISTS inferences` block and before `conn.commit()`:

```python
    cur.execute("""
    CREATE TABLE IF NOT EXISTS hot_tier (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT    NOT NULL,
        source    TEXT    NOT NULL,
        tvoc_ppb      REAL,
        eco2_ppm      REAL,
        temperature_c REAL,
        humidity_pct  REAL,
        pm25_ug_m3    REAL,
        co_ppb        REAL,
        no2_ppb       REAL,
        nh3_ppb       REAL
    );
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_hot_tier_timestamp ON hot_tier (timestamp);"
    )
```

- [ ] **Step 1.4: Run test to verify it passes**

```bash
python -m pytest tests/test_hot_tier_persistence.py::test_hot_tier_table_created_by_create_db -v
```
Expected: PASS.

- [ ] **Step 1.5: Run full suite to confirm no regressions**

```bash
python -m pytest tests/ -q
```
Expected: same count as before (318 passed) + 1 new pass.

- [ ] **Step 1.6: Commit**

```bash
git add database/init_db.py tests/test_hot_tier_persistence.py
git commit -m "feat: add hot_tier table to init_db schema"
```

---

## Task 2: Add DB persistence to `HotTier`

**Files:**
- Modify: `mlss_monitor/hot_tier.py`
- Modify: `tests/test_hot_tier_persistence.py` (add more tests)

### Step 2.1 — Write all failing tests first

- [ ] **Step 2.1: Add persistence tests to `tests/test_hot_tier_persistence.py`**

Append these tests to the file (after the schema test):

```python
# ── Write on push ─────────────────────────────────────────────────────────────

def test_push_writes_row_to_db(tmp_path):
    """push() must insert one row into the hot_tier table."""
    import database.init_db as dbi
    import database.db_logger as dbl
    import mlss_monitor.hot_tier as ht_mod

    db_path = str(tmp_path / "test.db")
    for mod in (dbi, dbl, ht_mod):
        mod.DB_FILE = db_path
    dbi.create_db()

    tier = HotTier(maxlen=3600, db_file=db_path)
    tier.push(_reading(tvoc=150.0))

    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT tvoc_ppb FROM hot_tier").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] == pytest.approx(150.0)


def test_push_stores_all_sensor_fields(tmp_path):
    """push() must store every NormalisedReading field correctly."""
    import database.init_db as dbi
    import mlss_monitor.hot_tier as ht_mod

    db_path = str(tmp_path / "test.db")
    dbi.DB_FILE = db_path
    ht_mod.DB_FILE = db_path
    dbi.create_db()

    r = NormalisedReading(
        timestamp=datetime(2026, 4, 3, 12, 0, 0, tzinfo=timezone.utc),
        source="test",
        tvoc_ppb=200.0,
        eco2_ppm=800.0,
        temperature_c=22.5,
        humidity_pct=55.0,
        pm25_ug_m3=5.0,
        co_ppb=None,
        no2_ppb=None,
        nh3_ppb=None,
    )
    tier = HotTier(maxlen=3600, db_file=db_path)
    tier.push(r)

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT * FROM hot_tier").fetchone()
    conn.close()

    # Columns: id, timestamp, source, tvoc_ppb, eco2_ppm, temperature_c,
    #          humidity_pct, pm25_ug_m3, co_ppb, no2_ppb, nh3_ppb
    assert row[2] == "test"           # source
    assert row[3] == pytest.approx(200.0)  # tvoc_ppb
    assert row[4] == pytest.approx(800.0)  # eco2_ppm
    assert row[5] == pytest.approx(22.5)   # temperature_c
    assert row[6] == pytest.approx(55.0)   # humidity_pct
    assert row[7] == pytest.approx(5.0)    # pm25_ug_m3
    assert row[8] is None                  # co_ppb
    assert row[9] is None                  # no2_ppb
    assert row[10] is None                 # nh3_ppb


def test_push_with_no_db_does_not_raise():
    """HotTier(db_file=None) must work exactly as before — no DB operations."""
    tier = HotTier(maxlen=3600, db_file=None)
    tier.push(_reading(tvoc=100.0))
    assert tier.size() == 1


# ── Reload on init ────────────────────────────────────────────────────────────

def test_init_loads_last_60_min_from_db(tmp_path):
    """HotTier.__init__ must pre-fill the deque from the DB."""
    import database.init_db as dbi
    import mlss_monitor.hot_tier as ht_mod

    db_path = str(tmp_path / "test.db")
    dbi.DB_FILE = db_path
    ht_mod.DB_FILE = db_path
    dbi.create_db()

    # Write 3 readings into DB manually
    tier1 = HotTier(maxlen=3600, db_file=db_path)
    tier1.push(_reading(tvoc=10.0, seconds_ago=120))
    tier1.push(_reading(tvoc=20.0, seconds_ago=60))
    tier1.push(_reading(tvoc=30.0, seconds_ago=0))

    # Reload — should get all 3 back
    tier2 = HotTier(maxlen=3600, db_file=db_path)
    assert tier2.size() == 3
    assert tier2.latest().tvoc_ppb == pytest.approx(30.0)


def test_init_ignores_rows_older_than_60_min(tmp_path):
    """HotTier.__init__ must NOT load rows older than 60 minutes."""
    import database.init_db as dbi
    import mlss_monitor.hot_tier as ht_mod

    db_path = str(tmp_path / "test.db")
    dbi.DB_FILE = db_path
    ht_mod.DB_FILE = db_path
    dbi.create_db()

    # Insert a row that is 2 hours old directly into the DB
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO hot_tier (timestamp, source, tvoc_ppb) VALUES (?, ?, ?)",
        (old_ts, "test", 999.0),
    )
    conn.commit()
    conn.close()

    tier = HotTier(maxlen=3600, db_file=db_path)
    assert tier.size() == 0  # old row ignored


def test_init_with_no_db_starts_empty():
    """HotTier(db_file=None) must start empty — no DB load attempted."""
    tier = HotTier(maxlen=3600, db_file=None)
    assert tier.size() == 0


# ── Pruning ───────────────────────────────────────────────────────────────────

def test_prune_old_deletes_rows_older_than_60_min(tmp_path):
    """prune_old() must delete rows with timestamp < now - 60 min."""
    import database.init_db as dbi
    import mlss_monitor.hot_tier as ht_mod

    db_path = str(tmp_path / "test.db")
    dbi.DB_FILE = db_path
    ht_mod.DB_FILE = db_path
    dbi.create_db()

    tier = HotTier(maxlen=3600, db_file=db_path)

    # Insert one recent and one old row via push
    tier.push(_reading(tvoc=1.0, seconds_ago=30))      # 30s ago — keep
    tier.push(_reading(tvoc=2.0, seconds_ago=3700))    # >60min ago — delete

    conn = sqlite3.connect(db_path)
    count_before = conn.execute("SELECT COUNT(*) FROM hot_tier").fetchone()[0]
    conn.close()
    assert count_before == 2

    tier.prune_old()

    conn = sqlite3.connect(db_path)
    rows_after = conn.execute(
        "SELECT tvoc_ppb FROM hot_tier"
    ).fetchall()
    conn.close()
    assert len(rows_after) == 1
    assert rows_after[0][0] == pytest.approx(1.0)  # recent row survives


def test_prune_old_with_no_db_does_not_raise():
    """prune_old() on a no-DB HotTier must be a no-op."""
    tier = HotTier(maxlen=3600, db_file=None)
    tier.prune_old()  # must not raise
```

- [ ] **Step 2.2: Run tests to verify they all fail**

```bash
python -m pytest tests/test_hot_tier_persistence.py -v
```
Expected: the schema test passes, all new tests FAIL (HotTier doesn't accept `db_file` yet).

### Step 2.3 — Implement the new `HotTier`

- [ ] **Step 2.3: Replace `mlss_monitor/hot_tier.py` with this implementation**

```python
"""HotTier: in-memory ring buffer of NormalisedReadings with SQLite persistence.

The DB is optional — pass db_file=None (or omit it) to run purely in-memory,
which is how all pre-existing tests use it.

When db_file is provided:
- __init__ loads the last 60 minutes of rows from the DB into the deque.
- push() inserts each reading into the DB as well as the deque.
- prune_old() deletes rows older than 60 minutes (call every 60s from app.py).
"""
from __future__ import annotations

import logging
import sqlite3
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mlss_monitor.data_sources.base import NormalisedReading

log = logging.getLogger(__name__)

# Module-level DB_FILE so tests can patch it (same pattern as db_logger.py).
# Overridden by app.py passing db_file= explicitly; this default is a fallback.
from config import config
DB_FILE: str = config.get("DB_FILE", "data/sensor_data.db")

# Ordered list of sensor columns in hot_tier table (matches NormalisedReading fields).
_SENSOR_COLS: tuple[str, ...] = (
    "tvoc_ppb", "eco2_ppm", "temperature_c",
    "humidity_pct", "pm25_ug_m3", "co_ppb", "no2_ppb", "nh3_ppb",
)


class HotTier:
    """In-memory ring buffer of NormalisedReading objects.

    Thread-safe for single-writer / multiple-reader usage under CPython's GIL.
    deque.append() and reads via list() are atomic in CPython.

    Args:
        maxlen: Maximum number of readings to keep in memory (default 3600 = 1hr at 1Hz).
        db_file: Path to SQLite DB for persistence. Pass None to disable DB entirely.
                 When None, push/prune are no-ops against the DB and __init__ skips
                 the reload. All existing behaviour is preserved.
    """

    def __init__(self, maxlen: int = 3600, db_file: str | None = None) -> None:
        from mlss_monitor.data_sources.base import NormalisedReading as _NR  # noqa: F401
        self._buffer: deque[NormalisedReading] = deque(maxlen=maxlen)
        self._db_file = db_file
        if self._db_file is not None:
            self._load_from_db()

    # ── Public API (unchanged from original) ─────────────────────────────────

    def push(self, reading: NormalisedReading) -> None:
        self._buffer.append(reading)
        if self._db_file is not None:
            self._insert_row(reading)

    def latest(self) -> NormalisedReading | None:
        return self._buffer[-1] if self._buffer else None

    def size(self) -> int:
        return len(self._buffer)

    def last_n(self, n: int) -> list[NormalisedReading]:
        """Return the n most recent readings, oldest first."""
        buf = list(self._buffer)
        return buf[-n:] if n <= len(buf) else buf

    def last_minutes(self, minutes: float) -> list[NormalisedReading]:
        """Return all readings from the last `minutes` minutes, oldest first."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        return [r for r in self._buffer if r.timestamp >= cutoff]

    def snapshot(self) -> list[NormalisedReading]:
        """Return a full copy of the buffer contents, oldest first."""
        return list(self._buffer)

    # ── New: DB maintenance ───────────────────────────────────────────────────

    def prune_old(self) -> None:
        """Delete rows older than 60 minutes from the hot_tier table.

        Call this periodically (every 60s) from the background log loop.
        No-op when db_file is None.
        """
        if self._db_file is None:
            return
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat()
        conn = None
        try:
            conn = sqlite3.connect(self._db_file)
            conn.execute("DELETE FROM hot_tier WHERE timestamp < ?", (cutoff,))
            conn.commit()
        except Exception as exc:
            log.warning("HotTier.prune_old failed: %s", exc)
        finally:
            if conn:
                conn.close()

    # ── Private helpers ───────────────────────────────────────────────────────

    def _load_from_db(self) -> None:
        """Load the last 60 minutes of rows from DB into the deque on startup."""
        from mlss_monitor.data_sources.base import NormalisedReading
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat()
        conn = None
        try:
            conn = sqlite3.connect(self._db_file)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM hot_tier WHERE timestamp >= ? ORDER BY timestamp ASC",
                (cutoff,),
            ).fetchall()
            for row in rows:
                ts_str = row["timestamp"]
                # Parse ISO 8601 timestamp — stored with UTC offset
                try:
                    ts = datetime.fromisoformat(ts_str)
                except ValueError:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                reading = NormalisedReading(
                    timestamp=ts,
                    source=row["source"],
                    **{col: row[col] for col in _SENSOR_COLS},
                )
                self._buffer.append(reading)
            if rows:
                log.info("HotTier: loaded %d readings from DB (last 60 min)", len(rows))
        except Exception as exc:
            log.warning("HotTier: could not load from DB: %s", exc)
        finally:
            if conn:
                conn.close()

    def _insert_row(self, reading: NormalisedReading) -> None:
        """Insert one NormalisedReading row into hot_tier."""
        cols = ("timestamp", "source") + _SENSOR_COLS
        placeholders = ", ".join("?" for _ in cols)
        values = (
            reading.timestamp.isoformat(),
            reading.source,
            *[getattr(reading, col) for col in _SENSOR_COLS],
        )
        conn = None
        try:
            conn = sqlite3.connect(self._db_file)
            conn.execute(
                f"INSERT INTO hot_tier ({', '.join(cols)}) VALUES ({placeholders})",
                values,
            )
            conn.commit()
        except Exception as exc:
            log.warning("HotTier._insert_row failed: %s", exc)
        finally:
            if conn:
                conn.close()
```

- [ ] **Step 2.4: Run persistence tests**

```bash
python -m pytest tests/test_hot_tier_persistence.py -v
```
Expected: all 11 tests PASS.

- [ ] **Step 2.5: Run existing hot tier tests (must still pass)**

```bash
python -m pytest tests/test_hot_tier.py -v
```
Expected: all 8 original tests PASS — `HotTier(maxlen=N)` with no `db_file` still works.

- [ ] **Step 2.6: Run full suite**

```bash
python -m pytest tests/ -q
```
Expected: 329 passed (318 + 11 new).

- [ ] **Step 2.7: Commit**

```bash
git add mlss_monitor/hot_tier.py tests/test_hot_tier_persistence.py
git commit -m "feat: add SQLite persistence to HotTier (write-on-push, reload-on-init, prune)"
```

---

## Task 3: Wire persistence into `app.py` + patch conftest

**Files:**
- Modify: `mlss_monitor/app.py`
- Modify: `tests/conftest.py`

### Step 3.1 — Patch `conftest.py` so tests can patch the new `DB_FILE`

- [ ] **Step 3.1: Update `_patch_db()` in `tests/conftest.py`**

Find `_patch_db` (around line 26):
```python
def _patch_db(path: str):
    dbi.DB_FILE = path
    dbl.DB_FILE = path
    udb.DB_FILE = path
```

Add one line so the hot tier module also uses the test DB:
```python
def _patch_db(path: str):
    import mlss_monitor.hot_tier as ht_mod
    dbi.DB_FILE = path
    dbl.DB_FILE = path
    udb.DB_FILE = path
    ht_mod.DB_FILE = path
```

- [ ] **Step 3.2: Run full suite — confirm no regressions**

```bash
python -m pytest tests/ -q
```
Expected: same count as Step 2.6 (329 passed).

### Step 3.3 — Wire `db_file` into `HotTier` instantiation in `app.py`

- [ ] **Step 3.3: Update `app.py` to pass `db_file` to `HotTier`**

Find this block (around line 190):
```python
hot_tier = HotTier(maxlen=3600)
state.hot_tier = hot_tier
```

Replace with:
```python
hot_tier = HotTier(maxlen=3600, db_file=DB_FILE)
state.hot_tier = hot_tier
```

Where `DB_FILE` is the import from `database.db_logger`. Check whether it is already imported in `app.py`:

```bash
grep "DB_FILE" mlss_monitor/app.py
```

If `DB_FILE` is not already imported at the top of `app.py`, add this import alongside the other `database.db_logger` imports:
```python
from database.db_logger import DB_FILE
```

(If `database.db_logger` is not yet imported in app.py, also add that import. Check with `grep "db_logger" mlss_monitor/app.py`.)

### Step 3.4 — Add `hot_tier.prune_old()` to the `_CYCLE_60S` block

- [ ] **Step 3.4: Add the cleardown call in `_background_log()`**

Find the existing `if _log_cycle % _CYCLE_60S == 0:` block that calls `run_analysis()`. Immediately AFTER all the existing `_CYCLE_60S` blocks (there are currently three — the original `run_analysis` one, the FeatureExtractor one, and the shadow detection engine one), add a fourth:

```python
        # Prune hot_tier DB rows older than 60 minutes to cap table size.
        if _log_cycle % _CYCLE_60S == 0:
            try:
                hot_tier.prune_old()
            except Exception as exc:
                log.error("hot_tier.prune_old error: %s", exc)
```

- [ ] **Step 3.5: Run full suite**

```bash
python -m pytest tests/ -q
```
Expected: 329 passed, no regressions.

- [ ] **Step 3.6: Verify DB import is clean**

```bash
python -c "from mlss_monitor.app import app; print('app import OK')"
```
Expected: `app import OK` (no import errors).

- [ ] **Step 3.7: Commit**

```bash
git add mlss_monitor/app.py tests/conftest.py
git commit -m "feat: wire HotTier db_file into app startup and add 60s prune job"
```

---

## Self-Review

### 1. Spec coverage

| Requirement | Task |
|---|---|
| Hot tier persisted to DB | Task 2: `_insert_row` on every `push()` |
| Loaded on restart | Task 2: `_load_from_db` in `__init__` |
| Only last 60 minutes loaded | Task 2: `WHERE timestamp >= cutoff` in `_load_from_db` |
| Automatic cleardown keeps table ≤ 3600 rows | Task 2: `prune_old()` + Task 3: called every 60s |
| Existing in-memory tests unaffected | Task 2: `db_file=None` default, all original API preserved |
| `conftest.py` patches new module | Task 3: `ht_mod.DB_FILE = path` in `_patch_db()` |
| Table created by `create_db()` | Task 1: `CREATE TABLE IF NOT EXISTS hot_tier` |
| Index on `timestamp` for fast pruning | Task 1: `CREATE INDEX IF NOT EXISTS idx_hot_tier_timestamp` |

All requirements covered. ✅

### 2. Placeholder scan

No TBD/TODO/placeholder text present. All code blocks are complete. ✅

### 3. Type consistency

- `HotTier.__init__(maxlen: int = 3600, db_file: str | None = None)` — used consistently in Task 2 implementation and Task 3 wiring.
- `prune_old()` — defined in Task 2, called in Task 3.
- `_SENSOR_COLS` — used in both `_insert_row` and `_load_from_db`. ✅
