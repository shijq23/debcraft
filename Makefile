.PHONY: test lint clean build mirror index docs

test:
	uv run pytest
	uv run pytest -m architecture

mirror:
	uv run debcraft mirror

index:
	uv run debcraft index

build:
	uv run python scripts/clean.py dist
	uv build

lint:
	uv run ruff format --check .
	uv run ruff check --fix .
	uv run basedpyright
	uv run mypy
	uv run lint-imports
	uv run pylint src/
	uv run pre-commit run --all-files

clean:
	uv run python scripts/clean.py

docs:
	uv run mkdocs build --strict
