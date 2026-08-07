#!/usr/bin/env bash
set -euo pipefail

# Default values
VERSION="1.0-1"
ARCH="all"
DEPENDS=""
DESCRIPTION="Test package"
OUTPUT_DIR="fixtures/packages/"
BUILD=false

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS] PACKAGE_NAME

Generate a minimal Debian package skeleton and optionally build it.

Options:
  -v, --version VERSION      Package version (default: 1.0-1)
  -a, --arch ARCHITECTURE    Architecture (default: all)
  -d, --depends DEPENDS      Comma-separated dependencies
  -D, --description TEXT     Package description
  -o, --output-dir DIR       Output directory (default: fixtures/packages/)
  -b, --build                Also build the .deb file
  -h, --help                 Show usage
EOF
}

die() {
    echo "Error: $1" >&2
    exit 1
}

validate_package_name() {
    local name="$1"
    if [[ ! "$name" =~ ^[a-z0-9][a-z0-9.+\-]+$ ]]; then
        die "Invalid package name '${name}'. Must match [a-z0-9][a-z0-9.+\\-]+"
    fi
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        -v|--version)
            VERSION="$2"
            shift 2
            ;;
        -a|--arch)
            ARCH="$2"
            shift 2
            ;;
        -d|--depends)
            DEPENDS="$2"
            shift 2
            ;;
        -D|--description)
            DESCRIPTION="$2"
            shift 2
            ;;
        -o|--output-dir)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        -b|--build)
            BUILD=true
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

# Check for package name
if [[ $# -lt 1 ]]; then
    usage >&2
    exit 1
fi

PACKAGE_NAME="$1"

# Validate package name
validate_package_name "$PACKAGE_NAME"

# Check output directory is writable
if [[ -e "$OUTPUT_DIR" && ! -w "$OUTPUT_DIR" ]]; then
    die "Cannot write to ${OUTPUT_DIR}"
fi

# Create package directory
PKG_DIR="${OUTPUT_DIR%/}/${PACKAGE_NAME}-${VERSION}/debian"
mkdir -p "$PKG_DIR"

# Generate debian/control
cat > "${PKG_DIR}/control" <<EOF
Source: ${PACKAGE_NAME}
Section: misc
Priority: optional
Maintainer: Test <test@example.com>
Build-Depends: debhelper (>= 7)
Standards-Version: 3.9.8

Package: ${PACKAGE_NAME}
Architecture: ${ARCH}
Depends: ${DEPENDS}
Description: ${DESCRIPTION}
EOF

# Generate debian/changelog
cat > "${PKG_DIR}/changelog" <<EOF
${PACKAGE_NAME} (${VERSION}) unstable; urgency=low

  * Test package

 -- Test <test@example.com>  Mon, 01 Jan 2024 00:00:00 +0000
EOF

# Generate debian/rules (note: the recipe line MUST use a real tab)
cat > "${PKG_DIR}/rules" <<'RULES'
#!/usr/bin/make -f
%:
	dh $@
RULES
chmod +x "${PKG_DIR}/rules"

# Generate debian/copyright
cat > "${PKG_DIR}/copyright" <<EOF
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: ${PACKAGE_NAME}

Files: *
Copyright: 2024 Test <test@example.com>
License: MIT

License: MIT
 Permission is hereby granted, free of charge, to any person obtaining a copy
 of this software and associated documentation files (the "Software"), to deal
 in the Software without restriction, including without limitation the rights
 to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 copies of the Software, and to permit persons to whom the Software is
 furnished to do so, subject to the following conditions:
 .
 The above copyright notice and this permission notice shall be included in all
 copies or substantial portions of the Software.
 .
 THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 SOFTWARE.
EOF

# Generate debian/compat
echo "7" > "${PKG_DIR}/compat"

echo "Package skeleton created at ${PKG_DIR%/debian}"

# Build if requested
if [[ "$BUILD" == true ]]; then
    if ! command -v dpkg-buildpackage &>/dev/null; then
        die "dpkg-buildpackage not found. Install dpkg-dev."
    fi

    cd "${PKG_DIR%/debian}"
    dpkg-buildpackage -us -uc -b
fi
