#!/usr/bin/env bash
# Generates fixtures/images/test.img — a raw ext4 disk image (≤4 MB) containing
# a synthetic var/lib/dpkg/status file with one package entry.
# Requires: dd (coreutils), mkfs.ext4, debugfs (e2fsprogs)
#
# This script uses debugfs to write files into the ext4 image without requiring
# root privileges or mount operations, making it portable for CI environments.
#
# Idempotency: Uses E2FSPROGS_FAKE_TIME to pin filesystem timestamps, fixed UUID
# and hash_seed for deterministic metadata. Running multiple times produces
# byte-identical output.

set -euo pipefail

# --- Tool presence checks ---
if ! command -v dd >/dev/null 2>&1; then
    echo "Error: dd is not installed. Install it with: apt install coreutils" >&2
    exit 1
fi

if ! command -v mkfs.ext4 >/dev/null 2>&1; then
    echo "Error: mkfs.ext4 is not installed. Install it with: apt install e2fsprogs" >&2
    exit 1
fi

if ! command -v debugfs >/dev/null 2>&1; then
    echo "Error: debugfs is not installed. Install it with: apt install e2fsprogs" >&2
    exit 1
fi

# --- Paths ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="${SCRIPT_DIR}/images"
OUTPUT_FILE="${OUTPUT_DIR}/test.img"

# Ensure output directory exists
mkdir -p "${OUTPUT_DIR}"

# --- Create staging directory ---
STAGING_DIR="$(mktemp -d)"
trap 'rm -rf "${STAGING_DIR}"' EXIT

# --- Write synthetic dpkg status file ---
cat > "${STAGING_DIR}/status" <<'EOF'
Package: base-files
Status: install ok installed
Priority: required
Section: admin
Architecture: amd64
Version: 13.5
Description: Debian base system miscellaneous files
EOF

# --- Fixed timestamp for idempotent filesystem metadata ---
# E2FSPROGS_FAKE_TIME pins creation/modification timestamps in ext4 metadata
# so that repeated runs produce byte-identical images.
export E2FSPROGS_FAKE_TIME="1700000000"

# --- Create raw disk image (4 MB) ---
dd if=/dev/zero of="${OUTPUT_FILE}" bs=1M count=4 status=none

# --- Format with ext4 ---
# -F forces creation on a regular file (not a block device).
# -q suppresses informational output.
# -U sets a fixed UUID for deterministic superblock content.
# -E hash_seed= sets a fixed hash seed for deterministic directory indexing.
# -L sets a fixed volume label.
mkfs.ext4 -q -F \
    -U "12345678-1234-1234-1234-123456789abc" \
    -E "hash_seed=87654321-4321-4321-4321-cba987654321" \
    -L "DEBCRAFT_TEST" \
    "${OUTPUT_FILE}"

# --- Write files into the ext4 image using debugfs ---
# debugfs -w opens the filesystem for writing without mounting.
# We create the directory structure and write the dpkg status file.
# E2FSPROGS_FAKE_TIME ensures inode timestamps are also deterministic.
debugfs -w -f /dev/stdin "${OUTPUT_FILE}" <<DEBUGFS_CMDS
mkdir var
mkdir var/lib
mkdir var/lib/dpkg
write ${STAGING_DIR}/status var/lib/dpkg/status
DEBUGFS_CMDS

echo "Created ${OUTPUT_FILE}"
