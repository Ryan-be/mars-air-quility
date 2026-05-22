"""Tests for the ``/controls`` page (MLSS topology view).

Phase 4 ships the page chrome: four host elements (topbar, graph,
statusbar, sidepanel) plus a ``data-role`` stamp on body so admin-only
controls can be revealed without leaking server-side role data into the
client. The actual graph + node cards land in Phase 5-6.
"""
import tempfile

import pytest


@pytest.fixture
def app_client(monkeypatch):
    """Flask test client backed by a fresh schema-primed tempfile DB.

    Mirrors ``tests.grow_server.test_grow_pages.client`` so this module
    is independent of the conftest ``app_client`` fixture (which forces
    an admin session — we want to exercise the default-viewer path too).
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # pylint: disable=R1732
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    init_db.create_db()

    from mlss_monitor.app import app
    app.config["TESTING"] = True
    return app.test_client(), tmp.name


def test_controls_page_has_topology_hosts(app_client):
    """All four host elements + data-role stamp render on /controls.

    Phase 5 mounts the graph into ``#tp-graph-host``, Phase 6 mounts
    node cards into the side panel host, etc — locking the IDs now
    means each subsequent phase has a stable mount point to target
    without a template churn that breaks every prior test.
    """
    client, _ = app_client
    r = client.get("/controls")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert 'id="tp-topbar-host"' in body
    assert 'id="tp-graph-host"' in body
    assert 'id="tp-statusbar-host"' in body
    assert 'id="tp-sidepanel-host"' in body
    # data-role is set by an inline script so the page can be rendered
    # the same way for viewer/controller/admin and the client-side
    # gating reads it on boot. Confirm the attribute name is wired
    # — the value depends on session state and is asserted separately.
    assert 'data-role=' in body
