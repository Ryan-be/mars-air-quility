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
