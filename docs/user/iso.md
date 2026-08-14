# ISO Scanning

## Introduction

DebCraft can scan ISO 9660 images — the format used by Debian/Ubuntu installers
and live media — to produce a Software Bill of Materials (SBOM). This is useful
for auditing installer discs, live images, or any ISO that embeds a Debian
rootfs or squashfs filesystem.

The scanner reads the ISO filesystem directly without mounting it, so no root
privileges or special kernel modules are required.

## Prerequisites

- **No root privileges required** — the scanner reads the ISO as a regular file.
- **No mount operations needed** — DebCraft uses `pycdlib` to traverse the ISO
  filesystem in userspace.
- **Python 3.13+** with DebCraft installed (see [Installation](index.md#installation)).

## CLI Invocation

Scan an ISO image by passing its path to `debcraft sbom`:

```bash
$ debcraft sbom fixtures/images/test.iso
```

### Expected Output

```
Scanning: fixtures/images/test.iso
Strategy: direct rootfs (var/lib/dpkg/status found in ISO root)

╭─────────────────── SBOM Generation Summary ───────────────────╮
│ Format       Output File          Size     SHA-256             │
│ spdx_2_3    sbom.spdx.json       1.0 KiB  a1b2c3d4...        │
│ spdx_3_0    sbom.spdx3.json      512 B    e5f6a7b8...        │
│ cyclonedx    sbom.cdx.json        768 B    c9d0e1f2...        │
╰───────────────────────────────────────────────────────────────╯
```

### Options

| Option | Description |
|--------|-------------|
| `--format` | Output format(s): `spdx_2_3`, `spdx_3_0`, `cyclonedx`. Defaults to all. |
| `--output-dir` | Directory for generated SBOM files. Created automatically if needed. |
| `--quiet` | Suppress progress output; only show the summary table. |

Generate a single format:

```bash
$ debcraft sbom fixtures/images/test.iso --format spdx_2_3
```

Write output to a specific directory:

```bash
$ debcraft sbom fixtures/images/test.iso --output-dir ./results
```

## How It Works

The ISO scanner uses a three-stage fallback strategy to locate Debian package
metadata inside the image:

1. **Squashfs search** — looks for a squashfs filesystem at well-known paths
   (`live/filesystem.squashfs`, `casper/filesystem.squashfs`). If found, it
   extracts and parses `var/lib/dpkg/status` from within the squashfs.

2. **Direct rootfs** — checks for `var/lib/dpkg/status` at the ISO root level.
   This is the path exercised by the test fixture.

3. **Filesystem analysis** — collects all file paths from the ISO, queries a
   Contents index to map them to package names, and resolves package metadata.

The scanner stops at the first strategy that succeeds.

## Error Handling and Diagnostics

### File does not exist

If the ISO path does not exist, DebCraft exits immediately with a non-zero
exit code and a clear error message:

```bash
$ debcraft sbom /path/to/nonexistent.iso
Error: Path '/path/to/nonexistent.iso' does not exist.
```

### File is not a valid ISO

If the file exists but is not a valid ISO 9660 image, the scanner reports a
diagnostic error describing the issue (e.g., missing ISO magic bytes) and
produces an empty package list.

### Getting help

Use `--help` for full usage details:

```bash
$ debcraft sbom --help
```

## Generating the Test Fixture

The repository includes a fixture script that creates a minimal ISO for testing.
The generated ISO contains a single synthetic package entry (`base-files 13.5 amd64`).

```bash
fixtures/build-iso.sh
```

This produces `fixtures/images/test.iso`. The script requires `genisoimage`:

```bash
# Install on Debian/Ubuntu
apt install genisoimage
```

The fixture script is idempotent — running it multiple times produces identical
output. See `fixtures/build-iso.sh` in the repository root for the full
implementation.
