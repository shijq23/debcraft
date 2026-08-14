# IMG Scanning

## Introduction

Raw disk images (`.img` files) are used for embedded Linux devices, Raspberry Pi
images, virtual machines, and cloud appliance snapshots. DebCraft can scan these
images to generate an SBOM by inspecting the partitions within the disk image and
extracting installed Debian package metadata — all without requiring root privileges
or mount operations.

Use IMG scanning when you have a raw disk image containing a Debian-based filesystem
and need to inventory the installed packages for compliance, vulnerability tracking,
or audit purposes.

## Prerequisites

IMG scanning relies on `python3-guestfs` (the libguestfs Python bindings) to
inspect partitions and read files from the disk image without mounting.

### Required Packages

| Package | Purpose |
|---------|---------|
| `python3-guestfs` | Libguestfs Python bindings for partition inspection |
| `libguestfs-tools` | Underlying guest filesystem access library |

### Installation

Install the required packages on Debian/Ubuntu:

```bash
sudo apt install python3-guestfs libguestfs-tools
```

### Verifying Dependencies

Run `debcraft doctor` to confirm that all required dependencies are available:

```bash
$ debcraft doctor
Python version >= 3.13 ... PASS
Writable temp directory ... PASS
Writable current directory ... PASS
```

If `python3-guestfs` is not installed, `debcraft doctor` will report the missing
dependency and provide installation instructions.

## CLI Invocation Examples

### Basic IMG scan

Scan a raw disk image and generate an SBOM:

```bash
$ debcraft sbom --type img fixtures/images/test.img
Scanning fixtures/images/test.img...
Inspecting partitions via guestfs...

Package          Version   Architecture   Status
base-files       13.5      amd64          install ok installed

1 package(s) identified.
```

### Scan with JSON output format

Generate the SBOM in JSON format for machine consumption:

```bash
$ debcraft sbom --type img --format json fixtures/images/test.img
{
  "packages": [
    {
      "name": "base-files",
      "version": "13.5",
      "architecture": "amd64",
      "status": "install ok installed"
    }
  ],
  "strategy": "dpkg_metadata",
  "artifact_path": "fixtures/images/test.img"
}
```

## How It Works

The IMG scanner uses a two-stage approach to extract package information from raw
disk images.

### Stage 1 — Partition Inspection via Guestfs

The scanner uses libguestfs to open the disk image and enumerate partitions via
`inspect_os()`. For each partition found (in table order), it:

1. Mounts the partition read-only
2. Checks for the presence of `/var/lib/dpkg/status`
3. If found, parses the dpkg status file to extract package metadata
4. Stops at the first partition where dpkg metadata is located

This approach works without root privileges because libguestfs uses a lightweight
QEMU appliance to access the disk image contents.

### Stage 2 — Filesystem Analysis Fallback

When no partition contains a `/var/lib/dpkg/status` file, the scanner falls back to
filesystem analysis:

1. Collects file paths from the mounted filesystem using guestfs
2. Queries a Contents index to map file paths to package names
3. Resolves each matched package name to its metadata (version, architecture)

**Limitations of the filesystem fallback:**

- Infers packages from file paths rather than reading dpkg metadata directly
- May produce incomplete results if the Contents index does not cover all file paths
- Cannot determine exact installation status (only that a package's files are present)
- Relies on an available Contents index for the target distribution

## Error Handling and Diagnostics

### Guestfs Unavailable

When `python3-guestfs` is not installed, the scanner cannot inspect the disk image.
In this case:

- The scanner returns **zero packages**
- A diagnostic message is emitted: `"guestfs library is not available: cannot inspect raw disk image without libguestfs"`
- No error or crash occurs — the scan completes gracefully

Install `python3-guestfs` to resolve this (see [Prerequisites](#prerequisites)).

### Image Not Accessible

If the specified image file does not exist or is not readable, the scanner reports:

```
Failed to open disk image at '<path>': <error details>
```

### No Partitions Found

If the image does not contain a recognized partition table or filesystem:

```
No OS partitions found in disk image: unrecognized partition table or filesystem
```

This can happen with empty images, images using unsupported partition schemes, or
corrupted files.

## Fixture Reference

The repository includes a fixture script for generating a minimal test IMG file:

**Script:** `fixtures/build-img.sh`

**Description:** Generates a 4 MB raw ext4 disk image containing a synthetic
`var/lib/dpkg/status` file with one package entry (`base-files 13.5 amd64`). The
script uses `debugfs` to write files into the image without requiring root privileges
or mount operations, making it portable for CI environments.

**Execute the fixture script:**

```bash
fixtures/build-img.sh
```

This produces `fixtures/images/test.img`. The script requires `dd` (coreutils),
`mkfs.ext4`, and `debugfs` (both from the `e2fsprogs` package):

```bash
sudo apt install e2fsprogs
```
