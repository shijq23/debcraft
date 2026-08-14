#!/usr/bin/env bash
# Generates fixtures/images/test.tar — a minimal Docker-format tarball containing
# a manifest.json, a repositories file, and two layer tarballs (one with a
# synthetic var/lib/dpkg/status file with one package entry).
# Requires: tar

set -euo pipefail

# --- Tool presence check ---
if ! command -v tar >/dev/null 2>&1; then
    echo "Error: tar is not installed. Install it with: apt install tar" >&2
    exit 1
fi

# --- Paths ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="${SCRIPT_DIR}/images"
OUTPUT_FILE="${OUTPUT_DIR}/test.tar"

# Ensure output directory exists
mkdir -p "${OUTPUT_DIR}"

# --- Create staging directory ---
STAGING_DIR="$(mktemp -d)"
trap 'rm -rf "${STAGING_DIR}"' EXIT

# --- Deterministic tar options ---
TAR_OPTS=(--sort=name --owner=0 --group=0 --numeric-owner "--mtime=2024-01-01 00:00:00")

# --- Layer 1: base layer with /etc/os-release ---
LAYER1_ID="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
LAYER1_DIR="${STAGING_DIR}/${LAYER1_ID}"
mkdir -p "${LAYER1_DIR}"

# Create layer content
LAYER1_CONTENT="$(mktemp -d)"
mkdir -p "${LAYER1_CONTENT}/etc"
cat > "${LAYER1_CONTENT}/etc/os-release" <<'EOF'
PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"
NAME="Debian GNU/Linux"
VERSION_ID="12"
ID=debian
EOF

# Create layer.tar
tar -cf "${LAYER1_DIR}/layer.tar" "${TAR_OPTS[@]}" -C "${LAYER1_CONTENT}" .

# Layer metadata
echo '{"id":"'"${LAYER1_ID}"'","created":"2024-01-01T00:00:00Z"}' > "${LAYER1_DIR}/json"
echo "1.0" > "${LAYER1_DIR}/VERSION"

# --- Layer 2: application layer with dpkg status ---
LAYER2_ID="b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3"
LAYER2_DIR="${STAGING_DIR}/${LAYER2_ID}"
mkdir -p "${LAYER2_DIR}"

# Create layer content with dpkg status
LAYER2_CONTENT="$(mktemp -d)"
mkdir -p "${LAYER2_CONTENT}/var/lib/dpkg"
cat > "${LAYER2_CONTENT}/var/lib/dpkg/status" <<'EOF'
Package: base-files
Status: install ok installed
Priority: required
Section: admin
Architecture: amd64
Version: 13.5
Description: Debian base system miscellaneous files
EOF

# Create layer.tar
tar -cf "${LAYER2_DIR}/layer.tar" "${TAR_OPTS[@]}" -C "${LAYER2_CONTENT}" .

# Layer metadata
echo '{"id":"'"${LAYER2_ID}"'","parent":"'"${LAYER1_ID}"'","created":"2024-01-01T00:00:01Z"}' > "${LAYER2_DIR}/json"
echo "1.0" > "${LAYER2_DIR}/VERSION"

# --- manifest.json ---
cat > "${STAGING_DIR}/manifest.json" <<EOF
[{"Config":"config.json","RepoTags":["debcraft-test:latest"],"Layers":["${LAYER1_ID}/layer.tar","${LAYER2_ID}/layer.tar"]}]
EOF

# --- config.json ---
cat > "${STAGING_DIR}/config.json" <<EOF
{"architecture":"amd64","os":"linux","rootfs":{"type":"layers","diff_ids":["sha256:${LAYER1_ID}","sha256:${LAYER2_ID}"]},"history":[{"created":"2024-01-01T00:00:00Z","comment":"base layer"},{"created":"2024-01-01T00:00:01Z","comment":"app layer"}]}
EOF

# --- repositories ---
cat > "${STAGING_DIR}/repositories" <<'EOF'
{"debcraft-test":{"latest":"b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3"}}
EOF

# --- Generate Docker tarball ---
# Use explicit file list to avoid ./ prefix that confuses tarball member lookups
tar -cf "${OUTPUT_FILE}" "${TAR_OPTS[@]}" -C "${STAGING_DIR}" \
    "${LAYER1_ID}" \
    "${LAYER2_ID}" \
    config.json \
    manifest.json \
    repositories

echo "Created ${OUTPUT_FILE}"
