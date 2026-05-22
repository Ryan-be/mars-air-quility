"""Stack-level: a real wss:// connection to a TLS-enabled listener works.

Generates a self-signed cert in tmp_path, wires it into start_ws_listener,
opens a websockets.connect(wss://...) with cert verification disabled
(self-signed), and asserts the registry sees the connection.

Skips if `cryptography` isn't installed (it should be, via authlib).
"""
import asyncio
import sqlite3
import ssl
import tempfile
from datetime import datetime, timedelta
import pytest


def _gen_self_signed(tmp_path):
    """Write a self-signed cert + key to tmp_path; return (cert_path, key_path).

    The SAN list contains both a dnsName ("127.0.0.1" — historical, not
    technically valid but tolerated by the no-verify path) AND an
    iPAddress entry. The latter is what Python's hostname verification
    requires when the client connects via a literal IP, so the new
    pinned-cert tests can exercise full hostname-checked verification."""
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        import ipaddress
    except ImportError:
        pytest.skip("cryptography lib not available")

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1"),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.utcnow())
        .not_valid_after(datetime.utcnow() + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("127.0.0.1"),
                x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return str(cert_path), str(key_path)


@pytest.fixture
def tls_server(monkeypatch, tmp_path):
    """Spin up a real TLS-enabled grow WS listener on a random port."""
    cert_path, key_path = _gen_self_signed(tmp_path)

    # DB
    tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # pylint: disable=R1732
    tmp_db.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp_db.name
    monkeypatch.setattr("mlss_monitor.grow.auth.DB_FILE", tmp_db.name)
    monkeypatch.setattr("mlss_monitor.routes.api_grow_ws.DB_FILE", tmp_db.name)
    monkeypatch.setattr("mlss_monitor.grow.handlers.DB_FILE", tmp_db.name)
    monkeypatch.setattr("mlss_monitor.grow.photo_storage.DB_FILE", tmp_db.name)
    init_db.create_db()

    # Enrol a unit
    from mlss_monitor.grow.auth import generate_token, hash_secret
    raw = generate_token()
    conn = sqlite3.connect(tmp_db.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (1, 'h', 'X', ?, ?, ?)",
        (datetime.utcnow(), hash_secret(raw), datetime.utcnow()),
    )
    conn.commit()
    conn.close()

    # Build TLS context
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_path, key_path)

    # Start listener with TLS
    from mlss_monitor.grow.ws_registry import WSRegistry
    from mlss_monitor.routes.api_grow_ws import (
        start_ws_listener, stop_ws_listener, _clear_auth_cache,
    )
    _clear_auth_cache()
    registry = WSRegistry()
    handle = start_ws_listener(
        host="127.0.0.1", port=0,
        registry=registry,
        ssl_context=ctx,
    )
    port = handle.sockets[0].getsockname()[1]  # pylint: disable=no-member

    yield port, raw, registry

    stop_ws_listener(handle)


@pytest.mark.asyncio
async def test_wss_connection_succeeds_with_valid_cert_and_token(tls_server):
    """End-to-end: a real wss:// client can connect to a TLS-enabled listener
    with a valid bearer token. This proves the full TLS-handshake ->
    process_request auth -> connection-handler chain works as documented."""
    import websockets

    port, token, registry = tls_server

    # Self-signed cert — disable verification client-side (matches firmware
    # `verify=False` on its self-signed-MLSS deployment posture)
    client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client_ctx.check_hostname = False
    client_ctx.verify_mode = ssl.CERT_NONE

    async with websockets.connect(
        f"wss://127.0.0.1:{port}/api/grow/1/ws",
        ssl=client_ctx,
        extra_headers={"Authorization": f"Bearer {token}"},
    ) as _ws:
        await asyncio.sleep(0.1)
        assert registry.is_connected(1) is True


@pytest.mark.asyncio
async def test_plain_ws_connection_to_tls_listener_fails(tls_server):
    """Plain ws:// client must NOT be able to connect to a TLS listener.
    Confirms TLS is actually enforced (not just optional)."""
    import websockets

    port, token, _ = tls_server
    with pytest.raises(Exception):  # OSError, ConnectionClosed, or InvalidMessage
        async with websockets.connect(
            f"ws://127.0.0.1:{port}/api/grow/1/ws",
            extra_headers={"Authorization": f"Bearer {token}"},
        ):
            pass


@pytest.fixture
def tls_server_with_cert_path(monkeypatch, tmp_path):
    """Variant of `tls_server` that exposes the cert path so the firmware-side
    WSClient can pin against it (mirrors /etc/mlss/server.crt in prod)."""
    cert_path, key_path = _gen_self_signed(tmp_path)

    tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # pylint: disable=R1732
    tmp_db.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp_db.name
    monkeypatch.setattr("mlss_monitor.grow.auth.DB_FILE", tmp_db.name)
    monkeypatch.setattr("mlss_monitor.routes.api_grow_ws.DB_FILE", tmp_db.name)
    monkeypatch.setattr("mlss_monitor.grow.handlers.DB_FILE", tmp_db.name)
    monkeypatch.setattr("mlss_monitor.grow.photo_storage.DB_FILE", tmp_db.name)
    init_db.create_db()

    from mlss_monitor.grow.auth import generate_token, hash_secret
    raw = generate_token()
    conn = sqlite3.connect(tmp_db.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (1, 'h', 'X', ?, ?, ?)",
        (datetime.utcnow(), hash_secret(raw), datetime.utcnow()),
    )
    conn.commit()
    conn.close()

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_path, key_path)

    from mlss_monitor.grow.ws_registry import WSRegistry
    from mlss_monitor.routes.api_grow_ws import (
        start_ws_listener, stop_ws_listener, _clear_auth_cache,
    )
    _clear_auth_cache()
    registry = WSRegistry()
    handle = start_ws_listener(
        host="127.0.0.1", port=0, registry=registry, ssl_context=ctx,
    )
    port = handle.sockets[0].getsockname()[1]  # pylint: disable=no-member

    yield port, raw, registry, cert_path
    stop_ws_listener(handle)


def _seed_resolver_with_loopback(monkeypatch):
    """Make ``hub_candidates()`` yield exactly one 127.0.0.1 candidate.

    Background: ``WSClient._try_connect_once`` iterates the mDNS-resilient
    host_resolver (host file -> cache file -> mDNS) to pick the hub IP and
    *ignores the URL's hostname* — by design, since prod URLs carry a
    placeholder. In CI none of the 3 resolution steps yields anything (no
    /etc/mlss/host, no /etc/mlss/host-cache, no mDNS responder), so the
    test's ``wss://127.0.0.1:<port>`` URL is never even tried. Inject a
    deterministic candidate so the integration code under test (SSLContext
    build + handshake) actually runs.
    """
    from mlss_grow import host_resolver, ws_client

    def _fake_candidates(*_args, **_kwargs):
        yield host_resolver.Candidate(
            ip="127.0.0.1", source=host_resolver.Source.HOST,
        )
    monkeypatch.setattr(ws_client, "hub_candidates", _fake_candidates)
    # The success path also calls record_successful_connect, which writes
    # to /etc/mlss/host-cache. Stub it so the test doesn't touch system paths.
    monkeypatch.setattr(ws_client, "record_successful_connect", lambda *_a, **_k: None)


@pytest.mark.asyncio
async def test_ws_client_handshakes_with_pinned_cert_against_self_signed_listener(
    tls_server_with_cert_path, monkeypatch,
):
    """Stack-level proof: WSClient configured with server_cert_path can
    actually establish a TLS connection to a self-signed-cert listener.
    This is the integration of:
      - install.sh writes the cert to /etc/mlss/server.crt
      - FirstbootConfig.server_cert_path carries it through
      - _default_connect builds an SSLContext that loads it as a CA
      - websockets.connect handshakes and passes verification

    Skipped when mlss_grow isn't importable from this env (it lives in a
    separate poetry env). The grow_unit/ test suite runs the same
    SSLContext logic in isolation; this is the cross-package proof.
    """
    pytest.importorskip("mlss_grow")
    from mlss_grow.ws_client import WSClient, _default_connect

    port, token, registry, cert_path = tls_server_with_cert_path
    _seed_resolver_with_loopback(monkeypatch)

    # The listener cert was issued for CN=127.0.0.1 + SAN dnsName=127.0.0.1.
    # Connect to 127.0.0.1 so hostname verification (kept ON for the pinned
    # cert path) can succeed.
    #
    # Bypass the host_resolver chain by injecting _default_connect as the
    # explicit connect_fn. The resolver path (hub_candidates) reads from
    # /etc/mlss/host and /etc/mlss/host-cache, which aren't seeded in CI,
    # so it would either yield no candidates or yield ones that don't
    # point at the test listener on 127.0.0.1. The connect_fn injection
    # is the documented test-only escape hatch (see _try_connect_once
    # docstring) — the URL is then used as-is, which is exactly what
    # this stack-level test needs.
    received_commands = []
    client = WSClient(
        url=f"wss://127.0.0.1:{port}/api/grow/1/ws",
        token=token,
        buffer_db_path=":memory:",  # not used for this test
        on_command=received_commands.append,
        server_cert_path=cert_path,
        connect_fn=_default_connect,
    )

    ok = await client._connect_once()
    assert ok is True, "TLS handshake against pinned-cert listener must succeed"
    assert client.is_connected()

    # Brief pause for the server to register the connection
    await asyncio.sleep(0.1)
    assert registry.is_connected(1) is True

    # Tidy up: close the underlying websocket
    if client._ws is not None:
        await client._ws.close()


@pytest.mark.asyncio
async def test_ws_client_fails_when_pinned_cert_is_for_a_different_host(
    tls_server_with_cert_path, tmp_path, monkeypatch,
):
    """Negative case: if the pinned cert is unrelated to the server's cert,
    the handshake must fail. Confirms verification is real, not a no-op.

    Skipped when mlss_grow isn't importable (separate poetry env)."""
    pytest.importorskip("mlss_grow")
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from mlss_grow.ws_client import WSClient, _default_connect
    _dt = datetime
    _td = timedelta

    port, token, _, _ = tls_server_with_cert_path
    # Seed the resolver so the handshake actually reaches the listener
    # (and therefore actually fails on cert mismatch, not on "no candidates").
    _seed_resolver_with_loopback(monkeypatch)

    # Make a totally unrelated self-signed cert and pin against THAT.
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "different.example")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_dt.utcnow())
        .not_valid_after(_dt.utcnow() + _td(days=1))
        .sign(key, hashes.SHA256())
    )
    bogus_path = tmp_path / "wrong.crt"
    bogus_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    # Inject connect_fn to bypass host_resolver — same reason as the
    # positive-case test above. Without this, the assertion would pass
    # spuriously via HostUnreachable from an unseeded resolver chain,
    # never actually exercising the TLS mismatch we're testing.
    client = WSClient(
        url=f"wss://127.0.0.1:{port}/api/grow/1/ws",
        token=token,
        buffer_db_path=":memory:",
        on_command=lambda cmd: None,
        server_cert_path=str(bogus_path),
        connect_fn=_default_connect,
    )
    ok = await client._connect_once()
    assert ok is False, "pinning the wrong cert must cause handshake failure"
