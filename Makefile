.PHONY: data run quick check test lint format

data:
	uv run python scripts/generate_data.py

run: data
	uv run peak-capacity --out results 2>&1 | tee results/run_log.txt

quick: data
	uv run peak-capacity --quick --out results-ci

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

format:
	uv run ruff format .
	uv run ruff check --fix .

test:
	uv run pytest -q

check: lint test
