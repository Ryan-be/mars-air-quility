"""Page route + nav link integration for /settings/grow.

Admin-only page; the three section divs the JS orchestrator mounts into
are present in the rendered HTML so the static/js/grow/settings.mjs
loader can find them.
"""
import tempfile

import pytest


@pytest.fixture
def client(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # pylint: disable=R1732
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


def test_grow_settings_page_returns_200_for_admin(client):
    _set_session(client, role="admin")
    r = client.get("/settings/grow")
    assert r.status_code == 200, r.data
    body = r.data.decode("utf-8")
    # Loads the JS orchestrator
    assert "/static/js/grow/settings.mjs" in body
    # Has the three section slots the orchestrator targets
    assert 'id="grow-settings-key"' in body
    assert 'id="grow-settings-profiles"' in body
    assert 'id="grow-settings-holiday"' in body


def test_grow_settings_page_redirects_viewer(client):
    _set_session(client, logged_in=True, role="viewer")
    r = client.get("/settings/grow")
    # require_role redirects non-API requests rather than returning 403
    assert r.status_code in (302, 303)


def test_grow_settings_page_redirects_controller(client):
    _set_session(client, logged_in=True, role="controller")
    r = client.get("/settings/grow")
    assert r.status_code in (302, 303)


def test_grow_settings_page_redirects_anonymous(client):
    _set_session(client, logged_in=False, role="viewer")
    r = client.get("/settings/grow")
    # Unauthenticated → redirected to login
    assert r.status_code in (302, 303)


def test_admin_dashboard_nav_includes_grow_settings_link(client):
    """When an admin renders any base.html-extended page, the nav row
    includes a 'Grow Settings' link pointing to /settings/grow.
    """
    _set_session(client, role="admin")
    r = client.get("/")
    body = r.data.decode("utf-8")
    assert "/settings/grow" in body
    assert "Grow Settings" in body


def test_non_admin_does_not_see_grow_settings_link(client):
    """The link is admin-gated in base.html — viewers and controllers
    don't get the entrypoint.
    """
    _set_session(client, logged_in=True, role="viewer")
    r = client.get("/")
    body = r.data.decode("utf-8")
    assert "/settings/grow" not in body
