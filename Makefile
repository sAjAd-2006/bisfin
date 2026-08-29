COMPOSE ?= docker compose
BASH ?= bash
UV ?= uv
UV_ENV_ARG = $(if $(wildcard .env),--env-file .env,)
UV_RUN = $(UV) run --frozen $(UV_ENV_ARG)
BRSAPI_SYMBOL ?= فملی
BRSAPI_FIXTURE ?= tests/fixtures/brsapi/candlestick_type2_success.json
BRSAPI_OUTPUT_FORMAT ?= human

.PHONY: db-up db-down db-logs db-wait db-migrate db-test db-test-pit db-reset db-shell \
	migration-current migration-history migration-check python-lint python-format-check \
	python-test python-test-integration brsapi-test brsapi-test-integration \
	brsapi-ingest-fixture brsapi-ingest-live catalog-test catalog-test-integration \
	catalog-validate-fixture catalog-bootstrap-fixture catalog-symbol-live-check calendar-test \
	calendar-test-integration calendar-validate-fixture calendar-import-fixture \
	bootstrap-e2e-fixture app-config-check app-db-health app-db-current check \
	snapshot-test snapshot-test-integration snapshot-e2e-fixture snapshot-build-fixture \
	snapshot-verify-fixture

snapshot-test:
	$(UV_RUN) pytest -m "not integration" tests/unit/test_snapshot_*.py

snapshot-test-integration: export BISFIN_RUN_DB_INTEGRATION := 1
snapshot-test-integration: db-wait
	$(UV_RUN) pytest -m integration \
		tests/integration/test_snapshot_repository.py \
		tests/integration/test_snapshot_eligibility.py \
		tests/integration/test_snapshot_lifecycle.py \
		tests/integration/test_snapshot_idempotency.py \
		tests/integration/test_snapshot_consistent_read.py \
		tests/integration/test_snapshot_cli.py

snapshot-e2e-fixture: export BISFIN_RUN_DB_INTEGRATION := 1
snapshot-e2e-fixture: db-wait
	$(UV_RUN) pytest -m integration tests/integration/test_snapshot_e2e.py

snapshot-build-fixture: db-wait
	$(MAKE) catalog-bootstrap-fixture
	$(MAKE) calendar-import-fixture
	$(MAKE) brsapi-ingest-fixture
	$(BASH) scripts/db/build_snapshot_fixture.sh

snapshot-verify-fixture: db-wait
	$(UV_RUN) bisfin snapshot verify --code fixture-daily-raw-2026-01 --against-db

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
	$(UV_RUN) pytest -m integration tests/test_temporal_overlap_concurrency.py tests/test_raw_event_partition_concurrency.py

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

brsapi-test:
	$(UV_RUN) pytest -m "not integration" tests/unit -k "brsapi or ingestion"

brsapi-test-integration: export BISFIN_RUN_DB_INTEGRATION := 1
brsapi-test-integration: db-wait
	$(UV_RUN) pytest -m integration tests/integration -k brsapi

brsapi-ingest-fixture: db-wait
	$(UV_RUN) bisfin ingest brsapi-daily-bars --symbol "$(BRSAPI_SYMBOL)" --fixture "$(BRSAPI_FIXTURE)" --output-format "$(BRSAPI_OUTPUT_FORMAT)"

brsapi-ingest-live: db-wait
	@test "$(BISFIN_RUN_BRSAPI_LIVE_TEST)" = "1" || (echo "Live BrsApi ingestion is disabled; set BISFIN_RUN_BRSAPI_LIVE_TEST=1 explicitly." >&2; exit 2)
	$(UV_RUN) bisfin ingest brsapi-daily-bars --symbol "$(BRSAPI_SYMBOL)" --output-format "$(BRSAPI_OUTPUT_FORMAT)"

catalog-test:
	$(UV_RUN) pytest -m "not integration" tests/unit/test_catalog_manifest.py tests/unit/test_catalog_manifest_validation.py tests/unit/test_brsapi_symbol_client.py tests/unit/test_brsapi_symbol_parser.py

catalog-test-integration: export BISFIN_RUN_DB_INTEGRATION := 1
catalog-test-integration: db-wait
	$(UV_RUN) pytest -m integration \
		tests/integration/test_catalog_temporal_coverage.py \
		tests/integration/test_catalog_identity_matrix.py \
		tests/integration/test_catalog_calendar_audit_durability.py::test_provider_mismatch_keeps_batch_and_raw_evidence \
		tests/integration/test_catalog_calendar_audit_durability.py::test_malformed_provider_response_keeps_exact_raw_evidence \
		tests/integration/test_catalog_calendar_audit_durability.py::test_canonical_conflict_keeps_catalog_raw_audit_and_existing_rows \
		tests/integration/test_catalog_calendar_audit_durability.py::test_invalid_manifest_creates_no_audit_or_canonical_rows \
		tests/integration/test_catalog_calendar_concurrency.py::test_concurrent_identical_catalog_bootstrap_creates_one_instrument \
		tests/integration/test_catalog_calendar_concurrency.py::test_concurrent_conflicting_symbol_renames_create_one_canonical_history \
		tests/integration/test_catalog_calendar_concurrency.py::test_independent_instrument_specification_writes_are_not_globally_serialized \
		tests/integration/test_catalog_calendar_concurrency.py::test_concurrent_conflicting_symbol_ownership_cannot_both_commit

catalog-validate-fixture:
	$(UV_RUN) bisfin catalog validate --manifest tests/fixtures/catalog/catalog_bootstrap_success.json

catalog-bootstrap-fixture: db-wait
	$(UV_RUN) bisfin catalog bootstrap --manifest tests/fixtures/catalog/catalog_bootstrap_success.json --validation-mode fixture-validate --symbol-fixture-dir tests/fixtures/brsapi/symbols

catalog-symbol-live-check: db-wait
	@test "$(BISFIN_RUN_BRSAPI_LIVE_TEST)" = "1" || (echo "Live BrsApi Symbol validation is disabled; set BISFIN_RUN_BRSAPI_LIVE_TEST=1 explicitly." >&2; exit 2)
	$(UV_RUN) bisfin catalog bootstrap --manifest tests/fixtures/catalog/catalog_bootstrap_success.json --validation-mode live-validate

calendar-test:
	$(UV_RUN) pytest -m "not integration" tests/unit/test_calendar_manifest.py

calendar-test-integration: export BISFIN_RUN_DB_INTEGRATION := 1
calendar-test-integration: db-wait
	$(UV_RUN) pytest -m integration \
		tests/integration/test_calendar_import.py \
		tests/integration/test_catalog_calendar_audit_durability.py::test_calendar_conflict_keeps_raw_rows_and_rolls_back_new_sessions \
		tests/integration/test_catalog_calendar_concurrency.py::test_concurrent_identical_calendar_import_creates_no_duplicate_sessions

calendar-validate-fixture:
	$(UV_RUN) bisfin calendar validate --file tests/fixtures/calendar/tse_regular_success.json

calendar-import-fixture: db-wait
	$(UV_RUN) bisfin calendar import --file tests/fixtures/calendar/tse_regular_success.json

bootstrap-e2e-fixture: db-wait
	$(UV_RUN) pytest -m integration tests/integration/test_catalog_calendar_bootstrap.py

app-config-check:
	$(UV_RUN) bisfin config-check

app-db-health: db-wait
	$(UV_RUN) bisfin db-health

app-db-current: db-wait
	$(UV_RUN) bisfin db-current

check: python-lint python-format-check python-test migration-check db-test
