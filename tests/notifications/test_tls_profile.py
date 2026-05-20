"""Tests for the iOS .mobileconfig generator + CA cert endpoint."""

import plistlib

import pytest

from mlss_monitor.notifications import tls_profile


_DUMMY_CA = (
    "-----BEGIN CERTIFICATE-----\n"
    "MIIBkTCB+w+...\nfakecontentfortesting\n"
    "-----END CERTIFICATE-----\n"
)


def test_build_mobileconfig_returns_valid_plist():
    blob = tls_profile.build_mobileconfig(
        _DUMMY_CA, "aaaa-bbbb", "MLSS", "MLSS Root CA")
    parsed = plistlib.loads(blob)
    assert parsed["PayloadType"] == "Configuration"
    assert parsed["PayloadOrganization"] == "MLSS"
    assert parsed["PayloadDisplayName"] == "MLSS Root CA"
    assert parsed["PayloadIdentifier"] == "com.mlss.tls-trust"
    assert parsed["PayloadUUID"] == "aaaa-bbbb"


def test_mobileconfig_embeds_ca_in_payload_content():
    blob = tls_profile.build_mobileconfig(
        _DUMMY_CA, "u1", "MLSS", "MLSS Root CA")
    parsed = plistlib.loads(blob)
    inner = parsed["PayloadContent"][0]
    assert inner["PayloadType"] == "com.apple.security.root"
    # plistlib decodes the inline base64 into bytes
    assert _DUMMY_CA.encode("utf-8") in inner["PayloadContent"]


def test_read_ca_pem_missing_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(tls_profile, "_CA_PATH", str(tmp_path / "no.pem"))
    with pytest.raises(FileNotFoundError):
        tls_profile.read_ca_pem()


def test_read_ca_pem_present_returns_content(tmp_path, monkeypatch):
    p = tmp_path / "ca.pem"
    p.write_text(_DUMMY_CA, encoding="utf-8")
    monkeypatch.setattr(tls_profile, "_CA_PATH", str(p))
    assert tls_profile.read_ca_pem() == _DUMMY_CA


def test_cert_not_after_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(tls_profile, "_CERT_PATH", str(tmp_path / "no.pem"))
    assert tls_profile.cert_not_after() is None
