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
    rm -rf dist/
    uv build

# Run linters (format check, ruff, pyright)
lint:
    uv run ruff format --check .
    uv run ruff check --fix .
    uv run basedpyright
    uv run mypy
    uv run lint-imports

# Clean build artifacts and caches
clean:
    rm -rf dist/
    find . -type d -name "__pycache__" -exec rm -rf {} +
    find . -type d -name "*.egg-info" -exec rm -rf {} +
    rm -rf .ruff_cache/
    rm -rf .pytest_cache/
    rm -rf .mypy_cache/
    rm -rf .coverage
    rm -rf htmlcov/
