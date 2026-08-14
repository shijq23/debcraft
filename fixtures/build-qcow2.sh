#!/usr/bin/env bash
# Generates fixtures/images/test.qcow2 — a QCOW2 disk image derived from the
# raw IMG fixture (test.img) using qemu-img convert.
# Requires: qemu-img (qemu-utils)
#
# This script converts the raw ext4 disk image produced by build-img.sh into
# QCOW2 format. If test.img does not exist, build-img.sh is invoked first.
#
# Idempotency: The input (test.img) is deterministic, and qemu-img convert
# produces deterministic output for a given input, so repeated runs produce
# byte-identical output.

set -euo pipefail

# --- Tool presence check ---
if ! command -v qemu-img >/dev/null 2>&1; then
    echo "Error: qemu-img is not installed. Install it with: apt install qemu-utils" >&2
    exit 1
fi

# --- Paths ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="${SCRIPT_DIR}/images"
OUTPUT_FILE="${OUTPUT_DIR}/test.qcow2"
IMG_FILE="${OUTPUT_DIR}/test.img"
BUILD_IMG_SCRIPT="${SCRIPT_DIR}/build-img.sh"

# Ensure output directory exists
mkdir -p "${OUTPUT_DIR}"

# --- Ensure source IMG exists ---
if [ ! -f "${IMG_FILE}" ]; then
    echo "test.img not found, building it first..."
    bash "${BUILD_IMG_SCRIPT}"
fi

# --- Convert IMG to QCOW2 ---
# -c enables compression to keep output under 100 KB.
# cluster_size=512 minimizes overhead for this small, mostly-sparse image.
qemu-img convert -c -f raw -O qcow2 -o cluster_size=512 "${IMG_FILE}" "${OUTPUT_FILE}"

echo "Created ${OUTPUT_FILE}"
