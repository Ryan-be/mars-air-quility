"""Pages: /grow renders the fleet template; nav has the Grow tab."""
import tempfile
import pytest


@pytest.fixture
def client(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # pylint: disable=R1732
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


# ---------------------------------------------------------------------------
# Grow sub-nav + URL consolidation
# ---------------------------------------------------------------------------
# The Grow Errors + Grow Settings top-nav links were collapsed into a single
# Grow tab; the corresponding sub-pages get a sub-nav row of three pills
# (Fleet / Errors / Settings) instead. The settings page also moved from
# /settings/grow → /grow/settings, with a 302 redirect kept on the legacy URL.

def _set_session(c, *, logged_in=True, role="admin"):
    with c.session_transaction() as sess:
        sess["logged_in"] = logged_in
        sess["user_role"] = role


def test_grow_settings_legacy_url_redirects_to_new_path(client):
    """Old bookmarks for /settings/grow keep working: any logged-in
    role gets a 302 to the canonical /grow/settings."""
    _set_session(client, role="admin")
    r = client.get("/settings/grow", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/grow/settings")


def test_grow_settings_legacy_url_redirects_for_viewer_too(client):
    """The legacy redirect doesn't gate on role — even a viewer hitting
    a stale bookmark gets the 302 (they'll then bounce off the
    admin-only canonical route, but at least the bookmark resolves)."""
    _set_session(client, logged_in=True, role="viewer")
    r = client.get("/settings/grow", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/grow/settings")


def test_grow_settings_new_url_serves_page(client):
    """GET /grow/settings as admin → 200 with the settings page body."""
    _set_session(client, role="admin")
    r = client.get("/grow/settings")
    assert r.status_code == 200
    body = r.data.decode("utf-8")
    assert "Grow unit settings" in body


def test_grow_subnav_appears_on_fleet_page(client):
    r = client.get("/grow")
    body = r.data.decode("utf-8")
    assert "subnav-pill" in body
    # Three pill destinations
    assert "/grow/errors" in body
    # Fleet pill — link to /grow itself
    assert "grow-subnav" in body


def test_grow_subnav_appears_on_errors_page(client):
    _set_session(client, role="viewer")
    r = client.get("/grow/errors")
    body = r.data.decode("utf-8")
    assert "subnav-pill" in body
    assert "grow-subnav" in body
    assert ">Errors" in body or "Errors\n" in body


def test_grow_subnav_appears_on_settings_page(client):
    _set_session(client, role="admin")
    r = client.get("/grow/settings")
    body = r.data.decode("utf-8")
    assert "subnav-pill" in body
    assert "grow-subnav" in body


def test_grow_subnav_marks_active_pill_correctly(client):
    """Active-pill detection uses request.endpoint (not URL string
    matching) so future URL renames don't break it."""
    _set_session(client, role="viewer")
    r = client.get("/grow/errors")
    body = r.data.decode("utf-8")
    # The Errors pill should carry the .active class. We look for the
    # combination "subnav-pill active" (with 'Errors' nearby) rather
    # than just any 'active' class — the latter would match the
    # top-level Grow tab's own .active.
    assert "subnav-pill active" in body
    # Fleet + Settings pills should NOT be marked active. Easiest
    # check: the Fleet href is `/grow` (without /errors suffix), and
    # if it had .active we'd see "subnav-pill active" preceding that
    # exact href. Simpler: count active pills in the subnav block.
    subnav_start = body.find("grow-subnav")
    subnav_end = body.find("</nav>", subnav_start)
    subnav_block = body[subnav_start:subnav_end]
    assert subnav_block.count("subnav-pill active") == 1


def test_top_nav_no_longer_includes_grow_errors_link(client):
    """The Errors link moved from the top nav into the sub-nav. Any
    page that extends base.html should NOT carry a top-level
    .tab-nav <a href="/grow/errors"> entry. The sub-nav is rendered
    inside .grow-subnav (different class), so we narrow our assertion
    to the .tab-nav block."""
    r = client.get("/")
    body = r.data.decode("utf-8")
    nav_start = body.find('class="tab-nav"')
    assert nav_start != -1
    nav_end = body.find("</nav>", nav_start)
    nav_block = body[nav_start:nav_end]
    assert "/grow/errors" not in nav_block


def test_top_nav_no_longer_includes_grow_settings_link(client):
    """Same as above for Grow Settings — moved into the admin pill of
    the sub-nav instead of being a top-level tab."""
    _set_session(client, role="admin")
    r = client.get("/")
    body = r.data.decode("utf-8")
    nav_start = body.find('class="tab-nav"')
    assert nav_start != -1
    nav_end = body.find("</nav>", nav_start)
    nav_block = body[nav_start:nav_end]
    # Neither the new path nor the legacy path should appear in the top nav
    assert "/grow/settings" not in nav_block
    assert "/settings/grow" not in nav_block


# ---------------------------------------------------------------------------
# "+ Add Unit" button on the fleet header (admin-only entry point for the
# enrollment-key reveal modal). The peek-once endpoint is also admin-gated
# server-side; the template-level hide is defence in depth so non-admins
# don't see a button that 403s when they click it.
# ---------------------------------------------------------------------------

def test_grow_fleet_admin_sees_add_unit_button(client):
    _set_session(client, role="admin")
    r = client.get("/grow")
    body = r.data.decode("utf-8")
    assert r.status_code == 200
    assert 'id="grow-add-btn"' in body
    # The role is also stamped on body.dataset.role so the modal can gate
    # its reveal button on the client side.
    assert 'document.body.dataset.role = "admin"' in body


def test_grow_fleet_viewer_does_not_see_add_unit_button(client):
    _set_session(client, logged_in=True, role="viewer")
    r = client.get("/grow")
    body = r.data.decode("utf-8")
    assert r.status_code == 200
    assert 'id="grow-add-btn"' not in body
    assert 'document.body.dataset.role = "viewer"' in body


def test_grow_fleet_controller_does_not_see_add_unit_button(client):
    _set_session(client, logged_in=True, role="controller")
    r = client.get("/grow")
    body = r.data.decode("utf-8")
    assert r.status_code == 200
    assert 'id="grow-add-btn"' not in body


def test_grow_fleet_loads_add_unit_modal_module(client):
    """The fleet view's JS module imports add-unit-modal.mjs to wire up
    the button click handler. Verifying the file exists keeps a missing
    import from silently breaking the button at runtime."""
    _set_session(client, role="admin")
    r = client.get("/static/js/grow/components/add-unit-modal.mjs")
    assert r.status_code == 200
    body = r.data.decode("utf-8")
    assert "openAddUnitModal" in body
    assert "/api/grow/enrollment-key/peek-once" in body


# /grow/docs/<name> — serves grow markdown docs from the repo so the empty-
# state's "Full setup guide" link works without depending on Flask's
# static handler (which only serves /static/, not /docs/).

def test_grow_doc_setup_returns_markdown_content(client):
    r = client.get("/grow/docs/setup")
    assert r.status_code == 200
    assert "text/markdown" in r.headers.get("Content-Type", "")
    body = r.data.decode("utf-8")
    # Sanity-check: the doc has a recognisable heading + the deployment-
    # critical install command. If either is removed the doc has lost
    # its purpose, not a CSS tweak.
    assert "PLANT_GROW_UNIT_SETUP" in body or "Plant Grow Unit" in body
    assert "/api/grow/install.sh" in body


def test_grow_doc_unknown_name_returns_404(client):
    r = client.get("/grow/docs/this-doc-does-not-exist")
    assert r.status_code == 404


def test_grow_doc_path_traversal_blocked(client):
    """A whitelist of allowed doc names — anything else 404s. Defends
    against /grow/docs/../../../etc/passwd-style abuse even though Flask
    routing already restricts <doc_name> to a single path segment."""
    for bad_name in ("..%2F..%2Fetc%2Fpasswd", "../../etc/passwd"):
        r = client.get(f"/grow/docs/{bad_name}")
        assert r.status_code in (400, 404), \
            f"path traversal {bad_name} should not reach a doc"
