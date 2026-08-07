# Test Fixtures

Scripts for creating minimal Debian packages and APT repositories used to test debcraft's repository parsing, package analysis, and mirror synchronization.

## Prerequisites

- **dpkg-dev** — provides `dpkg-buildpackage` and `dpkg-scanpackages`
- **apt-utils** — provides `apt-ftparchive`
- **gzip** — for compressing Packages indexes

Install on Debian/Ubuntu:

```bash
sudo apt-get install dpkg-dev apt-utils
```

## Quick Start

Generate all test packages and repositories:

```bash
cd fixtures/
make all
```

## create-package.sh

Generates a minimal Debian package skeleton and optionally builds a .deb file.

```
Usage: ./create-package.sh [OPTIONS] PACKAGE_NAME

Options:
  -v, --version VERSION      Package version (default: 1.0-1)
  -a, --arch ARCHITECTURE    Architecture (default: all)
  -d, --depends DEPENDS      Comma-separated dependencies
  -D, --description TEXT     Package description
  -o, --output-dir DIR       Output directory (default: fixtures/packages/)
  -b, --build                Also build the .deb file
  -h, --help                 Show usage
```

### Examples

Create a package skeleton:

```bash
./create-package.sh hello
```

Create and build a .deb:

```bash
./create-package.sh --build hello
```

Create a package with custom version, architecture, and dependencies:

```bash
./create-package.sh --build -v 2.0-1 -a amd64 -d "libc6" libfoo
```

Create a package that depends on another:

```bash
./create-package.sh --build -v 1.0-1 -d "hello" depends-on-hello
```

## create-repo.sh

Creates an APT repository structure and generates metadata from pool contents.

```
Usage: ./create-repo.sh [OPTIONS] REPO_NAME

Options:
  -s, --suite SUITE          Suite name (default: stable)
  -a, --arch ARCHITECTURES   Comma-separated architectures (default: amd64)
  -c, --component COMPONENT  Component name (default: main)
  -o, --output-dir DIR       Output directory (default: fixtures/repositories/)
  -p, --add-package DEB      Add a .deb to the pool (repeatable)
  -m, --generate-metadata    Generate Packages/Release indexes
  -h, --help                 Show usage
```

### Examples

Create an empty repository structure:

```bash
./create-repo.sh test-repo
```

Create a repository with packages and metadata:

```bash
./create-repo.sh -a amd64,arm64 \
    -p packages/hello_1.0-1_all.deb \
    -p packages/libfoo_2.0-1_amd64.deb \
    -m test-repo
```

## Directory Layout

After running `make all`, the expected structure is:

```
fixtures/
├── create-package.sh
├── create-repo.sh
├── Makefile
├── README.md
├── packages/
│   ├── hello-1.0-1/
│   │   └── debian/
│   │       ├── changelog
│   │       ├── compat
│   │       ├── control
│   │       └── rules
│   ├── hello_1.0-1_all.deb
│   ├── libfoo-2.0-1/
│   │   └── debian/
│   │       └── ...
│   └── libfoo_2.0-1_amd64.deb
└── repositories/
    └── test-repo/
        ├── pool/
        │   └── main/
        │       ├── h/
        │       │   └── hello/
        │       │       └── hello_1.0-1_all.deb
        │       └── libf/
        │           └── libfoo/
        │               └── libfoo_2.0-1_amd64.deb
        └── dists/
            └── stable/
                ├── Release
                └── main/
                    ├── binary-amd64/
                    │   ├── Packages
                    │   └── Packages.gz
                    └── binary-arm64/
                        ├── Packages
                        └── Packages.gz
```

Pool naming follows Debian conventions: regular packages use the first letter as prefix (`h/hello/`), while `lib*` packages use the first four characters (`libf/libfoo/`).

## Serving Repositories Locally

You can serve the generated APT repositories over HTTP using Python's built-in web server. This is useful for testing debcraft against a real HTTP-based apt source.

Start the server from the `repositories/` directory:

```bash
cd fixtures/repositories
python3 -m http.server 8080
```

Then configure apt to use it by adding a source entry:

```bash
echo "deb [trusted=yes] http://localhost:8080/test-repo stable main" | \
    sudo tee /etc/apt/sources.list.d/debcraft-test.list
sudo apt-get update
```

The `[trusted=yes]` option skips GPG signature verification, which is appropriate for local testing.

To stop the server, press `Ctrl+C` in the terminal where it's running.

## Makefile Targets

| Target | Description |
|--------|-------------|
| `all` | Build all packages and assemble all repositories |
| `packages` | Build all test .deb packages |
| `repos` | Assemble repositories from built packages |
| `clean` | Remove all generated packages and repositories |
