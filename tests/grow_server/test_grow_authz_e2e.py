"""Stack-level test: the full Flask app boots, the auth gate is wired in,
unauthorised requests to grow hardware-actuating endpoints fail closed.

This is the layer above test_grow_commands.py - it uses the real app
factory (not a hand-built Flask instance) to catch any blueprint
registration / decorator-stacking regression that would let the
endpoints leak through unauth.
"""
import sqlite3
import tempfile
from datetime import datetime
import pytest


@pytest.fixture
def real_app_client(monkeypatch):
    """Spin up the actual mlss_monitor.app.app with grow plumbing wired in."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # pylint: disable=R1732
    tmp.close()
    import database.init_db as init_db
    import database.db_logger as dbl
    import database.user_db as udb
    init_db.DB_FILE = tmp.name
    dbl.DB_FILE = tmp.name
    udb.DB_FILE = tmp.name

    # Patch grow modules' DB_FILE so they all see the test DB
    for mod in ["mlss_monitor.grow.auth", "mlss_monitor.grow.handlers",
                "mlss_monitor.grow.photo_storage", "mlss_monitor.routes.api_grow_enroll",
                "mlss_monitor.routes.api_grow_units", "mlss_monitor.routes.api_grow_dist",
                "mlss_monitor.routes.api_grow_history", "mlss_monitor.routes.api_grow_photos"]:
        try:
            monkeypatch.setattr(f"{mod}.DB_FILE", tmp.name)
        except AttributeError:
            pass

    init_db.create_db()

    # Insert a unit so the endpoint has something to address
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (1, 'h', 'X', ?, 'h', ?)",
        (datetime.utcnow(), datetime.utcnow()),
    )
    conn.commit()
    conn.close()

    # Build the real app
    import mlss_monitor.app as app_module
    monkeypatch.setattr(app_module, "LOG_INTERVAL", 99999)
    app_module.app.config["TESTING"] = True
    app_module.app.config["SECRET_KEY"] = "test-secret"

    # Set up a registered WS registry so endpoints can find it (otherwise
    # they 503 on registry=None - but that's the WRONG error class for these
    # tests; we want 401/403 from the auth gate).
    from mlss_monitor.grow.ws_registry import WSRegistry
    from mlss_monitor import state
    state.grow_ws_registry = WSRegistry()
    # Don't need a real WS connection - these tests assert on authz, which
    # short-circuits before _push_command_blocking touches the registry.

    with app_module.app.test_client() as client:
        yield client


def _set_session(c, *, logged_in, role):
    with c.session_transaction() as sess:
        sess["logged_in"] = logged_in
        sess["user_role"] = role


def test_real_app_water_now_denies_anonymous(real_app_client):
    """End-to-end: real Flask app, no session -> 401."""
    _set_session(real_app_client, logged_in=False, role="viewer")
    r = real_app_client.post("/api/grow/units/1/water-now", json={"duration_s": 5})
    assert r.status_code == 401


def test_real_app_water_now_denies_viewer(real_app_client):
    """End-to-end: real Flask app, viewer session -> 403."""
    _set_session(real_app_client, logged_in=True, role="viewer")
    r = real_app_client.post("/api/grow/units/1/water-now", json={"duration_s": 5})
    assert r.status_code == 403


def test_real_app_identify_denies_anonymous(real_app_client):
    _set_session(real_app_client, logged_in=False, role="viewer")
    r = real_app_client.post("/api/grow/units/1/identify")
    assert r.status_code == 401


def test_real_app_identify_denies_viewer(real_app_client):
    _set_session(real_app_client, logged_in=True, role="viewer")
    r = real_app_client.post("/api/grow/units/1/identify")
    assert r.status_code == 403


def test_real_app_read_endpoint_still_works_for_viewer(real_app_client):
    """GET /api/grow/units must remain accessible to viewers - only writes are gated.
    This proves we didn't accidentally over-restrict the read API."""
    _set_session(real_app_client, logged_in=True, role="viewer")
    r = real_app_client.get("/api/grow/units")
    assert r.status_code == 200


def test_real_app_water_now_admin_reaches_handler_returns_503_on_no_unit_ws(real_app_client):
    """Admin auth passes; downstream returns 503 unit_not_connected since
    no unit has registered a WS. This proves the auth gate is the first
    short-circuit, then the rest of the chain executes normally."""
    _set_session(real_app_client, logged_in=True, role="admin")
    r = real_app_client.post("/api/grow/units/1/water-now", json={"duration_s": 5})
    assert r.status_code == 503
    assert r.get_json()["error"] == "unit_not_connected"
