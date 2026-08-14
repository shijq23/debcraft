# QCOW2 Scanning

## Introduction

The QCOW2 scanner extracts installed Debian package metadata from QCOW2 virtual
machine disk images — the format used by QEMU/KVM, libvirt, and OpenStack. Use it
to generate SBOMs from VM disk snapshots, cloud images, or any `.qcow2` file
containing a Debian-based root filesystem.

The scanner inspects the image via libguestfs (no root privileges, no mount
operations), locates the dpkg status database inside the guest OS, and returns a
structured package list.

## Prerequisites

QCOW2 scanning requires the **libguestfs** Python bindings for partition inspection
and filesystem access within the disk image.

### Install libguestfs

On Debian/Ubuntu systems:

```bash
sudo apt install python3-guestfs
```

### Verify availability

Run `debcraft doctor` to confirm that the libguestfs dependency is detected:

```bash
debcraft doctor
```

If `python3-guestfs` is not installed, the doctor output will flag the missing
dependency.

## CLI Invocation

Scan a QCOW2 image using the `--type qcow2` option:

```bash
debcraft sbom --type qcow2 fixtures/images/test.qcow2
```

Expected output (using the test fixture):

```
Scanning QCOW2 image: fixtures/images/test.qcow2
Packages found: 1

Name          Version   Architecture
------------- --------- ------------
base-files    13.5      amd64
```

The `--type qcow2` flag tells debcraft to use the QCOW2 scanner directly. Without
it, auto-detection examines the file's magic bytes to determine the format.

## How It Works

The QCOW2 scanner follows this workflow:

1. **Validate format** — reads the first 4 bytes of the file and checks for the
   QFI magic header (`QFI\xfb`). If the magic is absent, the scan aborts with a
   diagnostic.

2. **Open image via guestfs** — uses the injected `GuestfsInspector` to open the
   QCOW2 image in read-only mode.

3. **Inspect OS roots** — calls `inspect_os()` to locate root filesystem partitions
   within the image.

4. **Mount and read dpkg status** — mounts the first discovered root partition
   read-only and reads `/var/lib/dpkg/status` to extract package metadata.

5. **Fallback to filesystem analysis** — if no dpkg status file is found on any
   partition, the scanner falls back to filesystem analysis using the Contents index
   to infer packages from file paths.

### Relationship to IMG Scanning

QCOW2 and IMG (raw disk image) scanning share the same underlying
`GuestfsInspector` interface. Both scanners use guestfs to inspect partitions,
mount filesystems, and read dpkg metadata. The difference is the container format:

- **QCOW2** wraps the disk data in a copy-on-write format with the `QFI\xfb` magic
  header at byte offset 0. It supports snapshots, compression, and sparse
  allocation.
- **IMG** is a raw byte-for-byte disk image with no wrapper or magic header.

The scanners are interchangeable at the guestfs layer — once the image is opened,
the inspection logic is identical.

## QCOW2 vs IMG: When to Use Which

| Criterion | Use `--type qcow2` | Use `--type img` |
|-----------|--------------------:|:-----------------|
| File has QFI magic header (`QFI\xfb` at offset 0) | ✓ | |
| File is a raw disk image (no format wrapper) | | ✓ |
| File extension is `.qcow2` | ✓ | |
| File extension is `.img` or `.raw` | | ✓ |
| Created by `qemu-img create -f qcow2` | ✓ | |
| Created by `dd` or `qemu-img create -f raw` | | ✓ |

**Rule of thumb:** if the first 4 bytes of the file are `QFI\xfb`, use QCOW2. If
the file has no recognizable magic header (raw bytes), use IMG.

You can check the format with:

```bash
file your-image.qcow2
# Output: QEMU QCOW2 Image (v3), ...

file your-image.img
# Output: Linux rev 1.0 ext4 filesystem data, ...
```

## Error Handling and Diagnostics

### libguestfs not installed

When `python3-guestfs` is not available, invoking `debcraft sbom` against a QCOW2
file produces **zero packages** and a diagnostic message:

```
guestfs library is not available: cannot inspect QCOW2 images.
Install python3-guestfs or libguestfs Python bindings.
```

The scanner does not crash — it returns an empty result with the diagnostic so you
can identify and resolve the missing dependency.

### Invalid QCOW2 format

If the file does not contain the expected QFI magic bytes at offset 0, the scanner
reports:

```
Invalid QCOW2 image at '<path>': missing QFI\xfb magic bytes at offset 0
```

This typically means the file is a raw IMG image, a different format entirely, or
corrupted. Try `--type img` if you suspect a raw disk image.

### File not readable

If the file path does not exist or is not readable, the scanner reports:

```
Cannot read QCOW2 image at '<path>': <OS error details>
```

## Fixture Reference

A minimal QCOW2 test image can be generated using the fixture script at
`fixtures/build-qcow2.sh`. This script converts the raw IMG fixture (`test.img`)
into QCOW2 format using `qemu-img convert`.

### Generate the test fixture

```bash
fixtures/build-qcow2.sh
```

This produces `fixtures/images/test.qcow2`. If `test.img` does not already exist,
the script automatically invokes `fixtures/build-img.sh` first.

### Requirements

The script requires `qemu-img` from the `qemu-utils` package:

```bash
sudo apt install qemu-utils
```

The upstream IMG fixture additionally requires `e2fsprogs` (for `mkfs.ext4` and
`debugfs`).
