#!/usr/bin/env bash
# Generates fixtures/images/test.iso — a tiny ISO 9660 image containing a
# synthetic var/lib/dpkg/status file with one package entry.
# Requires: genisoimage

set -euo pipefail

# --- Tool presence check ---
if ! command -v genisoimage >/dev/null 2>&1; then
    echo "Error: genisoimage is not installed. Install it with: apt install genisoimage" >&2
    exit 1
fi

# --- Paths ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="${SCRIPT_DIR}/images"
OUTPUT_FILE="${OUTPUT_DIR}/test.iso"

# Ensure output directory exists
mkdir -p "${OUTPUT_DIR}"

# --- Create staging directory ---
STAGING_DIR="$(mktemp -d)"
trap 'rm -rf "${STAGING_DIR}"' EXIT

# --- Write synthetic dpkg status file ---
mkdir -p "${STAGING_DIR}/var/lib/dpkg"
cat > "${STAGING_DIR}/var/lib/dpkg/status" <<'EOF'
Package: base-files
Status: install ok installed
Priority: required
Section: admin
Architecture: amd64
Version: 13.5
Description: Debian base system miscellaneous files
EOF

# --- Generate ISO ---
genisoimage -quiet -J -R -V "DEBCRAFT_TEST" -o "${OUTPUT_FILE}" "${STAGING_DIR}"

echo "Created ${OUTPUT_FILE}"
