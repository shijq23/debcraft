.PHONY: test lint clean build mirror index

test:
	uv run pytest

mirror:
	uv run debcraft mirror

index:
	uv run debcraft index

build:
	rm -rf dist/
	uv build

lint:
	uv run ruff format --check .
	uv run ruff check --fix .
	uv run basedpyright

clean:
	rm -rf dist/
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	rm -rf .ruff_cache/
	rm -rf .pytest_cache/
	rm -rf .coverage
	rm -rf htmlcov/
