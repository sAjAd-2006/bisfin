# PostgreSQL SQL artifacts

This directory contains the authoritative raw SQL for Bisfin. PostgreSQL 16
is the supported local and CI baseline, and no PostgreSQL extension is
required. Alembic provides ordering and version state but does not translate
the schema into SQLAlchemy metadata.

## Layout

```text
db/postgresql/
|-- migrations/
|   |-- 0001_core_schema.sql
|   `-- 0002_technical_backtest_completion.sql
`-- tests/
    |-- 0001_core_smoke.sql
    `-- 0002_technical_backtest_smoke.sql
```

## Canonical migration order

Only the files under `migrations/` mutate a real database. Their immutable
order is:

1. Alembic revision `0001`: `migrations/0001_core_schema.sql`
2. Alembic revision `0002`: `migrations/0002_technical_backtest_completion.sql`

`migration_registry.py` records this dependency chain and the SHA-256 of each
file. Every upgrade verifies existence and checksum before executing SQL.
`scripts/db/check_migrations.py` also requires the Alembic revision graph to
match the registry exactly. A renamed, missing, reordered, or edited migration
therefore fails before it can be accepted as canonical history.

Run the migration and inspection commands from the repository root:

```bash
make db-migrate
make migration-current
make migration-history
make migration-check
```

Downgrade is deliberately unsupported: destructive rollback cannot be made
safe for these migrations, so schema corrections require a new forward
migration.

## Migration purposes

### `migrations/0001_core_schema.sql`

Creates the core PostgreSQL schemas, reference/catalog data, immutable ingest
zone, versioned market data, external-data registry, backtest ledger, and the
currently reserved ML metadata structures. It also defines the initial market
partition helper and lookup seed rows.

The inherited SQL is idempotent and owns its transaction with
`BEGIN`/`COMMIT`.

### `migrations/0002_technical_backtest_completion.sql`

Adds the technical-backtesting completion layer: quote and participant-flow
snapshots, point-in-time decision inputs, reproducible market-series bindings,
auditable order/fill lineage, position accounting, valuation lineage,
invariants, indexes, triggers, and technical-data partition automation.

It does not extend the deferred ML/DL implementation. The inherited SQL is
idempotent and owns its transaction with `BEGIN`/`COMMIT`.

## Test purposes

### `tests/0001_core_smoke.sql`

Performs non-destructive structural and query-plan checks against the core
schema. It validates table/index state, creates a temporary monthly bar
partition, and prepares/explains representative backtest, dataset-window, and
strategy-report queries.

This is a test, not a migration. Its fixtures are rolled back.

### `tests/0002_technical_backtest_smoke.sql`

Runs an end-to-end technical-backtest database test with rolled-back fixtures.
It verifies frozen snapshots, point-in-time look-ahead rejection,
decision-to-fill lineage, order/fill invariants, position accounting,
valuation references, partitions, indexes, constraints, and a representative
quote-as-of query plan.

This is a test, not a migration, and ends with `ROLLBACK`.

## Transaction and locking behavior

Alembic uses a synchronous psycopg connection and a session-level PostgreSQL
advisory lock for the complete upgrade. Session scope is necessary because
each raw migration executes its own `COMMIT`; a transaction-level lock would
be released between revisions. The migration bytes are executed as one batch,
so a SQL error aborts immediately and prevents the Alembic revision marker
from advancing.

Preserving each file's inner `BEGIN`/`COMMIT` creates an unavoidable narrow
crash window: PostgreSQL may commit the schema just before Alembic records its
version row. If this happens, inspect the state and rerun the idempotent raw
migration. Do not edit a registered file, disable its checksum, or blindly
stamp the revision.

## Raw test execution order

After Alembic reaches head, `scripts/db/test.sh` executes:

1. `migrations/0001_core_schema.sql` again for idempotency
2. `migrations/0002_technical_backtest_completion.sql` again for idempotency
3. `tests/0001_core_smoke.sql`
4. `tests/0002_technical_backtest_smoke.sql`

Automated `psql` calls use `-X` and `ON_ERROR_STOP=1`. No wrapper transaction
is added because every SQL file already controls its own transaction.

```bash
make db-up
make db-wait
make db-migrate
make db-test
```

To recreate the configured development database and repeat Alembic plus both
raw test layers:

```bash
make db-reset
```
