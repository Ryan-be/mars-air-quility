"""Substring-render assertions for the per-unit detail page.

Bucket B2 of the pre-Phase-4 audit gap-closure: the e2e suite never
rendered /grow/units/<id> at all, so JS-side bugs (Plotly script tag
absence, empty-state rendering, "current value visible" UX gaps) and
template regressions slipped through to deployment.

This file uses the Flask test client to hit the page route and
substring-asserts on the rendered HTML. It DOES NOT execute JS — that
would require a browser harness — but it does pin three things at the
template + Jinja level:

  * Plotly's `<script src="cdn.plot.ly/plotly...">` tag is present
    (Bug 2 from the deployment-time discoveries).
  * The page references the unit_id passed in the URL (so a future
    refactor that drops the data-unit-id wiring fails here).
  * The page boots the unit_detail.mjs module via `<script type="module">`.

For DOM/JS-rendering assertions (empty-state copy, current-value
visibility) a jsdom or Playwright harness is needed — those are the
audit's recommendation 4 + 5 and live as backlog items.
"""
import sqlite3
import tempfile
from datetime import datetime

import pytest


@pytest.fixture
def app(monkeypatch):
    """Boot the real mlss_monitor.app with auth bypassed for this test."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # pylint: disable=R1732
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    for mod_path in [
        "mlss_monitor.grow.auth", "mlss_monitor.grow.handlers",
        "mlss_monitor.grow.photo_storage",
        "mlss_monitor.routes.api_grow_units",
        "mlss_monitor.routes.api_grow_photos",
        "mlss_monitor.routes.api_grow_history",
        "mlss_monitor.routes.api_grow_config",
    ]:
        try:
            monkeypatch.setattr(f"{mod_path}.DB_FILE", tmp.name)
        except AttributeError:
            pass
    init_db.create_db()

    # Seed a single unit with a known label so we can assert it shows up.
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, plant_type, "
        "medium_type, current_phase, enrolled_at, bearer_token_hash, "
        "phase_set_at, is_active) "
        "VALUES (1, 'hw-1', 'TestPlant', 'tomato', 'soil', 'vegetative', "
        "?, 'h', ?, 1)",
        (datetime.utcnow(), datetime.utcnow()),
    )
    conn.commit()
    conn.close()

    # Bypass GitHub OAuth + admin gating so the page renders.
    from mlss_monitor.app import app as flask_app
    from mlss_monitor import state as app_state
    monkeypatch.setattr(app_state, "github_oauth", object())
    flask_app.config["TESTING"] = True

    test_client = flask_app.test_client()
    with test_client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user"] = "test"
        sess["user_role"] = "admin"

    yield test_client


def test_unit_detail_page_loads_plotly_script(app):
    """Bug 2 regression guard: grow_unit_detail.html must include the
    Plotly CDN script tag. Without it, sensor-event-chart.mjs falls
    through its `typeof Plotly === "undefined"` guard and renders
    the literal text "Plotly not loaded" in place of the chart."""
    r = app.get("/grow/1")
    assert r.status_code == 200, r.data
    html = r.data.decode()
    assert "cdn.plot.ly/plotly" in html, (
        "Plotly CDN script tag missing from grow_unit_detail.html — "
        "see commit 94b08aa for the original fix and "
        "docs/superpowers/audits/2026-05-08-grow-e2e-gap-analysis.md Bug 2"
    )


def test_unit_detail_page_carries_unit_id(app):
    """The page template injects unit_id into a data-attribute on
    the page root. unit_detail.mjs reads
    `document.querySelector('[data-unit-id]').dataset.unitId` to
    bootstrap; if Jinja drops the variable, the JS bootstraps for
    the wrong unit (or NaN)."""
    r = app.get("/grow/42")
    html = r.data.decode()
    assert 'data-unit-id="42"' in html


def test_unit_detail_page_boots_js_module(app):
    """The bootstrap script tag is what runs the JS. A future refactor
    that drops the <script type="module"> tag would silently leave
    the page as a static skeleton."""
    r = app.get("/grow/1")
    html = r.data.decode()
    assert 'type="module"' in html
    assert "unit_detail.mjs" in html
