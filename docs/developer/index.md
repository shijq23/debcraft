# Developer Guide

This section covers everything you need to contribute to DebCraft.

## Contents

- [Getting Started](getting-started.md) — Environment setup and first steps

## Development Workflow

1. Create a feature branch from `main`
2. Make changes following the [coding standards](#coding-standards)
3. Run the full check suite locally before pushing
4. Submit a pull request with a clear description

## Coding Standards

DebCraft follows the [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html) enforced by:

- **Ruff** — Linting and formatting (line length 100, Google docstring convention)
- **BasedPyright** — Static type checking in standard mode
- **pre-commit** — Automatic checks on every commit

## Running Checks

```bash
# Format code
uv run ruff format src/ tests/

# Lint
uv run ruff check src/ tests/

# Type check
uv run ruff check src/ tests/
uv run basedpyright src/

# Unit tests
uv run pytest

# Architecture tests
uv run pytest -m architecture

# All tests with coverage
uv run pytest -m "unit or architecture" --cov=debcraft --cov-report=html
```

## Project Layout

```
src/debcraft/       # Source code (importable package)
tests/              # Test suite
  unit/             # Fast, isolated unit tests
  integration/      # Tests requiring external resources
  contract/         # API boundary tests
  architecture/     # Structural compliance tests
  e2e/              # End-to-end tests
  benchmark/        # Performance tests
  regression/       # Bug regression tests
docs/               # Documentation (MkDocs)
```
