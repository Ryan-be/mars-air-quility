"""Tests for /api/admin/tls/* endpoints."""

import pytest
from flask import Flask

from mlss_monitor.routes.api_tls import api_tls_bp


_DUMMY_CA = (
    "-----BEGIN CERTIFICATE-----\n"
    "MIIBkTCB+w+...\nfaketestcontent\n"
    "-----END CERTIFICATE-----\n"
)


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "mlss_monitor.notifications.tls_profile._CA_PATH",
        str(tmp_path / "ca.crt"),
    )
    monkeypatch.setattr(
        "mlss_monitor.notifications.tls_profile._CERT_PATH",
        str(tmp_path / "cert.pem"),
    )
    a = Flask(__name__)
    a.config["TESTING"] = True
    a.secret_key = "test"
    a.register_blueprint(api_tls_bp)
    a.config["_TMP"] = tmp_path
    return a


@pytest.fixture
def admin_client(app):
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["logged_in"] = True
            sess["user"] = "alice"
            sess["user_role"] = "admin"
            sess["user_id"] = 1
        yield c


def test_status_no_files(admin_client):
    r = admin_client.get("/api/admin/tls/status")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ca_exists"] is False
    assert body["cert_exists"] is False
    assert body["cert_not_after"] is None


def test_status_ca_only(admin_client, app):
    (app.config["_TMP"] / "ca.crt").write_text(_DUMMY_CA, encoding="utf-8")
    body = admin_client.get("/api/admin/tls/status").get_json()
    assert body["ca_exists"] is True
    assert body["cert_exists"] is False


def test_ca_download_missing_404(admin_client):
    r = admin_client.get("/api/admin/tls/ca.crt")
    assert r.status_code == 404


def test_ca_download_present(admin_client, app):
    (app.config["_TMP"] / "ca.crt").write_text(_DUMMY_CA, encoding="utf-8")
    r = admin_client.get("/api/admin/tls/ca.crt")
    assert r.status_code == 200
    assert "x509" in r.headers["Content-Type"] or \
           "ca-cert" in r.headers["Content-Type"]
    assert "mlss-root-ca.crt" in r.headers.get("Content-Disposition", "")


def test_mobileconfig_missing_ca_404(admin_client):
    r = admin_client.get("/api/admin/tls/ios-profile.mobileconfig")
    assert r.status_code == 404


def test_mobileconfig_present(admin_client, app):
    (app.config["_TMP"] / "ca.crt").write_text(_DUMMY_CA, encoding="utf-8")
    r = admin_client.get("/api/admin/tls/ios-profile.mobileconfig")
    assert r.status_code == 200
    assert "apple-aspen-config" in r.headers["Content-Type"]
    assert "mlss-mobile.mobileconfig" in r.headers.get("Content-Disposition", "")
    # Verify it parses as plist
    import plistlib
    parsed = plistlib.loads(r.data)
    assert parsed["PayloadType"] == "Configuration"


def test_non_admin_blocked(app):
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["logged_in"] = True
            sess["user"] = "viewer-user"
            sess["user_role"] = "viewer"
            sess["user_id"] = 2
        r = c.get("/api/admin/tls/status")
        assert r.status_code in (302, 403)
