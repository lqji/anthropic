# Shortcuts for the commands in README.md and AGENTS.md. Run `make` (or `make help`) to list them.
# Each target runs the documented command, so typing the command by hand still works and nothing
# needs make. Needs GNU make (3.81 or newer: macOS and Linux have it), uv, and Docker Compose v2.
# On Windows, use Git Bash or WSL with make installed.

.DEFAULT_GOAL := help

COMPOSE     ?= docker compose
DEV_COMPOSE := $(COMPOSE) -f compose.yaml -f compose.dev.yaml
BASE_URL    ?= http://localhost:8000

# The tests create and drop databases and empty tables on the server DATABASE_URL points to, so
# they use their own database, clinic_test, never the app's clinic database. `make db` creates it.
# If you changed POSTGRES_PASSWORD in .env, pass the matching URL: make test TEST_DATABASE_URL=...
TEST_DATABASE_URL ?= postgresql://clinic:change-me-local-only@localhost:5432/clinic_test

.PHONY: help install lint format format-check typecheck db test compose-check check \
	env up down ps logs smoke backup reset

##@ Develop (the same steps as the CI "quality" job)

help: ## List the targets
	@echo "Usage: make <target> [VAR=value]"
	@awk 'BEGIN {FS = ":.*## "} /^##@/ {printf "\n%s\n", substr($$0, 5)} /^[a-z-]+:.*## / {printf "  %-14s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install the locked dependencies (uv sync --locked)
	uv sync --locked

lint: ## ruff check
	uv run ruff check .

format: ## ruff format (rewrites files)
	uv run ruff format .

format-check: ## ruff format --check
	uv run ruff format --check .

typecheck: ## mypy (strict)
	uv run mypy

db: ## Start only PostgreSQL on 127.0.0.1:5432 for the tests, and create clinic_test
	$(DEV_COMPOSE) up -d --wait db
	$(DEV_COMPOSE) exec -T db sh -c 'psql -U "$$POSTGRES_USER" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '\''clinic_test'\''" | grep -q 1 || createdb -U "$$POSTGRES_USER" clinic_test'

test: ## pytest with coverage >= 80% on clinic_test (run `make db` first)
	@uv run python -c "import psycopg, sys; psycopg.connect(sys.argv[1], connect_timeout=3).close()" '$(TEST_DATABASE_URL)' 2>/dev/null \
		|| { echo "Cannot reach the test database (clinic_test on 127.0.0.1:5432): run make db first"; exit 1; }
	DATABASE_URL='$(TEST_DATABASE_URL)' LLM_PROVIDER=fake LANGGRAPH_STRICT_MSGPACK=true \
		uv run pytest --cov=clinic --cov-report=term-missing --cov-fail-under=80

compose-check: ## The compose file is valid with your .env
	@test -f .env || { echo "No .env yet: run make env"; exit 1; }
	$(COMPOSE) config -q

check: compose-check lint format-check typecheck test ## Everything CI checks (needs `make db`)

##@ Run and deploy (README: Run it, Deploy a version)

env: ## Create .env from .env.example (never overwrites an existing .env)
	@if [ -f .env ]; then echo ".env already exists: not changed"; \
	else cp .env.example .env && echo "Created .env: set OPENAI_API_KEY, or LLM_PROVIDER=fake"; fi

up: ## Build and start api + db, wait until healthy (APP_VERSION=<version> names the image)
	$(COMPOSE) up --build -d --wait

down: ## Stop the containers (the database is kept)
	$(COMPOSE) down

ps: ## Show the containers
	$(COMPOSE) ps

logs: ## Follow the api and db logs (Ctrl+C to stop)
	$(COMPOSE) logs -f --tail=100 api db

smoke: ## Run scripts/smoke_test.sh against BASE_URL (default http://localhost:8000)
	bash scripts/smoke_test.sh '$(BASE_URL)'

backup: ## pg_dump the database to backup-<date>-<time>.sql (git-ignored, holds patient data)
	@f=backup-$$(date +%F-%H%M%S).sql; tmp=$${f%.sql}.partial.sql; \
	if (umask 077; $(COMPOSE) exec -T db sh -c 'pg_dump -U "$$POSTGRES_USER" "$$POSTGRES_DB"' > $$tmp) && [ -s $$tmp ]; then \
		mv $$tmp $$f && echo "Wrote $$f"; \
	else rm -f $$tmp; echo "Backup FAILED: no file written"; exit 1; fi

reset: ## DELETE the database volume and start again with fresh demo data (needs CONFIRM=yes)
	@if [ "$(CONFIRM)" != "yes" ]; then \
		echo "This deletes all bookings, approvals and audit events. Run: make reset CONFIRM=yes"; \
		exit 1; \
	fi
	$(COMPOSE) down -v
	$(COMPOSE) up --build -d --wait
