"""iOS .mobileconfig generator for the local TLS CA.

The hub ships a self-signed leaf TLS cert signed by a local root CA
(see scripts/generate_local_ca.sh). iOS Safari refuses to install a
PWA from a site with an untrusted cert; the operator installs this
mobileconfig profile on the iPhone, which adds the root CA to the
iOS trust store. Safari then trusts the hub's HTTPS cert and the
"Add to Home Screen" install path becomes available.

Profile format reference:
  https://developer.apple.com/business/documentation/Configuration-Profile-Reference.pdf
"""

import logging
import plistlib

log = logging.getLogger(__name__)

_CA_PATH   = "certs/ca.crt"
_CERT_PATH = "certs/cert.pem"


def read_ca_pem() -> str:
    """Return the PEM-encoded CA cert. Raises FileNotFoundError if missing."""
    with open(_CA_PATH, "r", encoding="utf-8") as f:
        return f.read()


def cert_not_after() -> str | None:
    """Return the leaf cert's notAfter date as an ISO string, or None on error."""
    try:
        from cryptography import x509
        from cryptography.hazmat.backends import default_backend
        with open(_CERT_PATH, "rb") as f:
            pem = f.read()
        cert = x509.load_pem_x509_certificate(pem, default_backend())
        # not_valid_after_utc is the cryptography 42+ name; fall back to the
        # legacy not_valid_after which uses naive UTC datetimes.
        not_after = getattr(cert, "not_valid_after_utc", None)
        if not_after is None:
            not_after = cert.not_valid_after
        return not_after.isoformat()
    except Exception as exc:  # pylint: disable=broad-except
        log.debug("cert_not_after failed: %s", exc)
        return None


def build_mobileconfig(
    ca_pem: str, payload_uuid: str, payload_org: str, payload_name: str
) -> bytes:
    """Assemble an iOS Configuration Profile (XML plist) embedding the CA."""
    plist = {
        "PayloadType":         "Configuration",
        "PayloadVersion":      1,
        "PayloadIdentifier":   "com.mlss.tls-trust",
        "PayloadUUID":         payload_uuid,
        "PayloadDisplayName":  payload_name,
        "PayloadDescription":  (
            "Installs the MLSS hub TLS root CA so iOS Safari trusts the "
            "hub's HTTPS certificate."
        ),
        "PayloadOrganization": payload_org,
        "PayloadContent": [
            {
                "PayloadType":         "com.apple.security.root",
                "PayloadVersion":      1,
                "PayloadIdentifier":   "com.mlss.tls-trust.ca",
                "PayloadUUID":         payload_uuid + "-ca",
                "PayloadDisplayName":  payload_name + " (root)",
                "PayloadDescription":  "MLSS root CA certificate",
                "PayloadContent":      ca_pem.encode("utf-8"),
            }
        ],
    }
    # pylint: disable-next=no-member  # plistlib.FMT_XML is 3.8+; runtime check sufficient
    return plistlib.dumps(plist, fmt=plistlib.FMT_XML)
