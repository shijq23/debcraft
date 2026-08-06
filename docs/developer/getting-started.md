# Getting Started

## Prerequisites

- **Python 3.13+** — Required by the project
- **uv** — Fast Python package manager ([install instructions](https://docs.astral.sh/uv/getting-started/installation/))

## Setup

```bash
# Clone the repository
git clone <repository-url>
cd debcraft

# Install all dependencies (including dev tools)
uv sync

# Verify the installation
uv run debcraft version
uv run debcraft doctor
```

## Running the CLI

```bash
# Show version
uv run debcraft version

# Check environment health
uv run debcraft doctor

# Display environment info
uv run debcraft info
```

## Running Tests

```bash
# Run unit tests (default)
uv run pytest

# Run architecture compliance tests
uv run pytest -m architecture

# Run with coverage report
uv run pytest --cov=debcraft --cov-report=html

# Run a specific test file
uv run pytest tests/unit/test_cli.py
```

## Running Linters

```bash
# Check formatting (no changes)
uv run ruff format --check src/ tests/

# Auto-format
uv run ruff format src/ tests/

# Lint check
uv run ruff check src/ tests/

# Lint with auto-fix
uv run ruff check --fix src/ tests/

# Type checking
uv run basedpyright src/
```

## Building Documentation

```bash
# Serve docs locally with live reload
uv run mkdocs serve

# Build static site
uv run mkdocs build
```

The built documentation will be in the `site/` directory.

## Pre-commit Hooks

Install the pre-commit hooks to automatically check code on every commit:

```bash
uv run pre-commit install
```

This runs Ruff formatting, Ruff linting, and BasedPyright on staged files before each commit.
