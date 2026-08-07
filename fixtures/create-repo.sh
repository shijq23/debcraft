#!/usr/bin/env bash
set -euo pipefail

# Default values
SUITE="stable"
ARCH="amd64"
COMPONENT="main"
OUTPUT_DIR="fixtures/repositories/"
PACKAGES=()
GENERATE_METADATA=false

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS] REPO_NAME

Create an APT repository structure and generate metadata from pool contents.

Options:
  -s, --suite SUITE          Suite name (default: stable)
  -a, --arch ARCHITECTURES   Comma-separated architectures (default: amd64)
  -c, --component COMPONENT  Component name (default: main)
  -o, --output-dir DIR       Output directory (default: fixtures/repositories/)
  -p, --add-package DEB      Add a .deb to the pool (repeatable)
  -m, --generate-metadata    Generate Packages/Release indexes
  -h, --help                 Show usage
EOF
}

die() {
    echo "Error: $1" >&2
    exit 1
}

check_tool() {
    local tool="$1"
    local msg="$2"
    if ! command -v "$tool" &>/dev/null; then
        die "$msg"
    fi
}

# Extract package name from .deb filename
# e.g. hello_1.0-1_all.deb -> hello
get_package_name() {
    local filename
    filename="$(basename "$1")"
    echo "${filename%%_*}"
}

# Get pool prefix following Debian conventions
# lib* packages use first four characters, others use first character
get_pool_prefix() {
    local pkg_name="$1"
    if [[ "$pkg_name" == lib* ]]; then
        echo "${pkg_name:0:4}"
    else
        echo "${pkg_name:0:1}"
    fi
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        -s|--suite)
            SUITE="$2"
            shift 2
            ;;
        -a|--arch)
            ARCH="$2"
            shift 2
            ;;
        -c|--component)
            COMPONENT="$2"
            shift 2
            ;;
        -o|--output-dir)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        -p|--add-package)
            PACKAGES+=("$2")
            shift 2
            ;;
        -m|--generate-metadata)
            GENERATE_METADATA=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        -*)
            die "Unknown option: $1"
            ;;
        *)
            break
            ;;
    esac
done

# Check for repo name
if [[ $# -lt 1 ]]; then
    usage >&2
    exit 1
fi

REPO_NAME="$1"

# Check output directory is writable
if [[ -e "$OUTPUT_DIR" && ! -w "$OUTPUT_DIR" ]]; then
    die "Cannot write to ${OUTPUT_DIR}"
fi

# Build repo root path
REPO_DIR="${OUTPUT_DIR%/}/${REPO_NAME}"

# Parse comma-separated architectures into an array
IFS=',' read -ra ARCH_LIST <<< "$ARCH"

# Create directory structure
mkdir -p "${REPO_DIR}/pool/${COMPONENT}"
for arch in "${ARCH_LIST[@]}"; do
    mkdir -p "${REPO_DIR}/dists/${SUITE}/${COMPONENT}/binary-${arch}"
done

# Add packages to pool
for deb_path in "${PACKAGES[@]}"; do
    if [[ ! -f "$deb_path" ]]; then
        die "File not found: ${deb_path}"
    fi

    pkg_name="$(get_package_name "$deb_path")"
    prefix="$(get_pool_prefix "$pkg_name")"
    pool_dir="${REPO_DIR}/pool/${COMPONENT}/${prefix}/${pkg_name}"
    mkdir -p "$pool_dir"
    cp "$deb_path" "$pool_dir/"
done

# Generate metadata if requested
if [[ "$GENERATE_METADATA" == true ]]; then
    check_tool dpkg-scanpackages "dpkg-scanpackages not found. Install dpkg-dev."
    check_tool apt-ftparchive "apt-ftparchive not found. Install apt-utils."
    check_tool gzip "gzip not found."

    # Generate Packages and Packages.gz for each architecture
    for arch in "${ARCH_LIST[@]}"; do
        binary_dir="${REPO_DIR}/dists/${SUITE}/${COMPONENT}/binary-${arch}"

        # Run dpkg-scanpackages from the repo root
        (cd "$REPO_DIR" && dpkg-scanpackages --arch "$arch" pool/${COMPONENT}/) > "${binary_dir}/Packages"

        # Compress with gzip -n -k (keep original, no timestamps for reproducibility)
        gzip -n -k -f "${binary_dir}/Packages"
    done

    # Generate Release file
    ARCH_SPACE="${ARCH_LIST[*]}"
    RELEASE_DATE="$(date -u -R)"

    # First generate checksums from apt-ftparchive (before writing Release to avoid self-reference)
    APT_RELEASE="$(cd "${REPO_DIR}/dists/${SUITE}" && apt-ftparchive release .)"

    # Write the Release file with our custom header fields
    {
        cat <<EOF
Origin: debcraft-test
Label: debcraft-test
Suite: ${SUITE}
Codename: ${SUITE}
Date: ${RELEASE_DATE}
Architectures: ${ARCH_SPACE}
Components: ${COMPONENT}
Description: Debcraft test repository
EOF
        # Append checksums (MD5Sum, SHA1, SHA256 sections) from apt-ftparchive
        echo "$APT_RELEASE" | grep -A 9999 "^MD5Sum:" || true
    } > "${REPO_DIR}/dists/${SUITE}/Release"
fi

echo "Repository created at ${REPO_DIR}"
