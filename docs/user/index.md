# User Guide

DebCraft provides a command-line interface for working with Debian-based software artifacts.

## Installation

```bash
# Install with uv
uv sync

# Or install with pip
pip install debcraft
```

## Commands

### `debcraft version`

Display the current DebCraft version.

```bash
$ debcraft version
DebCraft 0.1.0
```

### `debcraft doctor`

Check environment health and report the status of required dependencies and permissions.

```bash
$ debcraft doctor
Python version >= 3.13 ... PASS
Writable temp directory ... PASS
Writable current directory ... PASS
```

### `debcraft info`

Display detailed configuration and environment information including Python version, platform details, and installation paths.

```bash
$ debcraft info
DebCraft 0.1.0
Python: 3.13.0 (/path/to/python)
Platform: Linux x86_64
Package: /path/to/debcraft
VEnv: /path/to/.venv
```

## Troubleshooting

See [Troubleshooting](troubleshooting.md) for diagnostic commands and
common issues with the mirror and indexer.

## Getting Help

Every command supports `--help` for detailed usage information:

```bash
debcraft --help
debcraft doctor --help
```
