COMPOSE ?= docker compose
BASH ?= bash

.PHONY: db-up db-down db-logs db-wait db-migrate db-test db-reset db-shell

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

db-reset:
	$(BASH) scripts/db/reset.sh

db-shell:
	$(COMPOSE) exec postgres bash -Eeuo pipefail -c 'export PGPASSWORD="$$POSTGRES_PASSWORD"; exec psql -X --host=127.0.0.1 --port=5432 --username="$$POSTGRES_USER" --dbname="$$POSTGRES_DB"'
