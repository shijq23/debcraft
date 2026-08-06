# DebCraft

**Artifact Intelligence Platform for Debian-based ecosystems.**

DebCraft analyzes, transforms, and manages software artifacts across the Debian packaging lifecycle. It provides tooling for repository management, package analysis, license detection, SBOM generation, and image operations.

## Key Capabilities

- **Repository Operations** — Mirror, inspect, and manage Debian repositories
- **Package Analysis** — Deep inspection of `.deb` packages and their metadata
- **License Intelligence** — DEP-5 parsing, SPDX normalization, and compliance checks
- **SBOM Generation** — CycloneDX and SPDX document output
- **Image Operations** — Docker, OCI, ISO, QCOW2, and AMI artifact handling
- **Plugin Architecture** — Extensible platform with SDK for custom integrations

## Quick Start

```bash
# Install with uv
uv sync

# Verify installation
uv run debcraft version

# Check environment health
uv run debcraft doctor
```

## Project Structure

```
src/debcraft/
├── cli/            # Command-line interface (Typer + Rich)
├── domain/         # Core business logic (no external dependencies)
├── infrastructure/ # External integrations (storage, network)
├── platform/       # Platform internals
│   ├── contracts/  # Interface definitions
│   ├── kernel/     # Core platform services
│   └── sdk/        # Plugin development SDK
└── plugins/        # Plugin implementations
```

## Documentation Sections

| Section | Description |
|---------|-------------|
| [Architecture](architecture/index.md) | System design, layer boundaries, dependency rules |
| [Specifications](specifications/index.md) | Feature specifications and milestone plans |
| [ADR](adr/index.md) | Architecture Decision Records |
| [Developer Guide](developer/index.md) | Setup, contributing, testing |
| [User Guide](user/index.md) | CLI usage and workflows |
