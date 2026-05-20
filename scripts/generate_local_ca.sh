#!/usr/bin/env bash
# Generate (or refresh) the local MLSS TLS CA + leaf cert.
#
# Usage:
#   bash scripts/generate_local_ca.sh [extra-hostname1 extra-hostname2 ...]
#   bash scripts/generate_local_ca.sh --dry-run
#
# Idempotent: re-running keeps the CA (so existing iOS profiles stay
# valid) but re-issues the leaf cert. Run from the repo root.
#
# Why a CA? iOS Safari rejects bare self-signed certs for PWA install.
# The CA + iOS profile install via /admin lets us bypass the limitation
# without paying for Apple's $99/yr developer cert.

set -euo pipefail

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=1
    shift
fi

CERT_DIR="certs"
CA_KEY="${CERT_DIR}/ca.key"
CA_CRT="${CERT_DIR}/ca.crt"
LEAF_KEY="${CERT_DIR}/cert.key"
LEAF_CRT="${CERT_DIR}/cert.pem"
LEAF_CSR="${CERT_DIR}/cert.csr"
SAN_CONF="${CERT_DIR}/.san.cnf"

CA_DAYS=3650
LEAF_DAYS=1825

# Detect primary LAN IP (best-effort).
LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
if [[ -z "${LAN_IP}" ]]; then
    # macOS fallback
    LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || true)"
fi

EXTRA_HOSTNAMES=("$@")

run() {
    if [[ "${DRY_RUN}" -eq 1 ]]; then
        echo "[dry-run] $*"
    else
        echo "+ $*"
        eval "$*"
    fi
}

mkdir -p "${CERT_DIR}"

# Build SAN list.
SAN_ENTRIES=("DNS:mlss.local" "DNS:localhost")
if [[ -n "${LAN_IP}" ]]; then
    SAN_ENTRIES+=("IP:${LAN_IP}")
fi
for h in "${EXTRA_HOSTNAMES[@]}"; do
    SAN_ENTRIES+=("DNS:${h}")
done
SAN_STRING="$(IFS=, ; echo "${SAN_ENTRIES[*]}")"

echo "==> SAN list: ${SAN_STRING}"

# CA (only if missing).
if [[ -f "${CA_CRT}" && -f "${CA_KEY}" ]]; then
    echo "==> Root CA already present (${CA_CRT}) — keeping it."
else
    echo "==> Generating new root CA (10-year validity)"
    run "openssl genrsa -out '${CA_KEY}' 4096"
    run "openssl req -x509 -new -nodes -key '${CA_KEY}' -sha256 -days ${CA_DAYS} \
         -subj '/CN=MLSS Root CA/O=MLSS/C=GB' \
         -out '${CA_CRT}'"
fi

# Leaf cert (always re-issued).
echo "==> Issuing leaf cert (${LEAF_DAYS} days)"

if [[ "${DRY_RUN}" -eq 1 ]]; then
    echo "[dry-run] would write SAN config to ${SAN_CONF}"
else
    cat > "${SAN_CONF}" <<EOF
[req]
distinguished_name = req_dn
req_extensions     = v3_req
prompt             = no

[req_dn]
CN = MLSS Hub
O  = MLSS

[v3_req]
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = ${SAN_STRING}
EOF
fi

run "openssl genrsa -out '${LEAF_KEY}' 2048"
run "openssl req -new -key '${LEAF_KEY}' -out '${LEAF_CSR}' -config '${SAN_CONF}'"
run "openssl x509 -req -in '${LEAF_CSR}' -CA '${CA_CRT}' -CAkey '${CA_KEY}' \
     -CAcreateserial -out '${LEAF_CRT}' -days ${LEAF_DAYS} -sha256 \
     -extensions v3_req -extfile '${SAN_CONF}'"

if [[ "${DRY_RUN}" -eq 0 ]]; then
    rm -f "${LEAF_CSR}" "${SAN_CONF}"
fi

cat <<EOF

==> Done.

CA cert : ${CA_CRT}
Hub cert: ${LEAF_CRT}
Hub key : ${LEAF_KEY}

Next steps to install on an iPhone:
  1. Visit https://<hub>/admin in Safari on the phone.
  2. Click 'Download iOS Profile' under the Mobile install card.
  3. Open Settings → 'Profile Downloaded' → Install → enter passcode.
  4. Settings → General → About → Certificate Trust Settings →
     toggle ON for 'MLSS Root CA'.
  5. Visit https://<hub>/ in Safari — padlock should be green now.
  6. Share → Add to Home Screen.

EOF
