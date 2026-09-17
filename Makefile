PYTHON ?= python3.13
VENV   := .venv
BIN    := $(VENV)/bin

.DEFAULT_GOAL := help
.PHONY: help venv install up down logs psql lint format typecheck test test-unit \
        test-integration check migrate revision downgrade alembic-verify clean

help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

venv: ## Create the virtualenv
	@command -v $(PYTHON) >/dev/null 2>&1 || { \
	  echo "ERROR: $(PYTHON) not found on PATH."; \
	  echo "  Project requires Python 3.13 (ADR-0001)."; \
	  echo "  Install with:  uv python install 3.13"; \
	  echo "  and ensure ~/.local/bin is on PATH, or run: make venv PYTHON=/path/to/python3.13"; \
	  exit 1; }
	$(PYTHON) -m venv $(VENV)

install: venv ## Install the project with dev extras
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -e ".[dev]"

up: ## Start PostgreSQL and wait until healthy
	docker compose up -d --wait

down: ## Stop PostgreSQL (data volume is preserved)
	docker compose down

logs: ## Tail PostgreSQL logs
	docker compose logs -f postgres

psql: ## Open a psql shell
	docker compose exec postgres psql -U jobradar -d jobradar

lint: ## Run Ruff (lint + format check)
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .

format: ## Apply Ruff formatting and fixes
	$(BIN)/ruff check --fix .
	$(BIN)/ruff format .

typecheck: ## Run mypy in strict mode
	$(BIN)/mypy

test-unit: ## Run tests that need no database
	$(BIN)/pytest -m "not integration"

test-integration: ## Run tests that need PostgreSQL
	$(BIN)/pytest -m integration

test: ## Run the full suite
	$(BIN)/pytest

check: lint typecheck test ## Run every gate

migrate: ## Apply migrations
	$(BIN)/alembic upgrade head

revision: ## Autogenerate a migration: make revision m="message"
	$(BIN)/alembic revision --autogenerate -m "$(m)"

downgrade: ## Revert the most recent migration
	$(BIN)/alembic downgrade -1

alembic-verify: ## Confirm Alembic is wired up correctly
	$(BIN)/alembic heads
	$(BIN)/alembic current

clean: ## Remove caches and build artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache build dist
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
