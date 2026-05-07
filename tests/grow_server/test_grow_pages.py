"""Pages: /grow renders the fleet template; nav has the Grow tab."""
import tempfile
import pytest


@pytest.fixture
def client(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    init_db.create_db()

    # Build the real app
    from mlss_monitor.app import app
    app.config["TESTING"] = True
    return app.test_client()


def test_grow_route_returns_200(client):
    r = client.get("/grow")
    assert r.status_code == 200


def test_dashboard_nav_includes_grow_tab(client):
    """Any rendered page that uses base.html should show the Grow tab in the nav."""
    r = client.get("/grow")
    assert b">Grow<" in r.data or b">GROW<" in r.data


def test_grow_page_loads_grow_static_assets(client):
    r = client.get("/grow")
    body = r.data.decode("utf-8")
    assert "/static/css/grow.css" in body
    assert "/static/js/grow/fleet.mjs" in body


# ---------------------------------------------------------------------------
# Phase 3 Task 6: storage warning banner
# ---------------------------------------------------------------------------
# The banner is driven by `storage_status` passed into the template from
# pages.grow_fleet. We patch get_storage_status at the import site
# (mlss_monitor.routes.pages, where the page route imports it) rather
# than at the source module — patching the source wouldn't affect the
# already-bound name in routes.pages.

def test_grow_fleet_page_shows_storage_warning_when_set(client, monkeypatch):
    monkeypatch.setattr(
        "mlss_monitor.routes.pages.get_storage_status",
        lambda: {
            "images_dir": "/tmp/grow-test-imgs",
            "used_bytes": 95_000_000_000,
            "total_bytes": 100_000_000_000,
            "used_pct": 95.0,
            "threshold_pct": 90.0,
            "is_warning": True,
        },
    )
    r = client.get("/grow")
    body = r.data.decode("utf-8")
    assert r.status_code == 200
    assert "storage-warning" in body
    assert "95.0%" in body
    assert "/tmp/grow-test-imgs" in body


def test_grow_fleet_page_omits_storage_warning_when_safe(client, monkeypatch):
    monkeypatch.setattr(
        "mlss_monitor.routes.pages.get_storage_status",
        lambda: {
            "images_dir": "/tmp/grow-test-imgs",
            "used_bytes": 30_000_000_000,
            "total_bytes": 100_000_000_000,
            "used_pct": 30.0,
            "threshold_pct": 90.0,
            "is_warning": False,
        },
    )
    r = client.get("/grow")
    body = r.data.decode("utf-8")
    assert r.status_code == 200
    assert "storage-warning" not in body


def test_grow_fleet_page_omits_storage_warning_on_check_failure(
    client, monkeypatch,
):
    """If get_storage_status returns None (best-effort contract), the
    page still renders — no banner, no crash.
    """
    monkeypatch.setattr(
        "mlss_monitor.routes.pages.get_storage_status", lambda: None,
    )
    r = client.get("/grow")
    body = r.data.decode("utf-8")
    assert r.status_code == 200
    assert "storage-warning" not in body
