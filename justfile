# Default fallback shell for Linux/macOS
set shell := ["sh", "-cu"]

# Windows-specific override (automatically ignored on Linux/macOS)
set windows-shell := ["powershell.exe", "-NoProfile", "-Command"]

# Run tests
test:
    uv run pytest
    uv run pytest -m architecture

# Run the mirror command
mirror:
    uv run debcraft mirror

# Run the index command
index:
    uv run debcraft index

# Build the package
build:
    uv run python scripts/clean.py dist
    uv build

# Run linters (format check, ruff, pyright)
lint:
    uv run ruff format --check .
    uv run ruff check --fix .
    uv run basedpyright
    uv run mypy
    uv run lint-imports
    uv run pylint src/
    uv run pre-commit run --all-files

# Clean build artifacts and caches (cross-platform)
clean:
    uv run python scripts/clean.py

# Generate documents site
docs:
	uv run mkdocs build --strict
