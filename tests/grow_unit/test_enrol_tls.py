"""enroll_unit TLS posture: pinned-cert verification with safe fallback.

The MLSS server runs a self-signed cert on the LAN. The firmware ships
`/etc/mlss/server.crt` (fetched and pinned by install.sh at install time
under the documented LAN-trust model). Once that file exists, every
enroll POST must verify against it — the previous `verify=False` posture
exposes the enrollment_key to anyone on the LAN doing an MITM, since the
key travels in the request body.

For dev/test convenience, when the cert file is missing, fall back to
`verify=False` AND log a prominent warning so the insecure posture is
visible in logs.
"""
import logging
from unittest.mock import MagicMock
import pytest
from mlss_grow.enrol import enroll_unit
from mlss_grow.config import FirstbootConfig


def _cfg(server_cert_path: str | None = None):
    cfg = FirstbootConfig(
        mlss_host="mlss.local", enrollment_key="key123",
        plant_name="Tomato", plant_type="tomato", medium="soil",
    )
    if server_cert_path is not None:
        cfg.server_cert_path = server_cert_path
    return cfg


def _ok_response():
    fake = MagicMock()
    fake.status_code = 201
    fake.json = lambda: {"unit_id": 1, "token": "t"}
    return fake


def test_enroll_uses_pinned_cert_when_present(monkeypatch, tmp_path):
    """When the configured cert path exists on disk, requests.post must be
    called with verify=<that path>. This is the production secure-by-default
    posture: pinned-cert TLS so the enrollment_key isn't sniffable."""
    cert_path = tmp_path / "server.crt"
    cert_path.write_text("-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----\n")

    fake_post = MagicMock(return_value=_ok_response())
    monkeypatch.setattr("mlss_grow.enrol.requests.post", fake_post)

    enroll_unit(_cfg(server_cert_path=str(cert_path)), hardware_serial="hw-1")

    assert fake_post.call_args.kwargs["verify"] == str(cert_path), \
        "verify= must be the pinned-cert path string"


def test_enroll_falls_back_to_verify_false_when_cert_missing(monkeypatch, tmp_path, caplog):
    """When the cert file isn't present (dev/test, or pre-install), the call
    must still go through with verify=False AND emit a warning so an
    operator scanning logs notices the insecure posture."""
    missing = tmp_path / "does-not-exist.crt"
    fake_post = MagicMock(return_value=_ok_response())
    monkeypatch.setattr("mlss_grow.enrol.requests.post", fake_post)

    with caplog.at_level(logging.WARNING):
        enroll_unit(_cfg(server_cert_path=str(missing)), hardware_serial="hw-1")

    assert fake_post.call_args.kwargs["verify"] is False, \
        "missing cert must fall back to verify=False"
    # And a warning must have been logged.
    assert any(rec.levelno >= logging.WARNING for rec in caplog.records), \
        "missing cert must log a WARNING (or higher)"


def test_enroll_warning_message_mentions_insecure(monkeypatch, tmp_path, caplog):
    """The fallback warning must use one of the words an operator would
    search for when triaging an insecure-posture incident."""
    missing = tmp_path / "absent.crt"
    monkeypatch.setattr("mlss_grow.enrol.requests.post",
                        MagicMock(return_value=_ok_response()))

    with caplog.at_level(logging.WARNING):
        enroll_unit(_cfg(server_cert_path=str(missing)), hardware_serial="hw-1")

    msg = " ".join(rec.getMessage().lower() for rec in caplog.records)
    assert any(word in msg for word in ("insecure", "mitm", "verify=false", "verify false")), \
        f"warning text must flag the insecure posture; got: {msg!r}"
