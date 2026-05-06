"""WSClient TLS posture: SSLContext built from a pinned cert path.

Mirror of test_enrol_tls.py for the WSS leg. The MLSS server presents a
self-signed cert; without verification override, websockets.connect
(which uses the system default verifying ssl context) raises
SSL: CERTIFICATE_VERIFY_FAILED.

Production: pin against /etc/mlss/server.crt (TOFU-installed by install.sh).
Dev/test: no cert path → CERT_NONE + warning.
"""
import logging
import ssl
from unittest.mock import AsyncMock, MagicMock
import pytest
from mlss_grow.ws_client import WSClient, _default_connect


@pytest.mark.asyncio
async def test_default_connect_uses_pinned_cert_when_provided(monkeypatch, tmp_path):
    """A real cert at the configured path → SSLContext loads it as a CA,
    keeps default check_hostname + CERT_REQUIRED."""
    # Generate a real self-signed cert so load_verify_locations() actually
    # parses it (a fake string would error out before we can introspect ctx).
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        from datetime import datetime, timedelta
    except ImportError:
        pytest.skip("cryptography lib not available")

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "mlss.local")]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.utcnow())
        .not_valid_after(datetime.utcnow() + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "server.crt"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    captured = {}

    async def fake_connect(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return MagicMock()

    import websockets
    monkeypatch.setattr(websockets, "connect", fake_connect)

    await _default_connect("wss://mlss.local:5001/api/grow/1/ws", "tok",
                           str(cert_path))

    ctx = captured["kwargs"]["ssl"]
    assert isinstance(ctx, ssl.SSLContext), "ssl= must be an SSLContext"
    assert ctx.verify_mode == ssl.CERT_REQUIRED, \
        "pinned-cert path must enforce CERT_REQUIRED"
    assert ctx.check_hostname is True, \
        "pinned-cert path must keep hostname verification on"


@pytest.mark.asyncio
async def test_default_connect_falls_back_to_cert_none_when_no_cert(monkeypatch, caplog):
    """No cert path → the firmware can't verify; flip to CERT_NONE and warn."""
    captured = {}

    async def fake_connect(url, **kwargs):
        captured["kwargs"] = kwargs
        return MagicMock()

    import websockets
    monkeypatch.setattr(websockets, "connect", fake_connect)

    with caplog.at_level(logging.WARNING):
        await _default_connect("wss://mlss.local:5001/api/grow/1/ws", "tok", None)

    ctx = captured["kwargs"]["ssl"]
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_NONE
    assert ctx.check_hostname is False
    assert any(rec.levelno >= logging.WARNING for rec in caplog.records)


@pytest.mark.asyncio
async def test_default_connect_falls_back_when_cert_path_does_not_exist(monkeypatch, tmp_path, caplog):
    """A configured-but-missing cert file is treated like None: warn + CERT_NONE."""
    captured = {}

    async def fake_connect(url, **kwargs):
        captured["kwargs"] = kwargs
        return MagicMock()

    import websockets
    monkeypatch.setattr(websockets, "connect", fake_connect)

    missing = str(tmp_path / "nope.crt")
    with caplog.at_level(logging.WARNING):
        await _default_connect("wss://mlss.local:5001/api/grow/1/ws", "tok", missing)

    ctx = captured["kwargs"]["ssl"]
    assert ctx.verify_mode == ssl.CERT_NONE
    assert ctx.check_hostname is False
    assert any(rec.levelno >= logging.WARNING for rec in caplog.records)


@pytest.mark.asyncio
async def test_default_connect_passes_authorization_header(monkeypatch, tmp_path):
    """Regression: the bearer-token header survived the SSL context wiring."""
    async def fake_connect(url, **kwargs):
        return MagicMock(_kwargs=kwargs)

    import websockets
    monkeypatch.setattr(websockets, "connect", fake_connect)

    ws = await _default_connect("wss://mlss.local:5001/api/grow/1/ws", "tok-abc", None)
    headers = ws._kwargs.get("extra_headers") or ws._kwargs.get("additional_headers")
    assert headers is not None, "Authorization header must still be sent"
    # Header structure varies (dict vs list-of-tuples) — normalize:
    if isinstance(headers, dict):
        assert headers.get("Authorization") == "Bearer tok-abc"
    else:
        assert any(k == "Authorization" and v == "Bearer tok-abc"
                   for k, v in headers)


@pytest.mark.asyncio
async def test_ws_client_passes_cert_path_to_connect_fn(tmp_path):
    """WSClient(server_cert_path=...) must forward that path to the connect_fn
    on every reconnect. This is what wires cfg.server_cert_path through to
    the real _default_connect at boot."""
    captured = {}

    async def recording_connect(url, token, cert_path):
        captured["url"] = url
        captured["token"] = token
        captured["cert_path"] = cert_path
        return MagicMock()

    client = WSClient(
        url="wss://mlss.local:5001/api/grow/1/ws",
        token="tok",
        buffer_db_path=str(tmp_path / "b.sqlite"),
        on_command=lambda cmd: None,
        connect_fn=recording_connect,
        server_cert_path="/etc/mlss/server.crt",
    )
    await client._connect_once()

    assert captured["cert_path"] == "/etc/mlss/server.crt"
    assert captured["url"] == "wss://mlss.local:5001/api/grow/1/ws"
    assert captured["token"] == "tok"


@pytest.mark.asyncio
async def test_ws_client_default_cert_path_is_none(tmp_path):
    """Backward-compat: omitting server_cert_path → connect_fn called with None."""
    captured = {}

    async def recording_connect(url, token, cert_path):
        captured["cert_path"] = cert_path
        return MagicMock()

    client = WSClient(
        url="wss://x", token="t", buffer_db_path=str(tmp_path / "b.sqlite"),
        on_command=lambda cmd: None,
        connect_fn=recording_connect,
    )
    await client._connect_once()
    assert captured["cert_path"] is None
