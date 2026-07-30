COMPOSE ?= docker compose
BASH ?= bash
UV ?= uv
UV_ENV_ARG = $(if $(wildcard .env),--env-file .env,)
UV_RUN = $(UV) run --frozen $(UV_ENV_ARG)

.PHONY: db-up db-down db-logs db-wait db-migrate db-test db-test-pit db-reset db-shell \
	migration-current migration-history migration-check python-lint python-format-check \
	python-test python-test-integration app-config-check app-db-health app-db-current check

db-up:
	$(COMPOSE) up -d postgres

db-down:
	$(COMPOSE) down --remove-orphans

db-logs:
	$(COMPOSE) logs --no-color --tail=100 --follow postgres

db-wait:
	$(BASH) scripts/db/wait.sh

db-migrate:
	$(BASH) scripts/db/migrate.sh

db-test:
	$(BASH) scripts/db/test.sh

db-test-pit: export BISFIN_RUN_DB_INTEGRATION := 1
db-test-pit: db-wait
	$(UV_RUN) pytest -m integration tests/test_temporal_overlap_concurrency.py

db-reset:
	$(BASH) scripts/db/reset.sh

db-shell:
	$(COMPOSE) exec postgres bash -Eeuo pipefail -c 'export PGPASSWORD="$$POSTGRES_PASSWORD"; exec psql -X --host=127.0.0.1 --port=5432 --username="$$POSTGRES_USER" --dbname="$$POSTGRES_DB"'

migration-current:
	$(UV_RUN) alembic current --verbose

migration-history:
	$(UV_RUN) alembic history --verbose

migration-check:
	$(UV_RUN) alembic current --check-heads
	$(UV_RUN) python -m scripts.db.check_migrations

python-lint:
	$(UV_RUN) ruff check .
	$(UV_RUN) mypy .

python-format-check:
	$(UV_RUN) ruff format --check src tests

python-test:
	$(UV_RUN) pytest -m "not integration"

python-test-integration: export BISFIN_RUN_DB_INTEGRATION := 1
python-test-integration: db-wait
	$(UV_RUN) pytest -m integration tests/integration

app-config-check:
	$(UV_RUN) bisfin config-check

app-db-health: db-wait
	$(UV_RUN) bisfin db-health

app-db-current: db-wait
	$(UV_RUN) bisfin db-current

check: python-lint python-format-check python-test migration-check db-test
