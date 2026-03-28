"""Tests for HTTPS / TLS configuration."""

import os
import ssl
import subprocess
from unittest.mock import patch

import pytest

# conftest.py stubs hardware modules before any app import
import mlss_monitor.app as app_module


class TestBuildSslContext:  # pylint: disable=protected-access
    """Unit tests for _build_ssl_context."""

    def test_returns_none_when_disabled(self):
        with patch.object(app_module, "HTTPS_ENABLED", False):
            assert app_module._build_ssl_context() is None

    def test_returns_none_when_cert_missing(self, tmp_path):
        with (
            patch.object(app_module, "HTTPS_ENABLED", True),
            patch.object(app_module, "SSL_CERT_FILE", str(tmp_path / "no-cert.pem")),
            patch.object(app_module, "SSL_KEY_FILE", str(tmp_path / "no-key.pem")),
        ):
            assert app_module._build_ssl_context() is None

    def test_returns_ssl_context_with_valid_certs(self, tmp_path):
        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"

        # Generate a real self-signed cert for the test
        try:
            subprocess.run(
                [
                    "openssl", "req", "-x509", "-newkey", "rsa:2048",
                    "-keyout", str(key), "-out", str(cert),
                    "-days", "1", "-nodes",
                    "-subj", "/CN=test",
                ],
                check=True,
                capture_output=True,
            )
        except FileNotFoundError:
            pytest.skip("openssl not available")

        with (
            patch.object(app_module, "HTTPS_ENABLED", True),
            patch.object(app_module, "SSL_CERT_FILE", str(cert)),
            patch.object(app_module, "SSL_KEY_FILE", str(key)),
        ):
            ctx = app_module._build_ssl_context()
            assert isinstance(ctx, ssl.SSLContext)
            assert ctx.minimum_version == ssl.TLSVersion.TLSv1_2


class TestGenerateCerts:
    """Tests for the generate_certs helper script."""

    def test_generate_creates_files(self, tmp_path):
        try:
            subprocess.run(["openssl", "version"], check=True, capture_output=True)
        except FileNotFoundError:
            pytest.skip("openssl not available")

        from scripts.generate_certs import generate_self_signed_cert

        cert, key = generate_self_signed_cert(cert_dir=str(tmp_path))
        assert os.path.isfile(cert)
        assert os.path.isfile(key)

    def test_generate_does_not_overwrite(self, tmp_path):
        try:
            subprocess.run(["openssl", "version"], check=True, capture_output=True)
        except FileNotFoundError:
            pytest.skip("openssl not available")

        from scripts.generate_certs import generate_self_signed_cert

        generate_self_signed_cert(cert_dir=str(tmp_path))
        first_mtime = os.path.getmtime(tmp_path / "cert.pem")

        generate_self_signed_cert(cert_dir=str(tmp_path))
        assert os.path.getmtime(tmp_path / "cert.pem") == first_mtime

    def test_generate_force_overwrites(self, tmp_path):
        try:
            subprocess.run(["openssl", "version"], check=True, capture_output=True)
        except FileNotFoundError:
            pytest.skip("openssl not available")

        from scripts.generate_certs import generate_self_signed_cert

        generate_self_signed_cert(cert_dir=str(tmp_path))
        first_mtime = os.path.getmtime(tmp_path / "cert.pem")

        import time
        time.sleep(0.1)  # ensure mtime difference
        generate_self_signed_cert(cert_dir=str(tmp_path), force=True)
        assert os.path.getmtime(tmp_path / "cert.pem") != first_mtime
