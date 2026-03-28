"""
Generate a self-signed TLS certificate and private key for HTTPS.

Usage:
    python scripts/generate_certs.py [--cert-dir DIR] [--ip IP] [--hostname NAME]

Creates ``cert.pem`` and ``key.pem`` inside *cert-dir* (default: ``certs/``).
Existing files are **not** overwritten unless ``--force`` is passed.

The certificate includes Subject Alternative Names (SANs) so browsers
accept it for the given IP / hostname without extra warnings.
"""

import argparse
import os
import socket
import subprocess
import sys
import tempfile


def _detect_local_ip():
    """Best-effort detection of the Pi's LAN IP address."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return None


def generate_self_signed_cert(
    cert_dir: str = "certs",
    force: bool = False,
    ip_addresses: list = None,
    hostnames: list = None,
):
    os.makedirs(cert_dir, exist_ok=True)
    cert_path = os.path.join(cert_dir, "cert.pem")
    key_path = os.path.join(cert_dir, "key.pem")

    if os.path.exists(cert_path) and os.path.exists(key_path) and not force:
        print(f"Certificates already exist in {cert_dir}/. Use --force to regenerate.")
        return cert_path, key_path

    # Build SAN entries
    san_entries = []
    for host in (hostnames or ["mlss-monitor", "localhost"]):
        san_entries.append(f"DNS:{host}")
    for ip in (ip_addresses or []):
        san_entries.append(f"IP:{ip}")
    san_entries.append("IP:127.0.0.1")

    # Auto-detect LAN IP if none provided
    if not ip_addresses:
        detected = _detect_local_ip()
        if detected:
            san_entries.append(f"IP:{detected}")
            print(f"Auto-detected LAN IP: {detected}")

    san_string = ",".join(san_entries)
    cn = (hostnames or ["mlss-monitor"])[0]

    # Write a temporary openssl config with SAN extensions
    ext_conf = (
        "[req]\n"
        "distinguished_name = req_dn\n"
        "x509_extensions = v3_ext\n"
        "prompt = no\n"
        f"[req_dn]\nCN = {cn}\nO = MLSS\n"
        f"[v3_ext]\nsubjectAltName = {san_string}\n"
        "basicConstraints = CA:FALSE\n"
        "keyUsage = digitalSignature, keyEncipherment\n"
        "extendedKeyUsage = serverAuth\n"
    )

    with tempfile.NamedTemporaryFile(mode="w", suffix=".cnf", delete=False) as f:
        f.write(ext_conf)
        cnf_path = f.name

    try:
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048",
                "-keyout", key_path,
                "-out", cert_path,
                "-days", "365",
                "-nodes",
                "-config", cnf_path,
            ],
            check=True,
        )
    finally:
        os.unlink(cnf_path)

    print(f"Generated self-signed certificate:\n  cert: {cert_path}\n  key:  {key_path}")
    print(f"  SANs: {san_string}")
    return cert_path, key_path


def main():
    parser = argparse.ArgumentParser(description="Generate self-signed TLS certs for MLSS Monitor")
    parser.add_argument("--cert-dir", default="certs", help="Directory to write cert/key files")
    parser.add_argument("--force", action="store_true", help="Overwrite existing certificates")
    parser.add_argument("--ip", action="append", default=None,
                        help="IP address to include in SAN (repeatable). Auto-detected if omitted.")
    parser.add_argument("--hostname", action="append", default=None,
                        help="Hostname to include in SAN (repeatable, default: mlss-monitor, localhost)")
    args = parser.parse_args()

    try:
        generate_self_signed_cert(
            cert_dir=args.cert_dir, force=args.force,
            ip_addresses=args.ip, hostnames=args.hostname,
        )
    except FileNotFoundError:
        print("Error: 'openssl' not found. Install OpenSSL and try again.", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as exc:
        print(f"Error generating certificates: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
