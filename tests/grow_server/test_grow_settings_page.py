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
    """Canonical URL is now /grow/settings (was /settings/grow).
    The legacy URL keeps working via redirect (see
    test_grow_settings_legacy_url_redirects_to_new_path)."""
    _set_session(client, role="admin")
    r = client.get("/grow/settings")
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
    r = client.get("/grow/settings")
    # require_role redirects non-API requests rather than returning 403
    assert r.status_code in (302, 303)


def test_grow_settings_page_redirects_controller(client):
    _set_session(client, logged_in=True, role="controller")
    r = client.get("/grow/settings")
    assert r.status_code in (302, 303)


def test_grow_settings_page_redirects_anonymous(client):
    _set_session(client, logged_in=False, role="viewer")
    r = client.get("/grow/settings")
    # Unauthenticated → redirected to login
    assert r.status_code in (302, 303)


def test_admin_grow_subnav_includes_settings_link(client):
    """The Grow sub-nav (rendered on every /grow* page) includes a
    Settings pill pointing to /grow/settings for admins. The
    top-level nav no longer carries this link — it lives in the
    sub-nav instead."""
    _set_session(client, role="admin")
    r = client.get("/grow")
    body = r.data.decode("utf-8")
    assert "/grow/settings" in body
    # The pill text — capital S, lowercase rest, distinct from the
    # old top-nav "Grow Settings" wording.
    assert ">Settings" in body or "Settings\n" in body


def test_non_admin_does_not_see_grow_settings_link(client):
    """The Settings pill is admin-gated in the sub-nav — viewers and
    controllers don't see it. They also don't get the legacy
    top-level link (which was removed entirely)."""
    _set_session(client, logged_in=True, role="viewer")
    r = client.get("/grow")
    body = r.data.decode("utf-8")
    assert "/grow/settings" not in body
    assert "/settings/grow" not in body
