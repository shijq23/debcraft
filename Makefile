.PHONY: test lint clean build mirror index docs

test:
	uv run pytest
	uv run pytest -m architecture

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
	uv run mypy
	uv run lint-imports

clean:
	rm -rf dist/
	rm -rf site/
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	rm -rf .ruff_cache/
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/
	rm -rf .coverage
	rm -rf htmlcov/

docs:
	uv run mkdocs build --strict
