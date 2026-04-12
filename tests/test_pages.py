"""Tests for page routes — including AstroUXDS Phase 1 landmark assertions."""


# ── Existing route tests (must keep passing) ─────────────────────────────────

def test_insights_engine_page_at_new_route(app_client):
    client, _ = app_client
    resp = client.get("/settings/insights-engine")
    assert resp.status_code == 200


def test_insights_engine_old_route_returns_404(app_client):
    client, _ = app_client
    resp = client.get("/insights-engine")
    assert resp.status_code == 404


# ── Phase 1: AstroUXDS landmark checks ───────────────────────────────────────

def test_dashboard_astro_landmarks(app_client):
    """Dashboard returns 200 and contains AstroUXDS structural landmarks."""
    client, _ = app_client
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert 'class="global-status-bar"' in body, "Missing .global-status-bar"
    assert 'class="tab-nav"' in body, "Missing .tab-nav"
    assert 'data-tab="dashboard"' in body, "Missing data-tab=dashboard"


def test_history_astro_landmarks(app_client):
    """History page returns 200 and contains AstroUXDS structural landmarks."""
    client, _ = app_client
    resp = client.get("/history")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert 'class="global-status-bar"' in body, "Missing .global-status-bar"
    assert 'class="tab-nav"' in body, "Missing .tab-nav"
    assert 'data-tab="history"' in body, "Missing data-tab=history"


def test_controls_astro_landmarks(app_client):
    """Controls page returns 200 and contains AstroUXDS structural landmarks."""
    client, _ = app_client
    resp = client.get("/controls")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert 'class="global-status-bar"' in body, "Missing .global-status-bar"
    assert 'class="tab-nav"' in body, "Missing .tab-nav"
    assert 'data-tab="controls"' in body, "Missing data-tab=controls"


def test_admin_astro_landmarks(app_client):
    """Admin/settings page returns 200 and contains AstroUXDS structural landmarks."""
    client, _ = app_client
    resp = client.get("/admin")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert 'class="global-status-bar"' in body, "Missing .global-status-bar"
    assert 'class="tab-nav"' in body, "Missing .tab-nav"
    assert 'data-tab="settings"' in body, "Missing data-tab=settings"


def test_insights_engine_astro_landmarks(app_client):
    """Insights engine page returns 200 and contains AstroUXDS structural landmarks."""
    client, _ = app_client
    resp = client.get("/settings/insights-engine")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert 'class="global-status-bar"' in body, "Missing .global-status-bar"
    assert 'class="tab-nav"' in body, "Missing .tab-nav"
    assert 'data-tab="settings"' in body, "Missing data-tab=settings"
