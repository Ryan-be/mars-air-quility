# Event Tagging Enhancements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Strengthen the event-tagging learning loop with classifier persistence, enforced controlled vocabulary, full feature-vector display in event cards, and a classifier health panel in the Admin UI.

**Architecture:** The `AttributionEngine` gains pickle-based persistence and a `valid_tags` property; `add_inference_tag()` in `db_logger.py` is updated in-place to validate against that vocabulary; a new `/api/tags` blueprint serves the vocabulary to the frontend; the inference detail dialog renders the full `FeatureVector` from `evidence`; and a new Classifier Model card in `admin.html` mirrors the Anomaly Models card.

**Tech Stack:** Python 3.11, Flask, River (`preprocessing.StandardScaler | linear_model.LogisticRegression`), SQLite, pytest, vanilla JS (no bundler).

**Spec:** `docs/superpowers/specs/2026-04-05-event-tagging-enhancements-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `mlss_monitor/attribution/engine.py` | Modify | Add pickle save/load, `valid_tags`, `classifier_stats()` |
| `database/db_logger.py` | Modify | Add `allowed_tags` validation to existing `add_inference_tag()` |
| `mlss_monitor/routes/api_tags.py` | **Create** | `GET /api/tags` — serves vocabulary from loaded fingerprints |
| `mlss_monitor/routes/api_inferences.py` | Modify | Pass `valid_tags` to `add_inference_tag()`; 400 on invalid tag |
| `mlss_monitor/routes/api_history.py` | Modify | Pass `valid_tags` to `add_inference_tag()`; 400 on invalid tag |
| `mlss_monitor/routes/api_insights.py` | Modify | Add `GET /api/classifier/stats` endpoint |
| `mlss_monitor/routes/__init__.py` | Modify | Register `api_tags_bp` |
| `templates/history.html` | Modify | Fix dropdowns, remove custom text input, dynamic load, FV display |
| `templates/admin.html` | Modify | Add Classifier Model card below Anomaly Models |
| `static/css/admin.css` | Modify | Add `classifier-table` column width rules |
| `tests/test_attribution_persistence.py` | **Create** | Pickle save/load, fallback, corrupt-file handling |
| `tests/test_api_tags.py` | **Create** | `/api/tags` endpoint |
| `tests/test_event_tags.py` | Modify | Add normalization / rejection tests |
| `tests/test_api_insights.py` | Modify | Add `/api/classifier/stats` test |

---

## Task 1: Classifier Persistence

**Files:**
- Modify: `mlss_monitor/attribution/engine.py`
- Create: `tests/test_attribution_persistence.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_attribution_persistence.py`:

```python
"""Tests for AttributionEngine classifier persistence."""
import pickle
import pytest
from unittest.mock import patch, MagicMock


def _make_engine(config_path, monkeypatch):
    """Build an AttributionEngine with DB training stubbed out."""
    monkeypatch.setattr(
        "mlss_monitor.attribution.engine.AttributionEngine.train_on_tags",
        lambda self: None,
    )
    from mlss_monitor.attribution.engine import AttributionEngine
    return AttributionEngine(config_path)


def test_train_on_tags_saves_pickle(tmp_path, monkeypatch):
    """train_on_tags() should write classifier.pkl to data/ dir."""
    config_path = tmp_path / "config" / "fingerprints.yaml"
    config_path.parent.mkdir()
    config_path.write_text("sources: []\n")
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    engine = _make_engine(str(config_path), monkeypatch)

    # Manually call the real save path
    pkl_path = tmp_path / "data" / "classifier.pkl"
    engine._pkl_path = pkl_path

    # Re-enable real train_on_tags but stub DB call
    with patch("mlss_monitor.attribution.engine.get_inferences", return_value=[]):
        engine.train_on_tags()

    assert pkl_path.exists(), "classifier.pkl should be written after training"


def test_init_loads_existing_pickle(tmp_path, monkeypatch):
    """__init__ should load classifier from pickle and skip DB retraining."""
    config_path = tmp_path / "config" / "fingerprints.yaml"
    config_path.parent.mkdir()
    config_path.write_text("sources: []\n")
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    from river import linear_model, preprocessing
    model = preprocessing.StandardScaler() | linear_model.LogisticRegression()
    pkl_path = data_dir / "classifier.pkl"
    with open(pkl_path, "wb") as f:
        pickle.dump(model, f)

    train_called = []

    def fake_train(self):
        train_called.append(True)

    monkeypatch.setattr(
        "mlss_monitor.attribution.engine.AttributionEngine.train_on_tags",
        fake_train,
    )

    # Patch _pkl_path property to return our tmp path
    with patch("mlss_monitor.attribution.engine.AttributionEngine._pkl_path",
               new_callable=lambda: property(lambda self: pkl_path)):
        from mlss_monitor.attribution.engine import AttributionEngine
        engine = AttributionEngine(str(config_path))

    assert not train_called, "train_on_tags should NOT be called when pickle exists"


def test_corrupt_pickle_falls_back_to_training(tmp_path, monkeypatch):
    """Corrupt classifier.pkl should be deleted and DB retraining used."""
    config_path = tmp_path / "config" / "fingerprints.yaml"
    config_path.parent.mkdir()
    config_path.write_text("sources: []\n")
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    pkl_path = data_dir / "classifier.pkl"
    pkl_path.write_bytes(b"not a valid pickle")

    train_called = []

    def fake_train(self):
        train_called.append(True)

    monkeypatch.setattr(
        "mlss_monitor.attribution.engine.AttributionEngine.train_on_tags",
        fake_train,
    )

    with patch("mlss_monitor.attribution.engine.AttributionEngine._pkl_path",
               new_callable=lambda: property(lambda self: pkl_path)):
        from mlss_monitor.attribution.engine import AttributionEngine
        engine = AttributionEngine(str(config_path))

    assert train_called, "train_on_tags should be called as fallback after corrupt pickle"
    assert not pkl_path.exists(), "corrupt pickle should be deleted"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /path/to/project
python -m pytest tests/test_attribution_persistence.py -v
```

Expected: 3 failures (AttributionEngine has no `_pkl_path`, no save/load logic yet).

- [ ] **Step 3: Implement pickle persistence in engine.py**

In `mlss_monitor/attribution/engine.py`, add the `_pkl_path` property and update `__init__` and `train_on_tags`:

```python
"""AttributionEngine: scores fingerprints against a FeatureVector, returns top match."""
from __future__ import annotations

import dataclasses
import logging
import pickle
from pathlib import Path

from river import linear_model, preprocessing  # pylint: disable=import-error

from mlss_monitor.attribution.loader import Fingerprint, load_fingerprints
from mlss_monitor.attribution.scorer import combine, sensor_score, temporal_score
from mlss_monitor.feature_vector import FeatureVector

log = logging.getLogger(__name__)

_RUNNER_UP_DELTA = 0.15
_READY_THRESHOLD = 5  # minimum tagged samples for a label to be "ready"
```

Replace `__init__` with:

```python
    def __init__(self, config_path) -> None:
        self._config_path = Path(config_path)
        self._fingerprints: list[Fingerprint] = load_fingerprints(self._config_path)
        self._ml_model = preprocessing.StandardScaler() | linear_model.LogisticRegression()
        log.info(
            "AttributionEngine: loaded %d fingerprints from %s",
            len(self._fingerprints),
            self._config_path.name,
        )
        # Try loading persisted classifier; fall back to DB retraining.
        pkl = self._pkl_path
        if pkl.exists():
            try:
                with open(pkl, "rb") as fh:
                    self._ml_model = pickle.load(fh)
                log.info("AttributionEngine: loaded classifier from disk (%s)", pkl.name)
                return
            except Exception as exc:
                log.warning(
                    "AttributionEngine: corrupt pickle %s (%s) — retraining from DB",
                    pkl, exc,
                )
                try:
                    pkl.unlink()
                except OSError:
                    pass
        self.train_on_tags()

    @property
    def _pkl_path(self) -> Path:
        """Path to the persisted classifier: <project_root>/data/classifier.pkl."""
        data_dir = self._config_path.parent.parent / "data"
        data_dir.mkdir(exist_ok=True)
        return data_dir / "classifier.pkl"
```

At the end of `train_on_tags()`, add the save block (inside the existing method, after the `if trained:` log line):

```python
        # Persist model to disk so restarts don't lose learned state.
        try:
            with open(self._pkl_path, "wb") as fh:
                pickle.dump(self._ml_model, fh)
            log.info("AttributionEngine: classifier saved to disk")
        except Exception as exc:
            log.warning("AttributionEngine: could not save classifier: %s", exc)
```

- [ ] **Step 4: Run tests — expect pass**

```bash
python -m pytest tests/test_attribution_persistence.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add mlss_monitor/attribution/engine.py tests/test_attribution_persistence.py
git commit -m "feat: persist River classifier to disk with pickle fallback"
```

---

## Task 2: `valid_tags` property + `/api/tags` endpoint

**Files:**
- Modify: `mlss_monitor/attribution/engine.py`
- Create: `mlss_monitor/routes/api_tags.py`
- Modify: `mlss_monitor/routes/__init__.py`
- Create: `tests/test_api_tags.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_api_tags.py`:

```python
"""Tests for GET /api/tags endpoint."""


def test_get_tags_returns_vocabulary(app_client):
    """GET /api/tags returns list of fingerprint id+label pairs."""
    client, _ = app_client
    resp = client.get("/api/tags")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "tags" in data
    tags = data["tags"]
    assert isinstance(tags, list)
    assert len(tags) > 0
    # Each entry must have id and label
    for t in tags:
        assert "id" in t
        assert "label" in t
    # Spot-check known fingerprint IDs
    ids = {t["id"] for t in tags}
    assert "cooking" in ids
    assert "combustion" in ids


def test_get_tags_ids_use_underscores(app_client):
    """Tag IDs must use underscores not hyphens (canonical form)."""
    client, _ = app_client
    resp = client.get("/api/tags")
    data = resp.get_json()
    for t in data["tags"]:
        assert "-" not in t["id"], f"Tag ID {t['id']!r} must not contain hyphens"
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_api_tags.py -v
```

Expected: FAIL — `/api/tags` returns 404.

- [ ] **Step 3: Add `valid_tags` property to `AttributionEngine`**

In `mlss_monitor/attribution/engine.py`, add after the `_pkl_path` property:

```python
    @property
    def valid_tags(self) -> frozenset[str]:
        """Frozenset of canonical tag IDs derived from loaded fingerprints."""
        return frozenset(fp.id for fp in self._fingerprints)

    def tags_with_labels(self) -> list[dict]:
        """Return [{"id": ..., "label": ...}, ...] for all loaded fingerprints."""
        return [{"id": fp.id, "label": fp.label} for fp in self._fingerprints]
```

- [ ] **Step 4: Create `mlss_monitor/routes/api_tags.py`**

```python
"""GET /api/tags — returns the controlled vocabulary of valid event tags."""

from flask import Blueprint, jsonify

from mlss_monitor import state

api_tags_bp = Blueprint("api_tags", __name__)


@api_tags_bp.route("/api/tags")
def list_tags():
    """Return all valid tag IDs and labels derived from loaded fingerprints."""
    engine = state.detection_engine
    if engine and engine._attribution_engine:
        tags = engine._attribution_engine.tags_with_labels()
    else:
        tags = []
    return jsonify({"tags": tags})
```

- [ ] **Step 5: Register the blueprint**

In `mlss_monitor/routes/__init__.py`:

```python
"""Register all route blueprints on the Flask app."""

from .auth import auth_bp
from .pages import pages_bp
from .api_data import api_data_bp
from .api_fan import api_fan_bp
from .api_weather import api_weather_bp
from .api_settings import api_settings_bp
from .api_users import api_users_bp
from .system import system_bp
from .api_inferences import api_inferences_bp
from .api_stream import api_stream_bp
from .api_insights import api_insights_bp
from .api_history import api_history_bp
from .api_tags import api_tags_bp


def register_routes(app):
    app.register_blueprint(auth_bp)
    app.register_blueprint(pages_bp)
    app.register_blueprint(api_data_bp)
    app.register_blueprint(api_fan_bp)
    app.register_blueprint(api_weather_bp)
    app.register_blueprint(api_settings_bp)
    app.register_blueprint(api_users_bp)
    app.register_blueprint(system_bp)
    app.register_blueprint(api_inferences_bp)
    app.register_blueprint(api_stream_bp)
    app.register_blueprint(api_insights_bp)
    app.register_blueprint(api_history_bp)
    app.register_blueprint(api_tags_bp)
```

- [ ] **Step 6: Run tests — expect pass**

```bash
python -m pytest tests/test_api_tags.py -v
```

Expected: 2 passed.

- [ ] **Step 7: Commit**

```bash
git add mlss_monitor/attribution/engine.py \
        mlss_monitor/routes/api_tags.py \
        mlss_monitor/routes/__init__.py \
        tests/test_api_tags.py
git commit -m "feat: add valid_tags property and GET /api/tags endpoint"
```

---

## Task 3: Tag Validation — Backend

**Files:**
- Modify: `database/db_logger.py`
- Modify: `mlss_monitor/routes/api_inferences.py`
- Modify: `mlss_monitor/routes/api_history.py`
- Modify: `tests/test_event_tags.py`

- [ ] **Step 1: Add tests for validation behaviour**

Append to `tests/test_event_tags.py`:

```python
def test_add_inference_tag_rejects_unknown_tag(db):
    """add_inference_tag raises ValueError for a tag not in allowed_tags."""
    from database.db_logger import add_inference_tag, save_inference
    import pytest

    inf_id = save_inference(
        event_type="tvoc_spike", severity="warning",
        title="T", description="D", action="A", evidence={}, confidence=0.5,
    )
    with pytest.raises(ValueError, match="Unknown tag"):
        add_inference_tag(inf_id, "not_a_real_tag", allowed_tags=frozenset(["cooking"]))


def test_add_inference_tag_accepts_valid_tag(db):
    """add_inference_tag succeeds when tag is in allowed_tags."""
    from database.db_logger import add_inference_tag, get_inference_tags, save_inference

    inf_id = save_inference(
        event_type="tvoc_spike", severity="warning",
        title="T", description="D", action="A", evidence={}, confidence=0.5,
    )
    add_inference_tag(inf_id, "cooking", allowed_tags=frozenset(["cooking"]))
    tags = get_inference_tags(inf_id)
    assert any(t["tag"] == "cooking" for t in tags)


def test_add_inference_tag_no_allowed_tags_passes_through(db):
    """add_inference_tag with no allowed_tags skips validation (backwards compat)."""
    from database.db_logger import add_inference_tag, get_inference_tags, save_inference

    inf_id = save_inference(
        event_type="tvoc_spike", severity="warning",
        title="T", description="D", action="A", evidence={}, confidence=0.5,
    )
    # No allowed_tags — should not raise
    add_inference_tag(inf_id, "anything_goes")
    tags = get_inference_tags(inf_id)
    assert any(t["tag"] == "anything_goes" for t in tags)


def test_api_post_tag_rejects_invalid(app_client, db):
    """POST /api/inferences/<id>/tags with unknown tag returns 400."""
    from database.db_logger import save_inference
    client, _ = app_client
    inf_id = save_inference(
        event_type="tvoc_spike", severity="warning",
        title="T", description="D", action="A", evidence={}, confidence=0.5,
    )
    resp = client.post(
        f"/api/inferences/{inf_id}/tags",
        json={"tag": "totally_made_up", "confidence": 1.0},
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["error"] == "invalid_tag"
    assert "valid_tags" in data


def test_api_post_tag_accepts_valid(app_client, db):
    """POST /api/inferences/<id>/tags with a known fingerprint ID returns 200."""
    from database.db_logger import save_inference
    client, _ = app_client
    inf_id = save_inference(
        event_type="tvoc_spike", severity="warning",
        title="T", description="D", action="A", evidence={}, confidence=0.5,
    )
    resp = client.post(
        f"/api/inferences/{inf_id}/tags",
        json={"tag": "cooking", "confidence": 1.0},
    )
    assert resp.status_code == 200
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_event_tags.py -v
```

Expected: the 3 new `add_inference_tag` tests fail (no `allowed_tags` param yet), API tests fail (no validation yet).

- [ ] **Step 3: Update `add_inference_tag` in `database/db_logger.py`**

Find the existing function and replace its signature + add validation:

```python
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
```

- [ ] **Step 4: Update `api_inferences.py` POST tags handler**

Replace the `POST` branch of the `tags()` view:

```python
    elif request.method == "POST":
        data = request.get_json(force=True)
        tag = data.get("tag", "").strip()
        confidence = data.get("confidence", 1.0)
        if not tag:
            return jsonify({"ok": False, "error": "tag is required"}), 400

        # Validate against controlled vocabulary when engine is available.
        from mlss_monitor import state  # pylint: disable=import-outside-toplevel
        engine = state.detection_engine
        allowed = (
            engine._attribution_engine.valid_tags
            if engine and engine._attribution_engine
            else None
        )
        if allowed is not None and tag not in allowed:
            return jsonify({
                "error": "invalid_tag",
                "valid_tags": sorted(allowed),
            }), 400

        add_inference_tag(inference_id, tag, confidence, allowed_tags=allowed)
        return jsonify({"ok": True})
```

- [ ] **Step 5: Update `api_history.py` range-tag handler**

In `tag_range()`, replace the `if tag:` block:

```python
    if tag:
        from mlss_monitor import state as _state  # pylint: disable=import-outside-toplevel
        _engine = _state.detection_engine
        _allowed = (
            _engine._attribution_engine.valid_tags
            if _engine and _engine._attribution_engine
            else None
        )
        if _allowed is not None and tag not in _allowed:
            return jsonify({
                "error": "invalid_tag",
                "valid_tags": sorted(_allowed),
            }), 400
        add_inference_tag(inference_id, tag, 1.0, allowed_tags=_allowed)
```

- [ ] **Step 6: Run all tag tests**

```bash
python -m pytest tests/test_event_tags.py -v
```

Expected: all pass.

- [ ] **Step 7: Run full test suite to check for regressions**

```bash
python -m pytest --tb=short -q
```

Expected: no new failures.

- [ ] **Step 8: Commit**

```bash
git add database/db_logger.py \
        mlss_monitor/routes/api_inferences.py \
        mlss_monitor/routes/api_history.py \
        tests/test_event_tags.py
git commit -m "feat: enforce controlled vocabulary in add_inference_tag and tag API endpoints"
```

---

## Task 4: Frontend — Fix Dropdowns, Remove Custom Input, Dynamic Load

**Files:**
- Modify: `templates/history.html`

- [ ] **Step 1: Remove hardcoded options and custom text input from both dropdowns**

In `templates/history.html`, replace the **first dropdown** (range-tag, around line 293):

```html
          <select id="corrRangeTagSelect">
            <option value="">Select a tag...</option>
          </select>
```

In `templates/history.html`, replace the **second dropdown + custom input** (inference dialog, around line 449):

```html
          <select id="infTagSelect" class="inf-tag-select">
            <option value="">Select a tag...</option>
          </select>
          <button class="btn inf-add-tag" id="infAddTag">Add Tag</button>
```

Remove the entire line:
```html
          <input type="text" id="infTagCustom" class="inf-tag-custom" placeholder="Or enter custom tag...">
```

- [ ] **Step 2: Add dynamic tag loading JS**

In the `{% block scripts %}` section (or in the existing `history.js` module if there is a `DOMContentLoaded` block in the template), add a `<script type="module">` block **before** the existing `history.js` import, or add inside the existing module script. The safest is to add a small inline script just before `{% endblock %}`:

Find the `{% block scripts %}` block at the bottom of `history.html` and add before the closing `{% endblock %}`:

```html
  <script type="module">
    // Populate both tag dropdowns from /api/tags on load.
    async function populateTagDropdowns() {
      let tags = [];
      try {
        const resp = await fetch('/api/tags');
        if (resp.ok) {
          const data = await resp.json();
          tags = data.tags || [];
        }
      } catch (_) { /* silently ignore — dropdowns remain empty */ }

      const EMOJI = {
        cooking: '🍳',
        external_pollution: '🌫️',
        vehicle_exhaust: '🚗',
        biological_offgas: '🧬',
        chemical_offgassing: '🧪',
        combustion: '🔥',
        cleaning_products: '🧹',
        human_activity: '👤',
        mould_voc: '🍄',
        personal_care: '🧴',
      };

      const selectors = ['#corrRangeTagSelect', '#infTagSelect'];
      selectors.forEach(sel => {
        const el = document.querySelector(sel);
        if (!el) return;
        tags.forEach(({ id, label }) => {
          const opt = document.createElement('option');
          opt.value = id;
          opt.textContent = `${EMOJI[id] || ''} ${label}`.trim();
          el.appendChild(opt);
        });
      });
    }

    document.addEventListener('DOMContentLoaded', populateTagDropdowns);
  </script>
```

- [ ] **Step 3: Remove JS references to `infTagCustom`**

Search `templates/history.html` and `static/js/history.js` for any references to `infTagCustom` and remove them:

```bash
grep -n "infTagCustom" templates/history.html static/js/history.js
```

For each match, remove the line or block that reads from `infTagCustom`. Typically this is in the "Add Tag" button click handler — replace:

```js
// BEFORE (example)
const tag = document.getElementById('infTagSelect').value
           || document.getElementById('infTagCustom').value.trim();
```

with:

```js
// AFTER
const tag = document.getElementById('infTagSelect').value;
```

- [ ] **Step 4: Smoke-test manually (or via existing pages test)**

```bash
python -m pytest tests/test_pages.py -v
```

Expected: all pass (pages load without server error).

- [ ] **Step 5: Commit**

```bash
git add templates/history.html static/js/history.js
git commit -m "feat: load tag dropdowns dynamically from /api/tags, remove free-text bypass"
```

---

## Task 5: Feature Vector Display in Event Card

**Files:**
- Modify: `templates/history.html`

The inference detail dialog (`<dialog>`) already shows notes and tags. When an inference has a `feature_vector` in its `evidence` JSON, we add a collapsible **Sensor Snapshot** section below the Tags section showing all non-null sensor values and the apparent pattern flags.

- [ ] **Step 1: Add the Sensor Snapshot HTML section**

In `templates/history.html`, inside the `<dialog>` element, add a new section **after** the Tags `inf-section` div:

```html
      <div class="inf-section" id="infFvSection" style="display:none;">
        <div class="inf-section-title">
          Sensor Snapshot
          <span class="info-icon" title="All sensor values and detected patterns at the time of this event, from the feature vector stored in evidence.">ⓘ</span>
        </div>
        <div id="infFvBody" class="inf-fv-body"></div>
      </div>
```

- [ ] **Step 2: Add CSS for the feature vector display**

In `templates/history.html` `<style>` block (or `static/css/dashboard.css` if a dedicated block exists for dialog styles), add:

```css
.inf-fv-body {
  font-size: 0.82rem;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 2px 12px;
}
.inf-fv-row {
  display: contents;
}
.inf-fv-key {
  color: var(--text-muted, #6b7280);
  padding: 1px 0;
}
.inf-fv-val {
  font-variant-numeric: tabular-nums;
  padding: 1px 0;
}
.inf-fv-group {
  grid-column: 1 / -1;
  font-weight: 600;
  margin-top: 6px;
  color: var(--text-secondary, #374151);
  border-bottom: 1px solid var(--border, #e5e7eb);
  padding-bottom: 2px;
}
.inf-fv-flag-true  { color: #22c55e; }
.inf-fv-flag-false { color: var(--text-muted, #6b7280); }
```

- [ ] **Step 3: Add JS to render the feature vector**

In the `history.js` module (or inline in the `<script type="module">` at the bottom of `history.html`), add the `renderFv` function and call it when the dialog opens with an inference that has `evidence.feature_vector`:

```js
// Groups for display — each entry is [groupLabel, fieldPrefix]
const FV_GROUPS = [
  ['TVOC',        'tvoc_'],
  ['eCO₂',        'eco2_'],
  ['Temperature', 'temperature_'],
  ['Humidity',    'humidity_'],
  ['PM1',         'pm1_'],
  ['PM2.5',       'pm25_'],
  ['PM10',        'pm10_'],
  ['CO',          'co_'],
  ['NO₂',         'no2_'],
  ['NH₃',         'nh3_'],
];

const FV_CROSS = ['nh3_lag_behind_tvoc_seconds', 'pm25_correlated_with_tvoc', 'co_correlated_with_tvoc', 'vpd_kpa'];

function _fvLabel(key) {
  // Convert snake_case suffix to readable label
  return key
    .replace(/_/g, ' ')
    .replace(/\b\w/g, c => c.toUpperCase())
    .replace('Ppb', 'ppb')
    .replace('Ppm', 'ppm')
    .replace('Kpa', 'kPa');
}

function renderFv(fv) {
  const body = document.getElementById('infFvBody');
  const section = document.getElementById('infFvSection');
  if (!fv || !body) return;

  const rows = [];

  FV_GROUPS.forEach(([groupLabel, prefix]) => {
    const fields = Object.entries(fv).filter(
      ([k, v]) => k.startsWith(prefix) && v !== null && v !== undefined && k !== 'timestamp'
    );
    if (!fields.length) return;

    rows.push(`<div class="inf-fv-group">${groupLabel}</div>`);
    fields.forEach(([k, v]) => {
      const label = _fvLabel(k.slice(prefix.length));
      let display;
      if (typeof v === 'boolean') {
        display = `<span class="inf-fv-flag-${v}">${v ? '✓ yes' : '✗ no'}</span>`;
      } else if (typeof v === 'number') {
        display = Number.isInteger(v) ? String(v) : v.toFixed(2);
      } else {
        display = String(v);
      }
      rows.push(`<span class="inf-fv-key">${label}</span><span class="inf-fv-val">${display}</span>`);
    });
  });

  // Cross-sensor features
  const crossFields = FV_CROSS.filter(k => fv[k] !== null && fv[k] !== undefined);
  if (crossFields.length) {
    rows.push('<div class="inf-fv-group">Cross-sensor</div>');
    crossFields.forEach(k => {
      const v = fv[k];
      let display;
      if (typeof v === 'boolean') {
        display = `<span class="inf-fv-flag-${v}">${v ? '✓ yes' : '✗ no'}</span>`;
      } else if (typeof v === 'number') {
        display = v.toFixed(2);
      } else {
        display = String(v);
      }
      rows.push(`<span class="inf-fv-key">${_fvLabel(k)}</span><span class="inf-fv-val">${display}</span>`);
    });
  }

  if (rows.length) {
    body.innerHTML = rows.join('');
    section.style.display = '';
  } else {
    section.style.display = 'none';
  }
}
```

In the existing code that opens the inference dialog (search for where `infNotes` / `infTagsList` are populated — it will be a function like `openInfDialog` or `showInference`), add a call to `renderFv`:

```js
// After populating notes/tags, render feature vector if present
const evidence = inf.evidence || {};
const fv = (typeof evidence === 'string') ? JSON.parse(evidence).feature_vector : evidence.feature_vector;
renderFv(fv || null);
```

Also clear it when dialog closes (find the dialog close handler):

```js
document.getElementById('infFvBody').innerHTML = '';
document.getElementById('infFvSection').style.display = 'none';
```

- [ ] **Step 4: Run pages test**

```bash
python -m pytest tests/test_pages.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add templates/history.html static/js/history.js static/css/dashboard.css
git commit -m "feat: show full feature vector sensor snapshot in inference event card"
```

---

## Task 6: Classifier Feedback Panel — Backend

**Files:**
- Modify: `mlss_monitor/attribution/engine.py`
- Modify: `mlss_monitor/routes/api_insights.py`
- Modify: `tests/test_api_insights.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_api_insights.py`:

```python
def test_classifier_stats_returns_all_tags(app_client, db):
    """GET /api/classifier/stats returns a row for every known fingerprint."""
    client, _ = app_client
    resp = client.get("/api/classifier/stats")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "total_samples" in data
    assert "tag_stats" in data
    assert isinstance(data["tag_stats"], list)
    # Every fingerprint label should be represented
    assert len(data["tag_stats"]) > 0
    for row in data["tag_stats"]:
        assert "tag" in row
        assert "label" in row
        assert "sample_count" in row
        assert "ready" in row


def test_classifier_stats_sample_counts_match_tags(app_client, db):
    """sample_count in classifier/stats reflects actual tagged events."""
    from database.db_logger import save_inference, add_inference_tag
    client, _ = app_client

    inf_id = save_inference(
        event_type="tvoc_spike", severity="warning",
        title="T", description="D", action="A",
        evidence={"feature_vector": {"tvoc_current": 450.0}},
        confidence=0.8,
    )
    # Tag without validation (no allowed_tags) so test is self-contained
    add_inference_tag(inf_id, "cooking")

    resp = client.get("/api/classifier/stats")
    data = resp.get_json()
    cooking_row = next((r for r in data["tag_stats"] if r["tag"] == "cooking"), None)
    assert cooking_row is not None
    assert cooking_row["sample_count"] >= 1
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_api_insights.py::test_classifier_stats_returns_all_tags \
                 tests/test_api_insights.py::test_classifier_stats_sample_counts_match_tags -v
```

Expected: FAIL — `/api/classifier/stats` returns 404.

- [ ] **Step 3: Add `classifier_stats()` to `AttributionEngine`**

In `mlss_monitor/attribution/engine.py`, add this method:

```python
    def classifier_stats(self) -> dict:
        """Return per-tag training statistics for the admin classifier panel.

        Returns:
            {
                "total_samples": int,
                "tag_stats": [
                    {"tag": str, "label": str, "sample_count": int,
                     "avg_confidence": float | None, "ready": bool},
                    ...
                ]
            }
        """
        from database.db_logger import get_inferences  # pylint: disable=import-outside-toplevel

        # Count tagged samples per tag from the DB.
        tag_counts: dict[str, int] = {}
        try:
            rows = get_inferences(limit=5000, include_dismissed=False)
            for inf in rows:
                for t in (inf.get("tags") or []):
                    tag_counts[t["tag"]] = tag_counts.get(t["tag"], 0) + 1
        except Exception as exc:
            log.warning("AttributionEngine.classifier_stats: DB error: %s", exc)

        # Compute avg predicted confidence per tag using stored feature vectors.
        tag_conf_sums: dict[str, float] = {}
        tag_conf_counts: dict[str, int] = {}
        try:
            rows_fv = get_inferences(limit=5000, include_dismissed=False)
            for inf in rows_fv:
                evidence = inf.get("evidence") or {}
                if isinstance(evidence, str):
                    import json as _json  # pylint: disable=import-outside-toplevel
                    try:
                        evidence = _json.loads(evidence)
                    except Exception:
                        continue
                fv_dict = evidence.get("feature_vector")
                if not fv_dict:
                    continue
                features = {k: v for k, v in fv_dict.items()
                            if k != "timestamp" and v is not None}
                if not features:
                    continue
                try:
                    proba = self._ml_model.predict_proba_one(features)
                    if proba:
                        for label, conf in proba.items():
                            tag_conf_sums[label] = tag_conf_sums.get(label, 0.0) + conf
                            tag_conf_counts[label] = tag_conf_counts.get(label, 0) + 1
                except Exception:
                    pass
        except Exception as exc:
            log.warning("AttributionEngine.classifier_stats: confidence calc error: %s", exc)

        tag_stats = []
        for fp in self._fingerprints:
            count = tag_counts.get(fp.id, 0)
            ready = count >= _READY_THRESHOLD
            avg_conf = None
            n_conf = tag_conf_counts.get(fp.id, 0)
            if ready and n_conf > 0:
                avg_conf = round(tag_conf_sums[fp.id] / n_conf, 3)
            tag_stats.append({
                "tag": fp.id,
                "label": fp.label,
                "sample_count": count,
                "avg_confidence": avg_conf,
                "ready": ready,
            })

        return {
            "total_samples": sum(tag_counts.values()),
            "tag_stats": tag_stats,
        }
```

- [ ] **Step 4: Add `GET /api/classifier/stats` to `api_insights.py`**

At the bottom of `mlss_monitor/routes/api_insights.py`, append:

```python
@api_insights_bp.route("/api/classifier/stats")
@require_role("admin")
def classifier_stats():
    """Return per-tag classifier training statistics."""
    engine = state.detection_engine
    if not engine or not engine._attribution_engine:
        return jsonify({"total_samples": 0, "tag_stats": []})
    return jsonify(engine._attribution_engine.classifier_stats())
```

- [ ] **Step 5: Run failing tests**

```bash
python -m pytest tests/test_api_insights.py::test_classifier_stats_returns_all_tags \
                 tests/test_api_insights.py::test_classifier_stats_sample_counts_match_tags -v
```

Expected: both pass.

- [ ] **Step 6: Run full suite**

```bash
python -m pytest --tb=short -q
```

Expected: no regressions.

- [ ] **Step 7: Commit**

```bash
git add mlss_monitor/attribution/engine.py \
        mlss_monitor/routes/api_insights.py \
        tests/test_api_insights.py
git commit -m "feat: add classifier_stats() and GET /api/classifier/stats endpoint"
```

---

## Task 7: Classifier Feedback Panel — Frontend

**Files:**
- Modify: `templates/admin.html`
- Modify: `static/css/admin.css`

- [ ] **Step 1: Add CSS for the classifier table**

In `static/css/admin.css`, after the existing `.anomaly-table` column rules, add:

```css
.classifier-table col.col-tag       { width: 28%; }
.classifier-table col.col-samples   { width: 12%; }
.classifier-table col.col-conf      { width: 30%; }
.classifier-table col.col-clstatus  { width: 30%; }
```

- [ ] **Step 2: Add the Classifier Model card in `admin.html`**

In `templates/admin.html`, find the closing `</div>` of the Anomaly Models card (the one wrapping the `📡 Anomaly Models` heading and `anomaly-table`). Immediately after that `</div>`, insert:

```html
        <div class="card" id="classifierModelCard">
          <h3>🧠 Classifier Model</h3>
          <table class="insights-table classifier-table" id="classifierTable">
            <colgroup>
              <col class="col-tag"><col class="col-samples">
              <col class="col-conf"><col class="col-clstatus">
            </colgroup>
            <thead>
              <tr>
                <th>Tag</th>
                <th>Samples</th>
                <th>Avg Confidence <span title="Mean predicted confidence across stored inferences for this label. Only shown once ≥5 samples are tagged." style="cursor:help;">ⓘ</span></th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody id="classifierTableBody">
              <tr><td colspan="4" style="color:var(--text-muted,#6b7280)">Loading…</td></tr>
            </tbody>
          </table>
        </div>
```

- [ ] **Step 3: Add the JS to load and render the classifier card**

In `templates/admin.html`, inside the `loadInsightsEngine()` function (or the block that populates `#ieContent`), add a call to `loadClassifierStats()` after the existing content is rendered. Add the function alongside the existing SSE setup block:

```js
    async function loadClassifierStats() {
      try {
        const res = await fetch('/api/classifier/stats');
        if (!res.ok) return;
        const data = await res.json();
        renderClassifierTable(data);
      } catch (_) {}
    }

    function renderClassifierTable(data) {
      const tbody = document.getElementById('classifierTableBody');
      if (!tbody) return;
      const stats = data.tag_stats || [];
      if (!stats.length) {
        tbody.innerHTML = '<tr><td colspan="4" style="color:var(--text-muted,#6b7280)">No fingerprints loaded.</td></tr>';
        return;
      }
      tbody.innerHTML = stats.map(function(row) {
        // Confidence bar
        let barHtml = '<span style="color:var(--text-muted,#6b7280)">—</span>';
        if (row.ready && row.avg_confidence !== null && row.avg_confidence !== undefined) {
          const conf = row.avg_confidence;
          const pct = Math.round(conf * 100);
          let barClass = 'score-bar--green';
          if (conf < 0.50) barClass = 'score-bar--red';
          else if (conf < 0.70) barClass = 'score-bar--amber';
          barHtml = `<div class="score-bar-wrap">
            <div class="score-bar ${barClass}" style="width:${pct}%"></div>
            <span class="score-label">${conf.toFixed(2)}</span>
          </div>`;
        }
        // Status badge
        let statusHtml;
        if (!row.ready) {
          const needed = Math.max(0, 5 - row.sample_count);
          statusHtml = `<span style="color:var(--text-muted,#6b7280)">⏳ Learning (need ${needed} more)</span>`;
        } else if (row.avg_confidence !== null && row.avg_confidence < 0.50) {
          statusHtml = '<span style="color:#ef4444;font-weight:600">⚠ Low confidence</span>';
        } else {
          statusHtml = '<span style="color:#22c55e;font-weight:600">● Ready</span>';
        }
        return `<tr>
          <td style="font-family:monospace">${row.tag}</td>
          <td>${row.sample_count}</td>
          <td>${barHtml}</td>
          <td class="status-cell">${statusHtml}</td>
        </tr>`;
      }).join('');
    }
```

Then call `loadClassifierStats()` at the end of the `loadInsightsEngine()` function (just before or after `_ieCached = true`):

```js
        loadClassifierStats();
```

Also wire a refresh after a tag is added. Find the `fetch('/api/inferences/${id}/tags', {method:'POST',...})` success handler in the admin page (if it exists) or in `history.js`, and add `loadClassifierStats()` after a successful tag submission.

- [ ] **Step 4: Smoke test**

```bash
python -m pytest tests/test_pages.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add templates/admin.html static/css/admin.css
git commit -m "feat: add Classifier Model health card to admin Insights Engine tab"
```

---

## Task 8: Final Lint Check + Full Test Run

- [ ] **Step 1: Run full test suite**

```bash
python -m pytest --tb=short -q
```

Expected: all tests pass, no new failures.

- [ ] **Step 2: Check for pylint issues in touched files**

```bash
python -m pylint \
  mlss_monitor/attribution/engine.py \
  mlss_monitor/routes/api_tags.py \
  mlss_monitor/routes/api_insights.py \
  mlss_monitor/routes/api_inferences.py \
  mlss_monitor/routes/api_history.py \
  database/db_logger.py \
  --disable=C,R \
  --score=no
```

Fix any W (warning) or E (error) level issues. Informational `C`/`R` codes are suppressed.

- [ ] **Step 3: Final commit if any lint fixes were needed**

```bash
git add -p   # stage only lint fixes
git commit -m "fix: lint cleanup across tagging enhancement files"
```

- [ ] **Step 4: Push branch**

```bash
git push origin feature/event-tagging-learning
```
