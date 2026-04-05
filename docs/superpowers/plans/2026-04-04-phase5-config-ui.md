# Phase 5 — Configuration UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build four admin-only configuration pages under `/settings/insights-engine/` that let operators edit rules, fingerprints, anomaly thresholds, and data source state at runtime without restarting the Pi. All YAML writes are atomic and thread-safe. Hot-reload is wired directly into existing engine objects via `reload()` methods. Live anomaly scores and fingerprint preview scores are fetched from running engine state via new API endpoints.

**Architecture:** New Flask blueprint `api_insights_bp` in `mlss_monitor/routes/api_insights.py` handles all `/api/insights-engine/` endpoints. Four new page routes are added to `mlss_monitor/routes/pages.py`. Four Jinja2 templates are created under `templates/`. YAML I/O is centralised in a new `mlss_monitor/yaml_io.py` helper that provides atomic write (write-to-temp-then-rename) and an `RLock` for thread safety. Both `RuleEngine` and `AttributionEngine` gain `reload()` methods. `AnomalyDetector` gains `reset_channel()` and `live_scores()` methods. `DataSource` tracking gains an `enabled` flag in `state.py`.

**Tech Stack:** Flask, Jinja2, PyYAML (already installed), `threading.RLock`, vanilla JS fetch (no new JS libraries). All processing is local; no new Python library dependencies.

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `mlss_monitor/yaml_io.py` | **Create** | `atomic_write(path, data)`, `yaml_lock` (RLock), `load_yaml(path)` helpers |
| `mlss_monitor/threshold_engine.py` | **Modify** | Add `reload()` method to `RuleEngine` |
| `mlss_monitor/attribution/engine.py` | **Modify** | Add `reload()` method to `AttributionEngine` |
| `mlss_monitor/anomaly_detector.py` | **Modify** | Add `reset_channel(ch)` and `live_scores()` methods |
| `mlss_monitor/state.py` | **Modify** | Add `data_source_enabled: dict[str, bool]` tracking |
| `mlss_monitor/routes/api_insights.py` | **Create** | All `/api/insights-engine/` REST endpoints (admin-only) |
| `mlss_monitor/routes/pages.py` | **Modify** | Add four page routes under `/settings/insights-engine/` |
| `app.py` | **Modify** | Register `api_insights_bp`; initialise `state.data_source_enabled` |
| `templates/ie_rules.html` | **Create** | Rule manager page |
| `templates/ie_fingerprints.html` | **Create** | Fingerprint manager page |
| `templates/ie_anomaly.html` | **Create** | Anomaly settings page |
| `templates/ie_sources.html` | **Create** | Data source manager page |
| `tests/test_yaml_io.py` | **Create** | Atomic write, lock, load helpers |
| `tests/test_rule_reload.py` | **Create** | `RuleEngine.reload()` picks up YAML changes |
| `tests/test_attribution_reload.py` | **Create** | `AttributionEngine.reload()` picks up YAML changes |
| `tests/test_anomaly_reset.py` | **Create** | `AnomalyDetector.reset_channel()` and `live_scores()` |
| `tests/test_api_insights.py` | **Create** | API endpoint integration tests (Flask test client) |

---

## Shared conventions

### Atomic YAML write

All four managers write YAML via the same helper so no partial write can corrupt a config file mid-read:

```python
# mlss_monitor/yaml_io.py
import os
import tempfile
import threading
from pathlib import Path
import yaml

yaml_lock = threading.RLock()   # one lock covers all YAML files (low contention)


def load_yaml(path: str | Path) -> dict:
    """Load a YAML file under the shared lock. Returns {} on missing file."""
    with yaml_lock:
        p = Path(path)
        if not p.exists():
            return {}
        with open(p) as f:
            return yaml.safe_load(f) or {}


def atomic_write(path: str | Path, data: dict) -> None:
    """Write data to path atomically under the shared lock.

    Writes to a sibling temp file then renames so readers never see a
    partial write. Safe on Linux (Pi OS); rename is atomic on POSIX.
    """
    p = Path(path)
    with yaml_lock:
        fd, tmp_path = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
            os.replace(tmp_path, p)          # atomic on POSIX
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
```

### Admin-only guard

Every endpoint and every page route uses the existing decorator:

```python
from mlss_monitor.rbac import require_role

@bp.route("/api/insights-engine/rules", methods=["POST"])
@require_role("admin")
def save_rules(): ...
```

### JSON error format

All API errors return the standard project shape already used in `api_settings.py`:

```python
return jsonify({"error": "human-readable message"}), 400
```

---

## Task 1 — `mlss_monitor/yaml_io.py` (foundation)

**Files:**
- Create: `mlss_monitor/yaml_io.py`
- Create: `tests/test_yaml_io.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_yaml_io.py`:

```python
"""Tests for atomic YAML write helper."""
from __future__ import annotations

import threading
from pathlib import Path

import pytest
import yaml


def test_atomic_write_creates_file(tmp_path):
    """atomic_write creates the target file with correct YAML content."""
    from mlss_monitor.yaml_io import atomic_write, load_yaml

    target = tmp_path / "test.yaml"
    atomic_write(target, {"key": "value", "num": 42})

    assert target.exists()
    result = load_yaml(target)
    assert result == {"key": "value", "num": 42}


def test_atomic_write_overwrites(tmp_path):
    """atomic_write replaces existing file atomically."""
    from mlss_monitor.yaml_io import atomic_write, load_yaml

    target = tmp_path / "test.yaml"
    atomic_write(target, {"version": 1})
    atomic_write(target, {"version": 2})

    result = load_yaml(target)
    assert result["version"] == 2


def test_atomic_write_no_tmp_left_on_success(tmp_path):
    """No temp files are left behind after a successful write."""
    from mlss_monitor.yaml_io import atomic_write

    target = tmp_path / "test.yaml"
    atomic_write(target, {"x": 1})

    leftover = list(tmp_path.glob("*.tmp"))
    assert leftover == []


def test_load_yaml_missing_file_returns_empty(tmp_path):
    """load_yaml returns {} when the file does not exist."""
    from mlss_monitor.yaml_io import load_yaml

    result = load_yaml(tmp_path / "nonexistent.yaml")
    assert result == {}


def test_concurrent_writes_do_not_corrupt(tmp_path):
    """Concurrent atomic_write calls from multiple threads all succeed."""
    from mlss_monitor.yaml_io import atomic_write, load_yaml

    target = tmp_path / "concurrent.yaml"
    errors = []

    def writer(n):
        try:
            atomic_write(target, {"n": n})
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    # File must be valid YAML (not corrupted mid-write)
    result = load_yaml(target)
    assert "n" in result
```

- [ ] **Step 2: Implement `mlss_monitor/yaml_io.py`**

Write the module exactly as shown in the shared conventions section above.

- [ ] **Step 3: Run tests**

```bash
cd /path/to/project && python -m pytest tests/test_yaml_io.py -v
```

All five tests must pass.

- [ ] **Step 4: Commit**

```bash
git add mlss_monitor/yaml_io.py tests/test_yaml_io.py
git commit -m "feat: add atomic YAML write helper with thread-safe RLock"
```

---

## Task 2 — `RuleEngine.reload()` method

**Files:**
- Modify: `mlss_monitor/threshold_engine.py`
- Create: `tests/test_rule_reload.py`

`RuleEngine` already has a `load()` method that reads and compiles rules from disk. The `reload()` method is a thin thread-safe wrapper that acquires `yaml_lock` before calling `load()`, ensuring no concurrent YAML write races with the read.

- [ ] **Step 1: Write the failing test**

`tests/test_rule_reload.py`:

```python
"""Tests for RuleEngine.reload()."""
from __future__ import annotations

from pathlib import Path
import yaml
import pytest


def _write_rules(path: Path, rules: list[dict]) -> None:
    path.write_text(yaml.dump({"rules": rules}))


def test_reload_picks_up_new_rule(tmp_path):
    """reload() re-reads YAML so a newly added rule becomes active."""
    from mlss_monitor.threshold_engine import RuleEngine

    rules_path = tmp_path / "rules.yaml"
    _write_rules(rules_path, [
        {
            "id": "rule_a",
            "expression": "tvoc_current > 100",
            "event_type": "tvoc_spike",
            "severity": "warning",
            "confidence": 0.8,
            "title_template": "TVOC high",
            "description_template": "TVOC is {tvoc_current:.0f}",
            "action": "Ventilate",
        }
    ])
    engine = RuleEngine(rules_path)
    assert len(engine._rules) == 1

    # Add a second rule to the YAML file
    _write_rules(rules_path, [
        {
            "id": "rule_a",
            "expression": "tvoc_current > 100",
            "event_type": "tvoc_spike",
            "severity": "warning",
            "confidence": 0.8,
            "title_template": "TVOC high",
            "description_template": "TVOC is {tvoc_current:.0f}",
            "action": "Ventilate",
        },
        {
            "id": "rule_b",
            "expression": "eco2_current > 1000",
            "event_type": "eco2_elevated",
            "severity": "warning",
            "confidence": 0.9,
            "title_template": "CO2 elevated",
            "description_template": "CO2 is {eco2_current:.0f}",
            "action": "Ventilate",
        },
    ])
    engine.reload()
    assert len(engine._rules) == 2
    assert engine._rules[1]["id"] == "rule_b"


def test_reload_removes_deleted_rule(tmp_path):
    """reload() reflects deletions: rules removed from YAML stop firing."""
    from mlss_monitor.threshold_engine import RuleEngine

    rules_path = tmp_path / "rules.yaml"
    _write_rules(rules_path, [
        {
            "id": "to_delete",
            "expression": "tvoc_current > 50",
            "event_type": "tvoc_spike",
            "severity": "warning",
            "confidence": 0.7,
            "title_template": "T",
            "description_template": "D",
            "action": "A",
        }
    ])
    engine = RuleEngine(rules_path)
    assert len(engine._rules) == 1

    _write_rules(rules_path, [])
    engine.reload()
    assert len(engine._rules) == 0
    assert len(engine._compiled) == 0


def test_reload_bad_yaml_leaves_previous_rules_intact(tmp_path):
    """reload() on a corrupt YAML file logs the error and keeps old rules."""
    from mlss_monitor.threshold_engine import RuleEngine

    rules_path = tmp_path / "rules.yaml"
    _write_rules(rules_path, [
        {
            "id": "stable",
            "expression": "tvoc_current > 50",
            "event_type": "tvoc_spike",
            "severity": "warning",
            "confidence": 0.7,
            "title_template": "T",
            "description_template": "D",
            "action": "A",
        }
    ])
    engine = RuleEngine(rules_path)
    assert len(engine._rules) == 1

    rules_path.write_text("{{{{ invalid yaml ::::")
    try:
        engine.reload()
    except Exception:
        pass  # acceptable — important thing is rules not silently zeroed
    # Old rules still loaded from before the corrupt write
    assert len(engine._rules) == 1
```

- [ ] **Step 2: Add `reload()` to `RuleEngine`**

In `mlss_monitor/threshold_engine.py`, add after the existing `load()` method:

```python
    def reload(self) -> None:
        """Thread-safe hot-reload: re-read YAML and recompile rules.

        Acquires the shared yaml_lock before reading so an in-progress
        atomic_write from the API handler cannot race with this read.
        If the YAML is malformed the error propagates to the caller;
        the caller (API route) should catch and return HTTP 500.
        """
        from mlss_monitor.yaml_io import yaml_lock
        with yaml_lock:
            self.load()
        log.info("RuleEngine: reloaded %d rules from %s", len(self._rules), self._rules_path.name)
```

- [ ] **Step 3: Run tests**

```bash
python -m pytest tests/test_rule_reload.py -v
```

All three tests must pass.

- [ ] **Step 4: Commit**

```bash
git add mlss_monitor/threshold_engine.py tests/test_rule_reload.py
git commit -m "feat: add RuleEngine.reload() for hot-reload without restart"
```

---

## Task 3 — `AttributionEngine.reload()` method

**Files:**
- Modify: `mlss_monitor/attribution/engine.py`
- Create: `tests/test_attribution_reload.py`

- [ ] **Step 1: Write the failing test**

`tests/test_attribution_reload.py`:

```python
"""Tests for AttributionEngine.reload()."""
from __future__ import annotations

from pathlib import Path
import yaml
import pytest


def _write_fingerprints(path: Path, sources: list[dict]) -> None:
    path.write_text(yaml.dump({"sources": sources}))


_BASE_FP = {
    "id": "test_fp",
    "label": "Test",
    "description": "A test fingerprint",
    "examples": "test",
    "sensors": {"tvoc": "elevated"},
    "temporal": {"rise_rate": "fast"},
    "confidence_floor": 0.5,
    "description_template": "TVOC: {tvoc_current:.0f}",
    "action_template": "Do something.",
}


def test_reload_picks_up_new_fingerprint(tmp_path):
    """reload() loads a fingerprint added to YAML after initial startup."""
    from mlss_monitor.attribution.engine import AttributionEngine

    fp_path = tmp_path / "fingerprints.yaml"
    _write_fingerprints(fp_path, [_BASE_FP])
    engine = AttributionEngine(fp_path)
    assert len(engine._fingerprints) == 1

    second = {**_BASE_FP, "id": "second_fp", "label": "Second"}
    _write_fingerprints(fp_path, [_BASE_FP, second])
    engine.reload()
    assert len(engine._fingerprints) == 2
    assert engine._fingerprints[1].id == "second_fp"


def test_reload_reflects_deletion(tmp_path):
    """reload() removes fingerprints deleted from YAML."""
    from mlss_monitor.attribution.engine import AttributionEngine

    fp_path = tmp_path / "fingerprints.yaml"
    _write_fingerprints(fp_path, [_BASE_FP])
    engine = AttributionEngine(fp_path)
    assert len(engine._fingerprints) == 1

    _write_fingerprints(fp_path, [])
    engine.reload()
    assert len(engine._fingerprints) == 0
```

- [ ] **Step 2: Add `reload()` to `AttributionEngine`**

In `mlss_monitor/attribution/engine.py`, add after `__init__`:

```python
    def reload(self) -> None:
        """Thread-safe hot-reload: re-read fingerprints.yaml.

        Acquires the shared yaml_lock before reading so a concurrent
        atomic_write cannot race with this read.
        """
        from mlss_monitor.yaml_io import yaml_lock
        with yaml_lock:
            self._fingerprints = load_fingerprints(self._config_path)
        log.info(
            "AttributionEngine: reloaded %d fingerprints from %s",
            len(self._fingerprints),
            self._config_path.name,
        )
```

- [ ] **Step 3: Run tests**

```bash
python -m pytest tests/test_attribution_reload.py -v
```

- [ ] **Step 4: Commit**

```bash
git add mlss_monitor/attribution/engine.py tests/test_attribution_reload.py
git commit -m "feat: add AttributionEngine.reload() for hot-reload without restart"
```

---

## Task 4 — `AnomalyDetector.reset_channel()` and `live_scores()`

**Files:**
- Modify: `mlss_monitor/anomaly_detector.py`
- Create: `tests/test_anomaly_reset.py`

`reset_channel()` deletes the `.pkl` file and reinitialises the in-memory model back to a fresh `HalfSpaceTrees`. `live_scores()` returns the most recent EMA-smoothed value per channel (used by the anomaly settings page to show "current score" without needing another full detection cycle).

- [ ] **Step 1: Write the failing tests**

`tests/test_anomaly_reset.py`:

```python
"""Tests for AnomalyDetector.reset_channel() and live_scores()."""
from __future__ import annotations

from pathlib import Path
import yaml
import pytest


def _write_anomaly_config(path: Path) -> None:
    cfg = {
        "anomaly": {
            "algorithm": "half_space_trees",
            "score_threshold": 0.7,
            "cold_start_readings": 2,
            "channels": ["tvoc_ppb", "eco2_ppm"],
        }
    }
    path.write_text(yaml.dump(cfg))


def test_reset_channel_clears_model_and_n_seen(tmp_path):
    """reset_channel reinitialises model and sets n_seen to 0."""
    from mlss_monitor.anomaly_detector import AnomalyDetector

    cfg_path = tmp_path / "anomaly.yaml"
    _write_anomaly_config(cfg_path)
    det = AnomalyDetector(cfg_path, tmp_path / "models")

    # Feed some readings so n_seen > 0
    det._n_seen["tvoc_ppb"] = 100
    det._save_models()
    assert (tmp_path / "models" / "tvoc_ppb.pkl").exists()

    det.reset_channel("tvoc_ppb")

    assert det._n_seen.get("tvoc_ppb", 0) == 0
    assert not (tmp_path / "models" / "tvoc_ppb.pkl").exists()


def test_reset_channel_unknown_channel_no_error(tmp_path):
    """reset_channel on an unknown channel name does not raise."""
    from mlss_monitor.anomaly_detector import AnomalyDetector

    cfg_path = tmp_path / "anomaly.yaml"
    _write_anomaly_config(cfg_path)
    det = AnomalyDetector(cfg_path, tmp_path / "models")
    det.reset_channel("nonexistent_channel")   # must not raise


def test_live_scores_returns_dict_per_channel(tmp_path):
    """live_scores() returns a dict keyed by channel name."""
    from mlss_monitor.anomaly_detector import AnomalyDetector

    cfg_path = tmp_path / "anomaly.yaml"
    _write_anomaly_config(cfg_path)
    det = AnomalyDetector(cfg_path, tmp_path / "models")

    # Before any readings EMA is empty — values should be None
    scores = det.live_scores()
    assert isinstance(scores, dict)
    assert "tvoc_ppb" in scores
    assert scores["tvoc_ppb"] is None   # no readings yet

    # After seeding EMA
    det._ema["tvoc_ppb"] = 350.0
    det._n_seen["tvoc_ppb"] = 600
    scores = det.live_scores()
    assert scores["tvoc_ppb"] == pytest.approx(350.0)
```

- [ ] **Step 2: Add `reset_channel()` and `live_scores()` to `AnomalyDetector`**

In `mlss_monitor/anomaly_detector.py`, add after the `baseline()` method:

```python
    def reset_channel(self, channel: str) -> None:
        """Reset a single channel model and delete its persisted pickle.

        Use when a sensor was faulty for a period and accumulated bad training
        data. After reset the channel re-enters the cold-start suppression
        period and rebuilds from scratch.
        """
        if channel not in self._models:
            log.warning("AnomalyDetector.reset_channel: unknown channel %r", channel)
            return
        self._models[channel] = HalfSpaceTrees(n_trees=10, height=8, window_size=150, seed=42)
        self._n_seen[channel] = 0
        self._ema.pop(channel, None)
        pkl_path = self._model_dir / f"{channel}.pkl"
        try:
            pkl_path.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("AnomalyDetector.reset_channel: could not delete %s: %s", pkl_path, exc)
        log.info("AnomalyDetector: reset channel %r", channel)

    def live_scores(self) -> dict[str, float | None]:
        """Return the most-recent EMA value per channel (not an anomaly score).

        Returns None for channels with no readings yet.  Used by the anomaly
        settings UI to show live sensor levels without running a full detection
        cycle.
        """
        return {ch: self._ema.get(ch) for ch in self._channels()}
```

- [ ] **Step 3: Run tests**

```bash
python -m pytest tests/test_anomaly_reset.py -v
```

All three tests must pass.

- [ ] **Step 4: Commit**

```bash
git add mlss_monitor/anomaly_detector.py tests/test_anomaly_reset.py
git commit -m "feat: add AnomalyDetector.reset_channel() and live_scores()"
```

---

## Task 5 — `state.py` data source enabled tracking

**Files:**
- Modify: `mlss_monitor/state.py`
- Modify: `app.py` (initialise dict at startup)

Data sources have no persistent config for enable/disable (they are hardware-attached). The enabled state lives in `state.data_source_enabled` (an in-memory dict), populated at startup from the registered data sources and persisted only in memory. A Pi restart re-enables all sources.

- [ ] **Step 1: Add `data_source_enabled` to `state.py`**

In `mlss_monitor/state.py`, add after the existing `shadow_log` line:

```python
# Data source enabled/disabled flags (in-memory; reset to True on restart)
# Keys are DataSource.name strings, values are bool.
data_source_enabled: dict[str, bool] = {}
```

- [ ] **Step 2: Initialise from registered sources in `app.py`**

In `app.py`, after the data sources are registered (look for the block where `_data_sources` is populated), add:

```python
# Initialise enabled flags for all registered data sources
from mlss_monitor import state as _state
for _ds in _data_sources:
    _state.data_source_enabled.setdefault(_ds.name, True)
```

- [ ] **Step 3: Commit**

```bash
git add mlss_monitor/state.py app.py
git commit -m "feat: add data_source_enabled dict to state for runtime enable/disable"
```

---

## Task 6 — `mlss_monitor/routes/api_insights.py` (all REST endpoints)

**Files:**
- Create: `mlss_monitor/routes/api_insights.py`
- Create: `tests/test_api_insights.py`

All endpoints are under `/api/insights-engine/` and require `@require_role("admin")`. They use `state.detection_engine` to reach engine objects at runtime.

### Endpoint summary

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/insights-engine/rules` | Return all rules as JSON array |
| POST | `/api/insights-engine/rules` | Replace entire rules array; atomic write + reload |
| PATCH | `/api/insights-engine/rules/<rule_id>` | Update one rule field; atomic write + reload |
| GET | `/api/insights-engine/fingerprints` | Return all fingerprints as JSON array |
| POST | `/api/insights-engine/fingerprints` | Replace entire fingerprints array; atomic write + reload |
| PATCH | `/api/insights-engine/fingerprints/<fp_id>` | Update one fingerprint; atomic write + reload |
| POST | `/api/insights-engine/fingerprints/<fp_id>/preview` | Live score preview against current FeatureVector |
| GET | `/api/insights-engine/anomaly` | Return per-channel config + live EMA values + n_seen |
| POST | `/api/insights-engine/anomaly` | Update score_threshold and/or cold_start_readings; atomic write + reload config |
| POST | `/api/insights-engine/anomaly/<channel>/reset` | reset_channel() on named channel |
| GET | `/api/insights-engine/sources` | Return all sources with enabled flag + last reading timestamp |
| POST | `/api/insights-engine/sources/<name>/enable` | Set `state.data_source_enabled[name] = True` |
| POST | `/api/insights-engine/sources/<name>/disable` | Set `state.data_source_enabled[name] = False` |

- [ ] **Step 1: Write the failing tests**

`tests/test_api_insights.py`:

```python
"""Integration tests for /api/insights-engine/ endpoints.

Uses Flask test client with a minimal app fixture that wires up
state.detection_engine with real engine objects pointing at tmp config files.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture()
def rules_yaml(tmp_path):
    data = {
        "rules": [
            {
                "id": "tvoc_spike",
                "expression": "tvoc_current > 250",
                "event_type": "tvoc_spike",
                "severity": "warning",
                "confidence": 0.8,
                "enabled": True,
                "title_template": "TVOC spike",
                "description_template": "TVOC {tvoc_current:.0f}",
                "action": "Ventilate",
            }
        ]
    }
    p = tmp_path / "rules.yaml"
    p.write_text(yaml.dump(data))
    return p


@pytest.fixture()
def fingerprints_yaml(tmp_path):
    data = {
        "sources": [
            {
                "id": "test_fp",
                "label": "Test",
                "description": "A test",
                "examples": "test",
                "sensors": {"tvoc": "elevated"},
                "temporal": {"rise_rate": "fast"},
                "confidence_floor": 0.5,
                "description_template": "TVOC: {tvoc_current:.0f}",
                "action_template": "Do something.",
            }
        ]
    }
    p = tmp_path / "fingerprints.yaml"
    p.write_text(yaml.dump(data))
    return p


@pytest.fixture()
def anomaly_yaml(tmp_path):
    data = {
        "anomaly": {
            "algorithm": "half_space_trees",
            "score_threshold": 0.7,
            "cold_start_readings": 500,
            "channels": ["tvoc_ppb", "eco2_ppm"],
        }
    }
    p = tmp_path / "anomaly.yaml"
    p.write_text(yaml.dump(data))
    return p


@pytest.fixture()
def app_client(tmp_path, rules_yaml, fingerprints_yaml, anomaly_yaml, monkeypatch):
    """Minimal Flask app wired to real engine objects with tmp config files."""
    from flask import Flask
    from mlss_monitor import state
    from mlss_monitor.threshold_engine import RuleEngine
    from mlss_monitor.attribution.engine import AttributionEngine
    from mlss_monitor.anomaly_detector import AnomalyDetector

    class _FakeEngine:
        _dry_run = True
        _rule_engine = RuleEngine(rules_yaml)
        _attribution_engine = AttributionEngine(fingerprints_yaml)
        _anomaly_detector = AnomalyDetector(anomaly_yaml, tmp_path / "models")
        _multivar_detector = None
        _rules_path = rules_yaml
        _fingerprints_path = fingerprints_yaml
        _anomaly_config_path = anomaly_yaml

    monkeypatch.setattr(state, "detection_engine", _FakeEngine())
    monkeypatch.setattr(state, "data_source_enabled", {"sgp30": True, "aht20": True})
    monkeypatch.setattr(state, "hot_tier", None)

    app = Flask(__name__, template_folder="../templates")
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test"

    # Patch require_role to be a no-op in tests
    import mlss_monitor.rbac as rbac
    monkeypatch.setattr(rbac, "require_role", lambda role: (lambda f: f))

    from mlss_monitor.routes.api_insights import api_insights_bp
    app.register_blueprint(api_insights_bp)

    with app.test_client() as client:
        yield client


# ── Rules endpoint tests ─────────────────────────────────────────────────────

def test_get_rules_returns_list(app_client):
    resp = app_client.get("/api/insights-engine/rules")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)
    assert data[0]["id"] == "tvoc_spike"


def test_patch_rule_updates_severity(app_client, rules_yaml):
    resp = app_client.patch(
        "/api/insights-engine/rules/tvoc_spike",
        json={"severity": "critical"},
        content_type="application/json",
    )
    assert resp.status_code == 200
    # Verify the YAML was written
    loaded = yaml.safe_load(rules_yaml.read_text())
    assert loaded["rules"][0]["severity"] == "critical"


def test_patch_nonexistent_rule_returns_404(app_client):
    resp = app_client.patch(
        "/api/insights-engine/rules/does_not_exist",
        json={"severity": "critical"},
        content_type="application/json",
    )
    assert resp.status_code == 404


# ── Fingerprints endpoint tests ──────────────────────────────────────────────

def test_get_fingerprints_returns_list(app_client):
    resp = app_client.get("/api/insights-engine/fingerprints")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)
    assert data[0]["id"] == "test_fp"


def test_patch_fingerprint_updates_floor(app_client, fingerprints_yaml):
    resp = app_client.patch(
        "/api/insights-engine/fingerprints/test_fp",
        json={"confidence_floor": 0.75},
        content_type="application/json",
    )
    assert resp.status_code == 200
    loaded = yaml.safe_load(fingerprints_yaml.read_text())
    assert loaded["sources"][0]["confidence_floor"] == pytest.approx(0.75)


# ── Anomaly endpoint tests ───────────────────────────────────────────────────

def test_get_anomaly_returns_channels(app_client):
    resp = app_client.get("/api/insights-engine/anomaly")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "channels" in data
    ch = data["channels"]
    assert any(c["channel"] == "tvoc_ppb" for c in ch)


def test_reset_channel(app_client):
    resp = app_client.post("/api/insights-engine/anomaly/tvoc_ppb/reset")
    assert resp.status_code == 200


def test_reset_unknown_channel_returns_404(app_client):
    resp = app_client.post("/api/insights-engine/anomaly/nonexistent/reset")
    assert resp.status_code == 404


# ── Sources endpoint tests ───────────────────────────────────────────────────

def test_get_sources_returns_list(app_client):
    resp = app_client.get("/api/insights-engine/sources")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)
    names = [s["name"] for s in data]
    assert "sgp30" in names


def test_disable_source(app_client):
    resp = app_client.post("/api/insights-engine/sources/sgp30/disable")
    assert resp.status_code == 200
    from mlss_monitor import state
    assert state.data_source_enabled["sgp30"] is False


def test_enable_source(app_client):
    from mlss_monitor import state
    state.data_source_enabled["aht20"] = False
    resp = app_client.post("/api/insights-engine/sources/aht20/enable")
    assert resp.status_code == 200
    assert state.data_source_enabled["aht20"] is True


def test_enable_unknown_source_returns_404(app_client):
    resp = app_client.post("/api/insights-engine/sources/nonexistent/enable")
    assert resp.status_code == 404
```

- [ ] **Step 2: Implement `mlss_monitor/routes/api_insights.py`**

```python
"""Insights Engine configuration API.

All routes require admin role. Writes are atomic via yaml_io.atomic_write.
Engine objects are reloaded in-place after each write (no restart required).
"""
from __future__ import annotations

import dataclasses
import logging

import yaml
from flask import Blueprint, jsonify, request

from mlss_monitor import state
from mlss_monitor.rbac import require_role
from mlss_monitor.yaml_io import atomic_write, load_yaml

log = logging.getLogger(__name__)
api_insights_bp = Blueprint("api_insights", __name__)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _engine():
    """Return the live DetectionEngine or raise RuntimeError."""
    eng = state.detection_engine
    if eng is None:
        raise RuntimeError("DetectionEngine not initialised")
    return eng


def _not_initialised():
    return jsonify({"error": "Detection engine not initialised"}), 503


# ── Rules ────────────────────────────────────────────────────────────────────

@api_insights_bp.route("/api/insights-engine/rules", methods=["GET"])
@require_role("admin")
def get_rules():
    try:
        eng = _engine()
    except RuntimeError:
        return _not_initialised()
    rules = eng._rule_engine._rules
    return jsonify(list(rules))


@api_insights_bp.route("/api/insights-engine/rules", methods=["POST"])
@require_role("admin")
def save_rules():
    try:
        eng = _engine()
    except RuntimeError:
        return _not_initialised()
    data = request.get_json()
    if not isinstance(data, list):
        return jsonify({"error": "Expected JSON array of rules"}), 400
    rules_path = eng._rules_path
    try:
        atomic_write(rules_path, {"rules": data})
        eng._rule_engine.reload()
    except Exception as exc:
        log.error("save_rules: %s", exc)
        return jsonify({"error": str(exc)}), 500
    return jsonify({"message": f"{len(data)} rule(s) saved and reloaded"})


@api_insights_bp.route("/api/insights-engine/rules/<rule_id>", methods=["PATCH"])
@require_role("admin")
def patch_rule(rule_id: str):
    try:
        eng = _engine()
    except RuntimeError:
        return _not_initialised()
    updates = request.get_json() or {}
    rules_path = eng._rules_path
    raw = load_yaml(rules_path)
    rules = raw.get("rules", [])
    for rule in rules:
        if rule.get("id") == rule_id:
            rule.update(updates)
            try:
                atomic_write(rules_path, {"rules": rules})
                eng._rule_engine.reload()
            except Exception as exc:
                log.error("patch_rule %r: %s", rule_id, exc)
                return jsonify({"error": str(exc)}), 500
            return jsonify({"message": f"Rule {rule_id!r} updated"})
    return jsonify({"error": f"Rule {rule_id!r} not found"}), 404


# ── Fingerprints ─────────────────────────────────────────────────────────────

@api_insights_bp.route("/api/insights-engine/fingerprints", methods=["GET"])
@require_role("admin")
def get_fingerprints():
    try:
        eng = _engine()
    except RuntimeError:
        return _not_initialised()
    fps = [dataclasses.asdict(fp) for fp in eng._attribution_engine._fingerprints]
    return jsonify(fps)


@api_insights_bp.route("/api/insights-engine/fingerprints", methods=["POST"])
@require_role("admin")
def save_fingerprints():
    try:
        eng = _engine()
    except RuntimeError:
        return _not_initialised()
    data = request.get_json()
    if not isinstance(data, list):
        return jsonify({"error": "Expected JSON array of fingerprints"}), 400
    fp_path = eng._fingerprints_path
    try:
        atomic_write(fp_path, {"sources": data})
        eng._attribution_engine.reload()
    except Exception as exc:
        log.error("save_fingerprints: %s", exc)
        return jsonify({"error": str(exc)}), 500
    return jsonify({"message": f"{len(data)} fingerprint(s) saved and reloaded"})


@api_insights_bp.route("/api/insights-engine/fingerprints/<fp_id>", methods=["PATCH"])
@require_role("admin")
def patch_fingerprint(fp_id: str):
    try:
        eng = _engine()
    except RuntimeError:
        return _not_initialised()
    updates = request.get_json() or {}
    fp_path = eng._fingerprints_path
    raw = load_yaml(fp_path)
    sources = raw.get("sources", [])
    for src in sources:
        if src.get("id") == fp_id:
            src.update(updates)
            try:
                atomic_write(fp_path, {"sources": sources})
                eng._attribution_engine.reload()
            except Exception as exc:
                log.error("patch_fingerprint %r: %s", fp_id, exc)
                return jsonify({"error": str(exc)}), 500
            return jsonify({"message": f"Fingerprint {fp_id!r} updated"})
    return jsonify({"error": f"Fingerprint {fp_id!r} not found"}), 404


@api_insights_bp.route("/api/insights-engine/fingerprints/<fp_id>/preview", methods=["POST"])
@require_role("admin")
def preview_fingerprint(fp_id: str):
    """Score the named fingerprint against the current live FeatureVector.

    The FeatureVector is taken from state.feature_vector (updated every 60s
    by the detection cycle). Returns sensor_score, temporal_score, combined
    confidence. Returns 503 if no FeatureVector is available yet.
    """
    try:
        eng = _engine()
    except RuntimeError:
        return _not_initialised()

    fv = state.feature_vector
    if fv is None:
        return jsonify({"error": "No feature vector available yet (cold start)"}), 503

    # Find the fingerprint in the live attribution engine
    fp = next(
        (f for f in eng._attribution_engine._fingerprints if f.id == fp_id),
        None,
    )
    if fp is None:
        return jsonify({"error": f"Fingerprint {fp_id!r} not found"}), 404

    from mlss_monitor.attribution.scorer import sensor_score, temporal_score, combine
    ss = sensor_score(fp, fv)
    ts = temporal_score(fp, fv)
    conf = combine(ss, ts)
    return jsonify({
        "fingerprint_id": fp_id,
        "sensor_score": round(ss, 4),
        "temporal_score": round(ts, 4),
        "confidence": round(conf, 4),
        "clears_floor": conf >= fp.confidence_floor,
        "confidence_floor": fp.confidence_floor,
    })


# ── Anomaly ──────────────────────────────────────────────────────────────────

@api_insights_bp.route("/api/insights-engine/anomaly", methods=["GET"])
@require_role("admin")
def get_anomaly():
    try:
        eng = _engine()
    except RuntimeError:
        return _not_initialised()
    det = eng._anomaly_detector
    cfg = det._config
    live = det.live_scores()
    channels = []
    for ch in det._channels():
        n = det._n_seen.get(ch, 0)
        cold_start = cfg.get("cold_start_readings", 1440)
        channels.append({
            "channel": ch,
            "n_seen": n,
            "cold_start": cold_start,
            "ready": n >= cold_start,
            "live_ema": live.get(ch),
        })
    return jsonify({
        "score_threshold": cfg.get("score_threshold", 0.7),
        "cold_start_readings": cfg.get("cold_start_readings", 1440),
        "channels": channels,
    })


@api_insights_bp.route("/api/insights-engine/anomaly", methods=["POST"])
@require_role("admin")
def save_anomaly():
    try:
        eng = _engine()
    except RuntimeError:
        return _not_initialised()
    data = request.get_json() or {}
    anomaly_path = eng._anomaly_config_path
    raw = load_yaml(anomaly_path)
    anomaly_cfg = raw.get("anomaly", {})

    if "score_threshold" in data:
        try:
            val = float(data["score_threshold"])
            if not (0.0 <= val <= 1.0):
                raise ValueError("out of range")
            anomaly_cfg["score_threshold"] = val
        except (TypeError, ValueError) as exc:
            return jsonify({"error": f"score_threshold: {exc}"}), 400

    if "cold_start_readings" in data:
        try:
            val = int(data["cold_start_readings"])
            if val < 0:
                raise ValueError("must be >= 0")
            anomaly_cfg["cold_start_readings"] = val
        except (TypeError, ValueError) as exc:
            return jsonify({"error": f"cold_start_readings: {exc}"}), 400

    try:
        atomic_write(anomaly_path, {"anomaly": anomaly_cfg})
        eng._anomaly_detector._load_config()   # reload from file
    except Exception as exc:
        log.error("save_anomaly: %s", exc)
        return jsonify({"error": str(exc)}), 500
    return jsonify({"message": "Anomaly config updated"})


@api_insights_bp.route("/api/insights-engine/anomaly/<channel>/reset", methods=["POST"])
@require_role("admin")
def reset_anomaly_channel(channel: str):
    try:
        eng = _engine()
    except RuntimeError:
        return _not_initialised()
    det = eng._anomaly_detector
    if channel not in det._models:
        return jsonify({"error": f"Channel {channel!r} not found"}), 404
    det.reset_channel(channel)
    return jsonify({"message": f"Channel {channel!r} reset"})


# ── Data sources ─────────────────────────────────────────────────────────────

@api_insights_bp.route("/api/insights-engine/sources", methods=["GET"])
@require_role("admin")
def get_sources():
    enabled_map = state.data_source_enabled
    result = []
    for name, enabled in enabled_map.items():
        result.append({
            "name": name,
            "enabled": enabled,
            "status": "active" if enabled else "disabled",
        })
    return jsonify(result)


@api_insights_bp.route("/api/insights-engine/sources/<name>/enable", methods=["POST"])
@require_role("admin")
def enable_source(name: str):
    if name not in state.data_source_enabled:
        return jsonify({"error": f"Source {name!r} not found"}), 404
    state.data_source_enabled[name] = True
    return jsonify({"message": f"Source {name!r} enabled"})


@api_insights_bp.route("/api/insights-engine/sources/<name>/disable", methods=["POST"])
@require_role("admin")
def disable_source(name: str):
    if name not in state.data_source_enabled:
        return jsonify({"error": f"Source {name!r} not found"}), 404
    state.data_source_enabled[name] = False
    return jsonify({"message": f"Source {name!r} disabled"})
```

- [ ] **Step 3: Run tests**

```bash
python -m pytest tests/test_api_insights.py -v
```

All tests must pass.

- [ ] **Step 4: Register the blueprint in `app.py`**

Find where other blueprints are registered (look for `api_settings_bp`) and add:

```python
from mlss_monitor.routes.api_insights import api_insights_bp
app.register_blueprint(api_insights_bp)
```

Also add the paths to `DetectionEngine` construction so the blueprint can reach them:

```python
# In DetectionEngine.__init__ (mlss_monitor/detection_engine.py), store paths:
self._rules_path = Path(rules_path)
self._fingerprints_path = Path(fingerprints_path)
self._anomaly_config_path = Path(anomaly_config_path)
```

Verify `DetectionEngine.__init__` already receives all three paths; if `fingerprints_path` or `anomaly_config_path` are not already stored as instance attributes, add them. Check construction in `app.py` and `detection_engine.py` and wire accordingly.

- [ ] **Step 5: Commit**

```bash
git add mlss_monitor/routes/api_insights.py tests/test_api_insights.py app.py mlss_monitor/detection_engine.py
git commit -m "feat: add /api/insights-engine/ REST API (rules, fingerprints, anomaly, sources)"
```

---

## Task 7 — Page routes in `pages.py`

**Files:**
- Modify: `mlss_monitor/routes/pages.py`

Four new page routes. Each renders a dedicated template. The routes themselves are thin — all data is fetched by the template via JS calls to the API endpoints added in Task 6.

- [ ] **Step 1: Add four routes to `pages.py`**

```python
@pages_bp.route("/settings/insights-engine/rules")
@require_role("admin")
def ie_rules():
    return render_template("ie_rules.html")


@pages_bp.route("/settings/insights-engine/fingerprints")
@require_role("admin")
def ie_fingerprints():
    return render_template("ie_fingerprints.html")


@pages_bp.route("/settings/insights-engine/anomaly")
@require_role("admin")
def ie_anomaly():
    return render_template("ie_anomaly.html")


@pages_bp.route("/settings/insights-engine/sources")
@require_role("admin")
def ie_sources():
    return render_template("ie_sources.html")
```

- [ ] **Step 2: Add navigation links to the existing `insights_engine.html` admin page**

In the existing `templates/insights_engine.html`, find the top navigation card or controls section and add a row of links:

```html
<!-- Add near the top of {% block content %}, after the Detection Engine card -->
<div class="card" style="margin-top:1rem;">
  <h3>⚙️ Configuration</h3>
  <div style="display:flex;gap:1rem;flex-wrap:wrap;margin-top:.75rem;">
    <a class="btn btn-secondary" href="/settings/insights-engine/rules">Rule Manager</a>
    <a class="btn btn-secondary" href="/settings/insights-engine/fingerprints">Fingerprint Manager</a>
    <a class="btn btn-secondary" href="/settings/insights-engine/anomaly">Anomaly Settings</a>
    <a class="btn btn-secondary" href="/settings/insights-engine/sources">Data Sources</a>
  </div>
</div>
```

- [ ] **Step 3: Commit**

```bash
git add mlss_monitor/routes/pages.py templates/insights_engine.html
git commit -m "feat: add four insights-engine config page routes + nav links"
```

---

## Task 8 — Rule manager template (`templates/ie_rules.html`)

**Files:**
- Create: `templates/ie_rules.html`

The rule manager fetches rules on page load, renders them in a table with inline edit controls, and posts changes back to the API. All state management is in vanilla JS. No build step required.

- [ ] **Step 1: Create `templates/ie_rules.html`**

```html
{% extends "base.html" %}
{% block title %}MLSS – Rule Manager{% endblock %}
{% block extra_css %}
  <link rel="stylesheet" href="{{ url_for('static', filename='css/admin.css') }}">
  <style>
    .rules-table { width:100%; border-collapse:collapse; font-size:.85rem; }
    .rules-table th, .rules-table td { padding:.4rem .6rem; border-bottom:1px solid #333; vertical-align:top; }
    .rules-table thead th { text-align:left; border-bottom:2px solid #555; }
    .rule-expr { font-family:monospace; font-size:.8rem; }
    .badge-warning  { background:#a06000; color:#fff; border-radius:3px; padding:1px 5px; font-size:.75rem; }
    .badge-critical { background:#8b0000; color:#fff; border-radius:3px; padding:1px 5px; font-size:.75rem; }
    .badge-info     { background:#004080; color:#fff; border-radius:3px; padding:1px 5px; font-size:.75rem; }
    .status-msg { margin-top:.5rem; font-size:.85rem; color:#4caf50; min-height:1.2em; }
    .status-err { color:#f44336; }
    input.inline-edit { background:#1a1a1a; border:1px solid #555; color:#eee; padding:2px 4px; border-radius:3px; width:100%; box-sizing:border-box; }
    .disabled-row { opacity:.45; }
  </style>
{% endblock %}
{% block topbar_controls %}
  <a href="/insights-engine">← Back to Insights Engine</a>
{% endblock %}
{% block content %}
<div style="max-width:1100px;margin:1.5rem auto;padding:0 1rem;">
  <div class="card">
    <h3>📋 Rule Manager</h3>
    <p style="color:#888;font-size:.85rem;margin:.4rem 0 .8rem;">
      Changes write to <code>config/rules.yaml</code> and hot-reload immediately. No restart required.
    </p>
    <div id="status-msg" class="status-msg"></div>
    <table class="rules-table" id="rules-table">
      <thead>
        <tr>
          <th>ID</th>
          <th>Expression</th>
          <th>Severity</th>
          <th>Confidence</th>
          <th>Enabled</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody id="rules-body">
        <tr><td colspan="6" style="color:#888;">Loading…</td></tr>
      </tbody>
    </table>
  </div>
</div>

<script>
const statusEl = document.getElementById('status-msg');
function showStatus(msg, isError=false) {
  statusEl.textContent = msg;
  statusEl.className = 'status-msg' + (isError ? ' status-err' : '');
  setTimeout(() => { statusEl.textContent = ''; statusEl.className = 'status-msg'; }, 4000);
}

async function loadRules() {
  const resp = await fetch('/api/insights-engine/rules');
  if (!resp.ok) { showStatus('Failed to load rules', true); return; }
  const rules = await resp.json();
  renderRules(rules);
}

function renderRules(rules) {
  const tbody = document.getElementById('rules-body');
  tbody.innerHTML = '';
  rules.forEach(rule => {
    const enabled = rule.enabled !== false;
    const tr = document.createElement('tr');
    if (!enabled) tr.classList.add('disabled-row');
    tr.innerHTML = `
      <td><code>${escHtml(rule.id)}</code></td>
      <td><input class="inline-edit rule-expr" data-id="${escHtml(rule.id)}" data-field="expression"
                 value="${escHtml(rule.expression)}" title="Edit expression"></td>
      <td>
        <select class="inline-edit" data-id="${escHtml(rule.id)}" data-field="severity">
          ${['critical','warning','info'].map(s =>
            `<option value="${s}" ${rule.severity===s?'selected':''}>${s}</option>`).join('')}
        </select>
      </td>
      <td><input class="inline-edit" type="number" step="0.05" min="0" max="1"
                 data-id="${escHtml(rule.id)}" data-field="confidence"
                 value="${rule.confidence}" style="width:5rem;"></td>
      <td>
        <input type="checkbox" data-id="${escHtml(rule.id)}" data-field="enabled"
               ${enabled?'checked':''} class="toggle-enabled">
      </td>
      <td><button class="btn btn-sm" onclick="saveRule('${escHtml(rule.id)}')">Save</button></td>`;
    tbody.appendChild(tr);
  });
}

async function saveRule(ruleId) {
  const row = [...document.querySelectorAll('[data-id]')]
    .filter(el => el.dataset.id === ruleId);
  const patch = {};
  row.forEach(el => {
    if (el.dataset.field === 'enabled') {
      patch.enabled = el.checked;
    } else if (el.dataset.field === 'confidence') {
      patch.confidence = parseFloat(el.value);
    } else {
      patch[el.dataset.field] = el.value;
    }
  });
  const resp = await fetch(`/api/insights-engine/rules/${ruleId}`, {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(patch),
  });
  const data = await resp.json();
  if (resp.ok) {
    showStatus(data.message || 'Saved');
    loadRules();
  } else {
    showStatus(data.error || 'Save failed', true);
  }
}

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

loadRules();
</script>
{% endblock %}
```

- [ ] **Step 2: Smoke-test in browser**

Navigate to `/settings/insights-engine/rules` and verify the table renders with real rule data, edits post correctly, and the status message appears.

- [ ] **Step 3: Commit**

```bash
git add templates/ie_rules.html
git commit -m "feat: add Rule Manager template (/settings/insights-engine/rules)"
```

---

## Task 9 — Fingerprint manager template (`templates/ie_fingerprints.html`)

**Files:**
- Create: `templates/ie_fingerprints.html`

The fingerprint manager lists source fingerprints with their sensor states and confidence floor. Each fingerprint has a "Preview score" button that hits the `/preview` endpoint using the current live readings and displays the resulting confidence inline.

- [ ] **Step 1: Create `templates/ie_fingerprints.html`**

```html
{% extends "base.html" %}
{% block title %}MLSS – Fingerprint Manager{% endblock %}
{% block extra_css %}
  <link rel="stylesheet" href="{{ url_for('static', filename='css/admin.css') }}">
  <style>
    .fp-table { width:100%; border-collapse:collapse; font-size:.85rem; }
    .fp-table th, .fp-table td { padding:.4rem .6rem; border-bottom:1px solid #333; vertical-align:top; }
    .fp-table thead th { text-align:left; border-bottom:2px solid #555; }
    .sensor-chips span { display:inline-block; background:#222; border:1px solid #444;
                         border-radius:3px; padding:1px 5px; margin:1px; font-size:.75rem; }
    .status-msg { margin-top:.5rem; font-size:.85rem; color:#4caf50; min-height:1.2em; }
    .status-err { color:#f44336; }
    .preview-result { font-size:.8rem; color:#80cbc4; margin-top:.25rem; }
    input.inline-edit { background:#1a1a1a; border:1px solid #555; color:#eee;
                        padding:2px 4px; border-radius:3px; width:100%; box-sizing:border-box; }
  </style>
{% endblock %}
{% block topbar_controls %}
  <a href="/insights-engine">← Back to Insights Engine</a>
{% endblock %}
{% block content %}
<div style="max-width:1100px;margin:1.5rem auto;padding:0 1rem;">
  <div class="card">
    <h3>🔍 Fingerprint Manager</h3>
    <p style="color:#888;font-size:.85rem;margin:.4rem 0 .8rem;">
      Changes write to <code>config/fingerprints.yaml</code> and hot-reload immediately.
      Preview score uses current live sensor readings.
    </p>
    <div id="status-msg" class="status-msg"></div>
    <table class="fp-table" id="fp-table">
      <thead>
        <tr>
          <th>ID</th>
          <th>Label</th>
          <th>Sensor states</th>
          <th>Conf. floor</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody id="fp-body">
        <tr><td colspan="5" style="color:#888;">Loading…</td></tr>
      </tbody>
    </table>
  </div>
</div>

<script>
const statusEl = document.getElementById('status-msg');
function showStatus(msg, isError=false) {
  statusEl.textContent = msg;
  statusEl.className = 'status-msg' + (isError ? ' status-err' : '');
  setTimeout(() => { statusEl.textContent = ''; statusEl.className = 'status-msg'; }, 5000);
}

async function loadFingerprints() {
  const resp = await fetch('/api/insights-engine/fingerprints');
  if (!resp.ok) { showStatus('Failed to load fingerprints', true); return; }
  const fps = await resp.json();
  renderFingerprints(fps);
}

function renderFingerprints(fps) {
  const tbody = document.getElementById('fp-body');
  tbody.innerHTML = '';
  fps.forEach(fp => {
    const sensorChips = Object.entries(fp.sensors || {})
      .map(([k,v]) => `<span>${escHtml(k)}: <b>${escHtml(v)}</b></span>`).join('');
    const tr = document.createElement('tr');
    tr.id = `fp-row-${fp.id}`;
    tr.innerHTML = `
      <td><code>${escHtml(fp.id)}</code></td>
      <td>${escHtml(fp.label)}</td>
      <td><div class="sensor-chips">${sensorChips}</div></td>
      <td>
        <input class="inline-edit" type="number" step="0.05" min="0" max="1"
               id="floor-${escHtml(fp.id)}" value="${fp.confidence_floor}" style="width:5rem;">
      </td>
      <td>
        <button class="btn btn-sm" onclick="saveFloor('${escHtml(fp.id)}')">Save floor</button>
        <button class="btn btn-sm btn-secondary" onclick="previewFp('${escHtml(fp.id)}')">Preview score</button>
        <div class="preview-result" id="preview-${escHtml(fp.id)}"></div>
      </td>`;
    tbody.appendChild(tr);
  });
}

async function saveFloor(fpId) {
  const floor = parseFloat(document.getElementById(`floor-${fpId}`).value);
  const resp = await fetch(`/api/insights-engine/fingerprints/${fpId}`, {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({confidence_floor: floor}),
  });
  const data = await resp.json();
  if (resp.ok) { showStatus(data.message || 'Saved'); }
  else { showStatus(data.error || 'Save failed', true); }
}

async function previewFp(fpId) {
  const el = document.getElementById(`preview-${fpId}`);
  el.textContent = 'Scoring…';
  const resp = await fetch(`/api/insights-engine/fingerprints/${fpId}/preview`, {method:'POST'});
  if (resp.status === 503) { el.textContent = 'No live data yet (cold start)'; return; }
  const data = await resp.json();
  if (!resp.ok) { el.textContent = data.error || 'Preview failed'; return; }
  const clears = data.clears_floor ? '✓ clears floor' : '✗ below floor';
  el.textContent = `conf=${data.confidence.toFixed(3)} (sensor=${data.sensor_score.toFixed(3)}, temporal=${data.temporal_score.toFixed(3)}) — ${clears}`;
}

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

loadFingerprints();
</script>
{% endblock %}
```

- [ ] **Step 2: Smoke-test**

Navigate to `/settings/insights-engine/fingerprints`. Verify fingerprints list renders. Click "Preview score" — if a live FeatureVector is available the scores appear inline; if not, a "cold start" message is shown.

- [ ] **Step 3: Commit**

```bash
git add templates/ie_fingerprints.html
git commit -m "feat: add Fingerprint Manager template with live score preview"
```

---

## Task 10 — Anomaly settings template (`templates/ie_anomaly.html`)

**Files:**
- Create: `templates/ie_anomaly.html`

The anomaly settings page shows a per-channel table with current n_seen, cold-start threshold, and whether the channel is ready. A global threshold slider and cold-start count are editable. Each channel has a reset button.

- [ ] **Step 1: Create `templates/ie_anomaly.html`**

```html
{% extends "base.html" %}
{% block title %}MLSS – Anomaly Settings{% endblock %}
{% block extra_css %}
  <link rel="stylesheet" href="{{ url_for('static', filename='css/admin.css') }}">
  <style>
    .anomaly-table { width:100%; border-collapse:collapse; font-size:.85rem; }
    .anomaly-table th, .anomaly-table td { padding:.4rem .6rem; border-bottom:1px solid #333; }
    .anomaly-table thead th { text-align:left; border-bottom:2px solid #555; }
    .badge-ready   { background:#1b5e20; color:#fff; border-radius:3px; padding:1px 6px; font-size:.75rem; }
    .badge-learning{ background:#4a3800; color:#fff; border-radius:3px; padding:1px 6px; font-size:.75rem; }
    .status-msg { font-size:.85rem; color:#4caf50; min-height:1.2em; margin:.5rem 0; }
    .status-err { color:#f44336; }
    .global-form label { display:inline-block; width:16rem; }
    .global-form input[type=range] { vertical-align:middle; }
  </style>
{% endblock %}
{% block topbar_controls %}
  <a href="/insights-engine">← Back to Insights Engine</a>
{% endblock %}
{% block content %}
<div style="max-width:900px;margin:1.5rem auto;padding:0 1rem;">

  <div class="card" style="margin-bottom:1rem;">
    <h3>⚙️ Global Anomaly Settings</h3>
    <div id="status-global" class="status-msg"></div>
    <div class="global-form" style="margin-top:.75rem;">
      <label>Score threshold: <span id="threshold-label">0.70</span></label>
      <input type="range" id="threshold-slider" min="0" max="1" step="0.01" value="0.70"
             oninput="document.getElementById('threshold-label').textContent=parseFloat(this.value).toFixed(2)">
      <br><br>
      <label>Cold-start readings:</label>
      <input type="number" id="cold-start-input" min="0" max="10000" value="500"
             style="width:6rem;background:#1a1a1a;border:1px solid #555;color:#eee;padding:3px 6px;border-radius:3px;">
      <br><br>
      <button class="btn" onclick="saveGlobal()">Save global settings</button>
    </div>
  </div>

  <div class="card">
    <h3>📊 Channel Status</h3>
    <p style="color:#888;font-size:.85rem;margin:.3rem 0 .8rem;">
      EMA values shown are sensor-level exponential moving averages used internally by the anomaly model.
      Reset a channel if its model was trained on faulty data.
    </p>
    <div id="status-channel" class="status-msg"></div>
    <table class="anomaly-table" id="anomaly-table">
      <thead>
        <tr>
          <th>Channel</th>
          <th>Readings seen</th>
          <th>Cold-start target</th>
          <th>Status</th>
          <th>EMA value</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody id="anomaly-body">
        <tr><td colspan="6" style="color:#888;">Loading…</td></tr>
      </tbody>
    </table>
  </div>
</div>

<script>
function showStatus(id, msg, isError=false) {
  const el = document.getElementById(id);
  el.textContent = msg;
  el.className = 'status-msg' + (isError ? ' status-err' : '');
  setTimeout(() => { el.textContent = ''; el.className = 'status-msg'; }, 4000);
}

async function loadAnomalyData() {
  const resp = await fetch('/api/insights-engine/anomaly');
  if (!resp.ok) { showStatus('status-channel', 'Failed to load anomaly data', true); return; }
  const data = await resp.json();

  document.getElementById('threshold-slider').value = data.score_threshold;
  document.getElementById('threshold-label').textContent = data.score_threshold.toFixed(2);
  document.getElementById('cold-start-input').value = data.cold_start_readings;

  const tbody = document.getElementById('anomaly-body');
  tbody.innerHTML = '';
  (data.channels || []).forEach(ch => {
    const badge = ch.ready
      ? '<span class="badge-ready">Ready</span>'
      : `<span class="badge-learning">Learning (${ch.n_seen}/${ch.cold_start})</span>`;
    const ema = ch.live_ema != null ? ch.live_ema.toFixed(2) : '—';
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><code>${escHtml(ch.channel)}</code></td>
      <td>${ch.n_seen.toLocaleString()}</td>
      <td>${ch.cold_start.toLocaleString()}</td>
      <td>${badge}</td>
      <td>${ema}</td>
      <td><button class="btn btn-sm btn-danger" onclick="resetChannel('${escHtml(ch.channel)}')">Reset model</button></td>`;
    tbody.appendChild(tr);
  });
}

async function saveGlobal() {
  const threshold = parseFloat(document.getElementById('threshold-slider').value);
  const coldStart = parseInt(document.getElementById('cold-start-input').value, 10);
  const resp = await fetch('/api/insights-engine/anomaly', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({score_threshold: threshold, cold_start_readings: coldStart}),
  });
  const data = await resp.json();
  if (resp.ok) { showStatus('status-global', data.message || 'Saved'); loadAnomalyData(); }
  else { showStatus('status-global', data.error || 'Save failed', true); }
}

async function resetChannel(channel) {
  if (!confirm(`Reset anomaly model for ${channel}? The model will restart cold-start learning.`)) return;
  const resp = await fetch(`/api/insights-engine/anomaly/${channel}/reset`, {method:'POST'});
  const data = await resp.json();
  if (resp.ok) { showStatus('status-channel', data.message || 'Reset'); loadAnomalyData(); }
  else { showStatus('status-channel', data.error || 'Reset failed', true); }
}

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

loadAnomalyData();
setInterval(loadAnomalyData, 30000);   // refresh EMA values every 30s
</script>
{% endblock %}
```

- [ ] **Step 2: Smoke-test**

Navigate to `/settings/insights-engine/anomaly`. Verify channel table renders, threshold slider changes label in real time, reset button triggers a confirm dialog, and the table reloads after saving.

- [ ] **Step 3: Commit**

```bash
git add templates/ie_anomaly.html
git commit -m "feat: add Anomaly Settings template with per-channel reset and live EMA"
```

---

## Task 11 — Data source manager template (`templates/ie_sources.html`)

**Files:**
- Create: `templates/ie_sources.html`

The data source manager shows all registered sources with their enabled state and a toggle button. Because last-reading timestamps are not yet stored on the source object (DataSource ABC has no `last_reading_at` field), the `GET /api/insights-engine/sources` response includes a `status` field only. A future enhancement can add timestamp tracking to the DataSource ABC.

- [ ] **Step 1: Create `templates/ie_sources.html`**

```html
{% extends "base.html" %}
{% block title %}MLSS – Data Sources{% endblock %}
{% block extra_css %}
  <link rel="stylesheet" href="{{ url_for('static', filename='css/admin.css') }}">
  <style>
    .sources-table { width:100%; border-collapse:collapse; font-size:.85rem; }
    .sources-table th, .sources-table td { padding:.4rem .6rem; border-bottom:1px solid #333; }
    .sources-table thead th { text-align:left; border-bottom:2px solid #555; }
    .badge-active   { background:#1b5e20; color:#fff; border-radius:3px; padding:1px 6px; font-size:.75rem; }
    .badge-disabled { background:#4a0000; color:#fff; border-radius:3px; padding:1px 6px; font-size:.75rem; }
    .status-msg { font-size:.85rem; color:#4caf50; min-height:1.2em; margin:.5rem 0; }
    .status-err { color:#f44336; }
  </style>
{% endblock %}
{% block topbar_controls %}
  <a href="/insights-engine">← Back to Insights Engine</a>
{% endblock %}
{% block content %}
<div style="max-width:800px;margin:1.5rem auto;padding:0 1rem;">
  <div class="card">
    <h3>🔌 Data Source Manager</h3>
    <p style="color:#888;font-size:.85rem;margin:.4rem 0 .8rem;">
      Disable a source to exclude its readings from the detection pipeline.
      Enable state resets to active on service restart.
    </p>
    <div id="status-msg" class="status-msg"></div>
    <table class="sources-table" id="sources-table">
      <thead>
        <tr>
          <th>Source</th>
          <th>Status</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody id="sources-body">
        <tr><td colspan="3" style="color:#888;">Loading…</td></tr>
      </tbody>
    </table>
  </div>
</div>

<script>
const statusEl = document.getElementById('status-msg');
function showStatus(msg, isError=false) {
  statusEl.textContent = msg;
  statusEl.className = 'status-msg' + (isError ? ' status-err' : '');
  setTimeout(() => { statusEl.textContent = ''; statusEl.className = 'status-msg'; }, 4000);
}

async function loadSources() {
  const resp = await fetch('/api/insights-engine/sources');
  if (!resp.ok) { showStatus('Failed to load sources', true); return; }
  const sources = await resp.json();
  const tbody = document.getElementById('sources-body');
  tbody.innerHTML = '';
  sources.forEach(src => {
    const badge = src.enabled
      ? '<span class="badge-active">Active</span>'
      : '<span class="badge-disabled">Disabled</span>';
    const btn = src.enabled
      ? `<button class="btn btn-sm btn-danger" onclick="toggleSource('${escHtml(src.name)}', false)">Disable</button>`
      : `<button class="btn btn-sm" onclick="toggleSource('${escHtml(src.name)}', true)">Enable</button>`;
    const tr = document.createElement('tr');
    tr.innerHTML = `<td><code>${escHtml(src.name)}</code></td><td>${badge}</td><td>${btn}</td>`;
    tbody.appendChild(tr);
  });
}

async function toggleSource(name, enable) {
  const action = enable ? 'enable' : 'disable';
  const resp = await fetch(`/api/insights-engine/sources/${name}/${action}`, {method:'POST'});
  const data = await resp.json();
  if (resp.ok) { showStatus(data.message || 'Updated'); loadSources(); }
  else { showStatus(data.error || 'Update failed', true); }
}

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

loadSources();
</script>
{% endblock %}
```

- [ ] **Step 2: Smoke-test**

Navigate to `/settings/insights-engine/sources`. Verify all registered sources appear. Toggle a source and confirm the badge and button change correctly.

- [ ] **Step 3: Commit**

```bash
git add templates/ie_sources.html
git commit -m "feat: add Data Source Manager template with enable/disable toggle"
```

---

## Task 12 — End-to-end verification and full test suite

- [ ] **Step 1: Run the full test suite**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -40
```

All new tests (yaml_io, rule_reload, attribution_reload, anomaly_reset, api_insights) must pass. No existing tests must regress.

- [ ] **Step 2: Deploy to Pi and smoke-test all four pages**

```bash
git pull && sudo systemctl restart mlss-monitor
```

Visit in order:
- `/settings/insights-engine/rules` — confirm rule table loads, edit one rule's severity, click Save, verify the page reloads with the updated value.
- `/settings/insights-engine/fingerprints` — confirm fingerprint table loads, click "Preview score" on `biological_offgas`, confirm a JSON score result appears inline.
- `/settings/insights-engine/anomaly` — confirm channel table loads, adjust threshold slider, click Save, confirm the new value persists after a page refresh.
- `/settings/insights-engine/sources` — confirm all five sources appear, disable one, confirm the badge changes, re-enable it.

- [ ] **Step 3: Verify hot-reload does not require restart**

Edit one rule expression directly in the rule manager UI, click Save. Without restarting the service, confirm that the next detection cycle (within 60 seconds) uses the updated rule by checking the shadow log at `/insights-engine`.

- [ ] **Step 4: Final commit and push**

```bash
git push origin claude/zealous-hugle
git push origin claude/zealous-hugle:feature/phase3-detection-layer
```

---

## Known limitations and follow-up work

| Item | Status | Notes |
|------|--------|-------|
| Add-new-rule form | Deferred to Phase 6 | PATCH covers edits; a full "add rule" form with expression builder is a larger UI effort |
| Add-new-fingerprint form | Deferred to Phase 6 | Same rationale; editing existing fingerprints covers the most common admin task |
| `DataSource.last_reading_at` | Deferred | DataSource ABC has no timestamp field yet; sources page shows status only |
| Multivar anomaly model reset | Not in scope | `MultivarAnomalyDetector` is not yet wired into the anomaly settings page; add in a follow-up task once the multivar API is stable |
| Enabled-flag persistence across restart | Deferred | Currently in-memory only; persist to a small `config/source_state.yaml` in a follow-up if needed |
| Expression validation in rule editor | Deferred | The API currently saves and reloads any expression; invalid expressions are caught by `RuleEngine.load()` and logged but don't block the save |
