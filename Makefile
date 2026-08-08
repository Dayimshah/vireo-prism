# ===========================================================================
# Prism — Vireo Product Analytics Platform
# ---------------------------------------------------------------------------
# Every routine operation has exactly one documented entrypoint here, so the
# README never has to explain a nine-flag docker command.
#
#   make help          list every target
#   make up            start the stack
#   make seed          generate the synthetic dataset
#   make check         lint + types + tests (what CI runs)
# ===========================================================================

SHELL := /bin/sh
.DEFAULT_GOAL := help

COMPOSE     := docker compose
BACKEND_DIR := backend
FRONTEND_DIR:= frontend
API_SVC     := api
DB_SVC      := postgres

# Exec into the running api container; fall back to a throwaway one if the
# stack is down, so targets work either way.
DC_RUN := $(COMPOSE) run --rm --no-deps $(API_SVC)
DC_EXEC:= $(COMPOSE) exec -T $(API_SVC)

.PHONY: help
help: ## Show this help
	@printf '\n\033[1mPrism — available targets\033[0m\n\n'
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'
	@printf '\n'

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

.PHONY: env
env: ## Create .env from .env.example if absent
	@if [ -f .env ]; then \
		echo ".env already exists, leaving it alone"; \
	else \
		cp .env.example .env && echo "created .env — review it before deploying anywhere real"; \
	fi

# ---------------------------------------------------------------------------
# Stack lifecycle
# ---------------------------------------------------------------------------

.PHONY: build
build: ## Build the api and web images
	$(COMPOSE) build

.PHONY: up
up: env ## Start the full stack in the background (applies migrations)
	$(COMPOSE) up -d --build
	@printf '\n  API   http://localhost:$${API_HOST_PORT:-8000}/docs\n'
	@printf '  Web   http://localhost:$${WEB_HOST_PORT:-5173}\n\n'
	@printf '  Next: make seed\n\n'

.PHONY: down
down: ## Stop the stack, keep the database volume
	$(COMPOSE) down

.PHONY: nuke
nuke: ## Stop the stack and DELETE the database volume (irreversible)
	@printf 'This deletes the vireo-prism-pgdata volume and all generated data.\n'
	@printf 'Type "yes" to continue: ' && read ans && [ "$$ans" = "yes" ]
	$(COMPOSE) down -v --remove-orphans

.PHONY: restart
restart: ## Restart the api service only
	$(COMPOSE) restart $(API_SVC)

.PHONY: ps
ps: ## Show service status
	$(COMPOSE) ps

.PHONY: logs
logs: ## Tail logs from every service
	$(COMPOSE) logs -f --tail=120

.PHONY: logs-api
logs-api: ## Tail api logs
	$(COMPOSE) logs -f --tail=120 $(API_SVC)

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

.PHONY: migrate
migrate: ## Apply all migrations (alembic upgrade head)
	$(DC_RUN) alembic upgrade head

.PHONY: downgrade
downgrade: ## Roll back one migration
	$(DC_RUN) alembic downgrade -1

.PHONY: revision
revision: ## Autogenerate a migration: make revision m="add x"
	@test -n "$(m)" || (echo 'usage: make revision m="short message"' && exit 1)
	$(DC_RUN) alembic revision --autogenerate -m "$(m)"

.PHONY: history
history: ## Show migration history and current head
	$(DC_RUN) alembic history --indicate-current

.PHONY: psql
psql: ## Open an interactive psql shell
	$(COMPOSE) exec $(DB_SVC) psql -U $${POSTGRES_USER:-prism} -d $${POSTGRES_DB:-vireo}

.PHONY: refresh
refresh: ## Refresh the analytics materialized views
	$(COMPOSE) exec $(DB_SVC) psql -U $${POSTGRES_USER:-prism} -d $${POSTGRES_DB:-vireo} \
		-c "SELECT analytics.refresh_all(concurrent => true);"

.PHONY: tables
tables: ## Print row counts for every core table
	$(COMPOSE) exec $(DB_SVC) psql -U $${POSTGRES_USER:-prism} -d $${POSTGRES_DB:-vireo} \
		-c "SELECT relname AS table, n_live_tup AS approx_rows FROM pg_stat_user_tables \
		    WHERE schemaname='core' ORDER BY n_live_tup DESC;"

# Builds the optional `powerbi` schema: star-schema views over core/analytics for
# Power BI. Additive and disposable — nothing in the API or dashboard reads it, and
# `DROP SCHEMA powerbi CASCADE` removes it. The script drops and recreates the
# schema, so this is safe to re-run after a reseed.
.PHONY: powerbi
powerbi: ## Build the Power BI star-schema views (optional)
	$(COMPOSE) exec -T $(DB_SVC) psql -U $${POSTGRES_USER:-prism} -d $${POSTGRES_DB:-vireo} \
		-v ON_ERROR_STOP=1 -q < powerbi/01_star_schema.sql
	@printf '\n  powerbi schema built. Connect Power BI per docs/powerbi.md\n\n'

# ---------------------------------------------------------------------------
# Synthetic data
# ---------------------------------------------------------------------------

.PHONY: seed
seed: ## Generate the dataset (profile from .env, default medium)
	$(COMPOSE) --profile seed run --rm seeder

.PHONY: seed-small
seed-small: ## Generate the small dataset (600 users, ~1.1M events, ~2.5 min)
	$(COMPOSE) --profile seed run --rm seeder --profile small --truncate

.PHONY: seed-large
seed-large: ## Generate the large dataset (15k users, ~28M events, ~50 min)
	$(COMPOSE) --profile seed run --rm seeder --profile large --truncate

.PHONY: report
report: ## Write docs/data_quality_report.html from the seeded data
	$(COMPOSE) --profile seed run --rm --entrypoint python seeder -m seeder.report

# ---------------------------------------------------------------------------
# Quality gates
# ---------------------------------------------------------------------------

.PHONY: lint
lint: ## Lint the backend (ruff)
	$(DC_RUN) ruff check .

.PHONY: fmt
fmt: ## Format the backend (ruff format)
	$(DC_RUN) ruff format .
	$(DC_RUN) ruff check --fix .

.PHONY: types
types: ## Type-check the backend (mypy --strict)
	$(DC_RUN) mypy

.PHONY: test
test: ## Run the backend test suite
	$(DC_RUN) pytest

.PHONY: test-cov
test-cov: ## Run tests with a coverage report
	$(DC_RUN) sh -c "coverage run -m pytest && coverage report -m"

.PHONY: check
check: lint types test ## Everything CI runs

# ---------------------------------------------------------------------------
# Frontend (host-side, for hot reload)
# ---------------------------------------------------------------------------

.PHONY: web-install
web-install: ## Install frontend dependencies
	cd $(FRONTEND_DIR) && npm ci

.PHONY: web-dev
web-dev: ## Run the Vite dev server with hot reload
	cd $(FRONTEND_DIR) && npm run dev

.PHONY: web-build
web-build: ## Type-check and build the production bundle
	cd $(FRONTEND_DIR) && npm run build

.PHONY: web-lint
web-lint: ## Lint the frontend
	cd $(FRONTEND_DIR) && npm run lint

# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------

.PHONY: shell
shell: ## Open a shell in the api container
	$(COMPOSE) exec $(API_SVC) sh

.PHONY: fresh
fresh: nuke up seed ## Nuke, rebuild and reseed from scratch
	@echo "fresh stack ready"
