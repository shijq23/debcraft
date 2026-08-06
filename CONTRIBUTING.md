# Contributing to DebCraft

Thank you for your interest in contributing to DebCraft. This guide covers the development workflow, coding standards, and contribution process.

## Development Setup

DebCraft uses [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
# Clone the repository
git clone <repository-url>
cd debcraft

# Install dependencies (creates virtualenv automatically)
uv sync

# Verify the setup
uv run debcraft version
uv run debcraft doctor
```

## Coding Standards

This project follows the [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html).

Key conventions:

- Python 3.13+ features are encouraged
- Line length limit: 120 characters
- Use `pathlib.Path` for all path operations
- Type annotations on all public functions
- Google-style docstrings on all public modules, classes, and functions

## Testing

Tests are organized by type and use pytest markers for selective execution.

### Running Tests

```bash
# Run unit tests (default)
uv run pytest

# Run architecture compliance tests
uv run pytest -m architecture

# Run all tests
uv run pytest -m "unit or architecture or integration"

# Run with coverage
uv run pytest --cov=debcraft --cov-report=html

# Run a specific test file
uv run pytest tests/unit/test_cli.py
```

### Pytest Markers

- `unit` — Fast, isolated unit tests (run by default)
- `integration` — Tests requiring external resources
- `architecture` — Architecture compliance checks
- `contract` — API boundary verification
- `benchmark` — Performance tests
- `slow` — Long-running tests (excluded from default runs)

### Writing Tests

- Place unit tests in `tests/unit/`
- Mark every test function with the appropriate marker
- Use fixtures from `tests/conftest.py` for shared setup

## Code Quality Tools

All code must pass these checks before merging:

```bash
# Format code
uv run ruff format src/ tests/

# Lint code
uv run ruff check src/ tests/

# Lint and auto-fix
uv run ruff check --fix src/ tests/

# Type checking
uv run basedpyright src/
```

## Pull Request Guidelines

1. Create a feature branch from `main`
2. Make focused, single-purpose commits
3. Ensure all checks pass locally before pushing:
   - `uv run ruff format --check src/ tests/`
   - `uv run ruff check src/ tests/`
   - `uv run basedpyright src/`
   - `uv run pytest`
4. Write a clear PR description explaining the change
5. Reference any related issues in the PR description
6. Keep PRs small and reviewable — prefer multiple small PRs over one large one
