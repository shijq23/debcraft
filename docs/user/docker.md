# Docker Scanning

## Introduction

DebCraft can scan Docker image tarballs to identify installed Debian packages
and generate SBOMs. This is useful for auditing container images, verifying
supply chain integrity, and producing software inventories from exported Docker
images — all without requiring the Docker daemon or root privileges.

The Docker scanner reads tarballs in the `docker save` format, merges image
layers into a virtual filesystem, and extracts package metadata from the
embedded `var/lib/dpkg/status` file.

## Prerequisites

- **DebCraft** installed and available on your `PATH`
- **Docker** (only needed to export images; not required for scanning)
- No root privileges or Docker daemon access required for the scan itself

## CLI Invocation

### Basic scan of an existing tarball

```bash
debcraft sbom --type docker path/to/image.tar
```

### Export and scan a Docker image

The typical workflow is to export an image with `docker save` and then scan the
resulting tarball:

```bash
docker save myimage:latest -o myimage.tar
debcraft sbom --type docker myimage.tar
```

These two commands can be copied and executed directly — substitute `myimage:latest`
with your target image name.

### Scan with specific output format

```bash
debcraft sbom --type docker fixtures/images/test.tar --format spdx_2_3 --output-dir ./sbom-output
```

## How It Works

The Docker scanner processes image tarballs through four stages:

1. **Open tarball and read `manifest.json`** — The manifest lists image
   metadata and the ordered set of layer tarballs.

2. **Extract layers bottom-to-top** — Each layer is a tar archive containing
   filesystem entries. Layers are applied sequentially from the base layer
   (first in the manifest) to the topmost layer (last), building a virtual
   filesystem in memory.

3. **Apply whiteout semantics** — Docker uses special marker files to represent
   deletions between layers:

    | Marker | Effect |
    |--------|--------|
    | `.wh.<filename>` | Removes the named file from lower layers. For example, `.wh.config.json` in a layer deletes `config.json` inherited from any layer below. |
    | `.wh..wh..opq` | Opaque whiteout — removes **all** files in the containing directory that came from lower layers, while preserving files added in the same layer. |

   After whiteout processing, the virtual filesystem reflects the final merged
   state of all layers as they would appear in a running container.

4. **Parse `var/lib/dpkg/status`** — If the merged filesystem contains the dpkg
   status file, it is parsed to extract installed package metadata. If no dpkg
   status is found, the scanner falls back to filesystem analysis using file
   paths and a Contents index.

## Output Format

When scanning completes successfully, DebCraft identifies packages and produces
an SBOM. The scan result contains a table with the following columns for each
identified package:

| Column | Description |
|--------|-------------|
| Package Name | The Debian package name (e.g., `base-files`) |
| Version | The installed package version (e.g., `13.5`) |
| Architecture | Target architecture (e.g., `amd64`, `arm64`, `all`) |
| Installation Status | dpkg status value (e.g., `installed`, `config-files`) |

Example output from scanning the test fixture:

```
base-files  13.5  amd64  installed
```

## Error Handling and Diagnostics

DebCraft handles invalid or missing Docker tarballs gracefully — it reports a
diagnostic error message and produces an empty package list without crashing.

| Condition | Behavior |
|-----------|----------|
| Tarball path does not exist | Reports `Docker image tarball not found: <path>` and returns empty package list |
| Path is not a regular file | Reports `Path is not a file: <path>` and returns empty package list |
| File is not a valid tarball | Reports `Invalid Docker image tarball: <error>` and returns empty package list |
| Tarball missing `manifest.json` | Reports `Invalid Docker image: missing manifest.json` and returns empty package list |
| `manifest.json` is malformed | Reports `Invalid Docker image: cannot parse manifest.json: <error>` and returns empty package list |
| No layers in manifest | Reports `Invalid Docker image: no layers found in manifest` and returns empty package list |

In all error cases, DebCraft exits cleanly with diagnostic information and
never crashes or produces partial/corrupt output.

## Fixture Reference

For testing and development, the project includes a fixture script that
generates a minimal Docker image tarball without requiring Docker:

```bash
fixtures/build-docker.sh
```

This script creates `fixtures/images/test.tar` — a Docker-format tarball
containing:

- A `manifest.json` referencing two layers
- A base layer with `/etc/os-release` (Debian 12 bookworm)
- An application layer with `var/lib/dpkg/status` containing one synthetic
  package entry (`base-files 13.5 amd64`)
- A `repositories` file and `config.json`

The only tool required is `tar` (coreutils). Run the script to regenerate the
fixture at any time:

```bash
fixtures/build-docker.sh
# Output: Created fixtures/images/test.tar
```

Then scan it:

```bash
debcraft sbom --type docker fixtures/images/test.tar
```
