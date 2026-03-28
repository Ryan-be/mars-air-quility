"""
Generate a self-signed TLS certificate and private key for HTTPS.

Usage:
    python generate_certs.py [--cert-dir DIR]

Creates ``cert.pem`` and ``key.pem`` inside *cert-dir* (default: ``certs/``).
Existing files are **not** overwritten unless ``--force`` is passed.
"""

import argparse
import os
import subprocess
import sys


def generate_self_signed_cert(cert_dir: str = "certs", force: bool = False):
    os.makedirs(cert_dir, exist_ok=True)
    cert_path = os.path.join(cert_dir, "cert.pem")
    key_path = os.path.join(cert_dir, "key.pem")

    if os.path.exists(cert_path) and os.path.exists(key_path) and not force:
        print(f"Certificates already exist in {cert_dir}/. Use --force to regenerate.")
        return cert_path, key_path

    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", key_path,
            "-out", cert_path,
            "-days", "365",
            "-nodes",
            "-subj", "/CN=mlss-monitor/O=MLSS/C=US",
        ],
        check=True,
    )

    print(f"Generated self-signed certificate:\n  cert: {cert_path}\n  key:  {key_path}")
    return cert_path, key_path


def main():
    parser = argparse.ArgumentParser(description="Generate self-signed TLS certs for MLSS Monitor")
    parser.add_argument("--cert-dir", default="certs", help="Directory to write cert/key files")
    parser.add_argument("--force", action="store_true", help="Overwrite existing certificates")
    args = parser.parse_args()

    try:
        generate_self_signed_cert(cert_dir=args.cert_dir, force=args.force)
    except FileNotFoundError:
        print("Error: 'openssl' not found. Install OpenSSL and try again.", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as exc:
        print(f"Error generating certificates: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
