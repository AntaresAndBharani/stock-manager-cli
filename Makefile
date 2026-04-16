.PHONY: help install test lint format clean check setup

help:
	@echo "Available commands:"
	@echo "  install    Install dependencies and project in dev mode"
	@echo "  test       Run tests with coverage"
	@echo "  lint       Run Ruff and Mypy checks"
	@echo "  format     Auto-format code with Ruff"
	@echo "  check      Run format, lint, and test"
	@echo "  setup      Install pre-commit hooks"
	@echo "  clean      Remove cache files"

install:
	pip install -e ".[dev]"

setup: install
	pre-commit install

test:
	pytest

lint:
	ruff check .
	mypy src tests

format:
	ruff format .
	ruff check --fix .

check: format lint test

clean:
	rm -rf .pytest_cache .coverage .mypy_cache .ruff_cache dist build *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
