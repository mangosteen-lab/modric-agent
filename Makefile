.PHONY: help sync run test lint lint-fix

help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "  sync      Install Modric Agent dependencies"
	@echo "  run       Run Modric Agent"
	@echo "  test      Run Modric Agent tests"
	@echo "  lint      Lint Modric Agent with ruff"
	@echo "  lint-fix  Auto-fix Soil lint issues"

sync:
	uv sync --extra dev

run: sync
	uv run python -m app.main

test: sync
	uv run pytest tests -v --tb=short

lint: sync
	uv run ruff check .

lint-fix: sync
	uv run ruff check --fix .
