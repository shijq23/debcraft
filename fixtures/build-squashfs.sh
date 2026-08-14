#!/usr/bin/env bash
# Generates fixtures/images/test.squashfs — a tiny squashfs image containing a
# synthetic var/lib/dpkg/status file with one package entry, plus nested
# directories and a small text file for listing/read tests.
# Requires: mksquashfs (squashfs-tools)

set -euo pipefail

# --- Tool presence check ---
if ! command -v mksquashfs >/dev/null 2>&1; then
    echo "Error: mksquashfs is not installed. Install it with: apt install squashfs-tools" >&2
    exit 1
fi

# --- Paths ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="${SCRIPT_DIR}/images"
OUTPUT_FILE="${OUTPUT_DIR}/test.squashfs"

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

# --- Create nested directories and a small text file ---
mkdir -p "${STAGING_DIR}/usr/bin"
mkdir -p "${STAGING_DIR}/etc"
echo "debcraft-test" > "${STAGING_DIR}/etc/hostname"

# --- Generate squashfs image ---
mksquashfs "${STAGING_DIR}" "${OUTPUT_FILE}" -noappend -quiet

echo "Created ${OUTPUT_FILE}"
