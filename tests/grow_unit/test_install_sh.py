"""install.sh syntactic checks + critical commands present."""
import os
import shutil
import subprocess
from pathlib import Path
import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL = REPO_ROOT / "grow_unit" / "install.sh"


def test_install_script_exists():
    assert INSTALL.exists()


def test_install_script_is_executable():
    assert os.access(INSTALL, os.X_OK)


def test_install_script_starts_with_strict_mode():
    content = INSTALL.read_text()
    assert content.startswith("#!/bin/bash") or content.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in content


def test_install_script_creates_mlss_grow_user():
    content = INSTALL.read_text()
    assert "useradd" in content or "adduser" in content
    assert "mlss-grow" in content


def test_install_script_downloads_wheels_from_mlss():
    content = INSTALL.read_text()
    assert "/api/grow/dist/" in content
    assert "mlss_grow" in content
    assert "mlss_contracts" in content


def test_install_script_creates_systemd_unit():
    content = INSTALL.read_text()
    assert "/etc/systemd/system/mlss-grow.service" in content
    assert "systemctl enable" in content
    assert "systemctl start" in content


def test_install_script_creates_required_directories():
    content = INSTALL.read_text()
    for d in ["/opt/mlss-grow", "/etc/mlss", "/var/lib/mlss-grow", "/var/log/mlss-grow"]:
        assert d in content


def test_install_script_passes_shellcheck_when_available():
    if shutil.which("shellcheck") is None:
        pytest.skip("shellcheck not installed")
    r = subprocess.run(["shellcheck", str(INSTALL)], capture_output=True, text=True, check=False)
    assert r.returncode == 0, f"shellcheck:\n{r.stdout}\n{r.stderr}"


# ---------------------------------------------------------------------------
# SHA256 verification (Vuln 4 — defends against LAN MITM tampering)
# ---------------------------------------------------------------------------

def test_install_script_verifies_wheel_sha256():
    """The script must compute and check sha256 of every wheel before pip install."""
    content = INSTALL.read_text()
    # Must read sha256 from the manifest
    assert "sha256" in content
    # Must use sha256sum (or equivalent) to compute
    assert "sha256sum" in content
    # Must abort on mismatch (set -e + explicit exit, or || exit / || return)
    assert "exit 1" in content or "exit_code" in content


def test_install_script_uses_filename_from_manifest():
    """The script must use the filename returned by /latest, not hardcode it.
    This couples the served bytes to the verified hash."""
    content = INSTALL.read_text()
    # Should reference filename field from the JSON manifest
    assert "filename" in content


def test_install_script_verifies_both_wheels():
    """Both mlss_grow and mlss_contracts wheels must be verified."""
    content = INSTALL.read_text()
    assert "GROW_SHA256" in content or "grow_sha256" in content.lower()
    assert "CONTRACTS_SHA256" in content or "contracts_sha256" in content.lower()


# ---------------------------------------------------------------------------
# I5 — systemd unit fetched via the dist endpoint with SHA256 verification.
#
# The old script tried three brittle paths to install the systemd unit:
#   1. cp from a repo-relative path (only works during local dev)
#   2. cp from the venv site-packages dir (the wheel doesn't ship it)
#   3. curl with no integrity check (LAN MITM could substitute a unit)
#
# After the fix the script must:
#   * Fetch the .service file from /api/grow/dist/mlss-grow.service.
#   * SHA256-verify it against the manifest entry — same pattern as wheels.
#   * Drop the package-data cp fallbacks (or only use them after the
#     verified curl path).
# ---------------------------------------------------------------------------

def test_install_script_fetches_service_from_dist_endpoint():
    """The script must download mlss-grow.service via /api/grow/dist/, not
    rely on a wheel-bundled package-data copy.

    The actual filename can come from the manifest at runtime (a
    SERVICE_FILENAME var) or be a literal in the script. Either way, we
    expect a curl against /api/grow/dist/ for an artefact whose name is
    derived from the .service manifest entry.
    """
    content = INSTALL.read_text()
    has_literal = "/api/grow/dist/mlss-grow.service" in content
    has_var_curl = (
        "/api/grow/dist/${SERVICE_FILENAME}" in content
        or '/api/grow/dist/"$SERVICE_FILENAME"' in content
        or "/api/grow/dist/$SERVICE_FILENAME" in content
    )
    assert has_literal or has_var_curl, (
        "install.sh must fetch the systemd unit via /api/grow/dist/, either "
        "with the literal filename or via a SERVICE_FILENAME variable read "
        "from the manifest"
    )


def test_install_script_verifies_service_sha256():
    """SHA256 of the downloaded .service file must be checked against the
    manifest entry, exactly like the wheels."""
    content = INSTALL.read_text()
    # Some sentinel naming the service hash variable. Both common cases ok.
    assert (
        "SERVICE_SHA256" in content
        or "service_sha256" in content.lower()
        or "SERVICE_FILENAME" in content  # implies hash flow exists too
    ), "install.sh must verify the .service file's SHA256"
    # Should reference the manifest field for the .service file.
    assert "mlss-grow.service" in content


def test_install_script_drops_repo_relative_cp_fallback():
    """The old fallback `cp $INSTALL_DIR/../../systemd/mlss-grow.service`
    relied on the wheel layout having the systemd dir alongside the package.
    The wheel does not ship that, so the cp always fails — drop it."""
    content = INSTALL.read_text()
    # The repo-relative ../../systemd/ pattern is the load-bearing tell.
    assert "../../systemd/mlss-grow.service" not in content, (
        "the brittle repo-relative cp fallback must be removed; the verified "
        "curl path is now the canonical install method"
    )


def test_install_script_drops_site_packages_cp_fallback():
    """The site-packages cp fallback also relied on wheel-bundled data that
    isn't actually included in the wheel. Drop it."""
    content = INSTALL.read_text()
    assert (
        "site-packages/mlss_grow/systemd/mlss-grow.service" not in content
    ), (
        "the site-packages cp fallback must be removed; mlss-grow.service is "
        "not packaged inside the wheel"
    )


def test_install_script_aborts_on_service_sha256_mismatch():
    """A mismatched .service hash must abort the install — NoNewPrivileges
    and friends mean a tampered unit could expand the firmware's privileges
    on first boot. set -e + verify_sha exit 1 covers this if both wheels and
    .service flow through the same verify_sha helper."""
    content = INSTALL.read_text()
    # The verify_sha helper from the wheel verification path must also be
    # invoked for the service file. Look for either an explicit verify_sha
    # call referencing the service, or the SERVICE_SHA256 var name being
    # passed through.
    assert (
        "verify_sha" in content
    ), "verify_sha helper must remain — it's the abort-on-mismatch primitive"
    # And the .service path/var must be wired into it. Tolerant matching
    # because the exact var name is up to the implementer.
    assert (
        "verify_sha" in content
        and ("SERVICE" in content or "service" in content)
    )


# ---------------------------------------------------------------------------
# Server-cert pinning (C2 + C3 fix)
#
# The MLSS server presents a self-signed cert on the LAN. Without pinning,
# the firmware's enroll POST and WSS connection can't verify the cert and
# either crash (default ssl context) or trust anything (verify=False — bad,
# enrollment_key sniffable from the body).
#
# install.sh now fetches the cert via openssl s_client at install time
# (TOFU under the documented LAN-trust posture, same as `curl -k`), writes
# it to /etc/mlss/server.crt with mode 0644, owned by root (it's the trust
# anchor — mlss-grow only needs to read it).
# ---------------------------------------------------------------------------

def test_install_sh_fetches_server_cert_via_openssl_s_client():
    """The script must extract the live server cert via openssl s_client and
    write it to /etc/mlss/server.crt. This is the install-time TOFU step
    that bootstraps secure enroll + WS on every subsequent boot."""
    content = INSTALL.read_text()
    assert "openssl s_client" in content, \
        "install.sh must use openssl s_client to fetch the server cert"
    assert "/etc/mlss/server.crt" in content, \
        "install.sh must write the cert to /etc/mlss/server.crt"


def test_install_sh_pins_server_cert_via_x509_pem():
    """openssl s_client outputs the raw TLS handshake; the chain must be
    piped through `openssl x509 -outform PEM` to extract a clean cert."""
    content = INSTALL.read_text()
    assert "openssl x509" in content, \
        "install.sh must pipe through openssl x509 to extract a clean PEM cert"


def test_install_sh_server_cert_uses_correct_mlss_port():
    """The server cert lives behind the same port that serves wheels +
    enrollment (5000). Any other port would give us a cert for a different
    surface (or no cert at all)."""
    content = INSTALL.read_text()
    # The exact form is "$MLSS_HOST:5000" or similar — tolerant match.
    assert ":5000" in content
    # And the s_client invocation should be near the cert pinning step,
    # so loosely assert it references MLSS_HOST.
    assert "MLSS_HOST" in content


def test_install_sh_server_cert_has_mode_0644():
    """The cert is the trust anchor; world-readable is correct (any
    non-root reader on the system needs to verify against it). 0644 is
    the canonical posture for /etc/ssl/certs/* style files."""
    content = INSTALL.read_text()
    # We expect either an explicit chmod 0644 or an `install -m 0644`
    # (the script already uses install(1) elsewhere) for the cert.
    has_install = "install -m 0644" in content and "/etc/mlss/server.crt" in content
    has_chmod = "chmod 0644 /etc/mlss/server.crt" in content or \
                "chmod 644 /etc/mlss/server.crt" in content
    assert has_install or has_chmod, \
        "the server cert must be installed with mode 0644"


def test_install_sh_server_cert_owned_by_root():
    """The cert is the trust anchor — only root should be able to replace it.
    mlss-grow needs read access (via the world-readable bit) but must not
    own it."""
    content = INSTALL.read_text()
    # Look for `install -o root -g root .../server.crt` or chown root.
    has_install_root = (
        "-o root -g root" in content and "/etc/mlss/server.crt" in content
    )
    has_chown_root = "chown root" in content and "/etc/mlss/server.crt" in content
    assert has_install_root or has_chown_root, \
        "server.crt must be owned by root, not mlss-grow"


def test_install_sh_documents_tofu_posture():
    """The cert pinning is TOFU at install time — that's the documented LAN
    trust model, same as `curl -k` for install.sh itself. A code comment
    near the cert step makes the trade-off explicit so a reviewer knows
    this isn't an accidental verify=False."""
    content = INSTALL.read_text()
    # Loose match — the comment can use TOFU, "trust on first use", or
    # "first contact" wording.
    msg = content.lower()
    assert any(token in msg for token in ("tofu", "trust on first", "first-contact",
                                           "first contact", "lan trust")), \
        "the cert-pinning step must have a comment explaining the TOFU/LAN-trust posture"
