# PostgreSQL SQL files

This directory contains the executable SQL for the Bisfin database. PostgreSQL
16 is the supported local and CI baseline. The SQL requires no PostgreSQL
extension.

## Canonical migration order

Only the following files are migrations, and they must be applied in this
exact order:

1. `001_core_schema.sql`
2. `003_technical_backtest_completion.sql`

The automation in `scripts/db/migrate.sh` is the canonical executable version
of this ordering. Do not insert smoke-test files into the migration sequence.

## File purposes

### `001_core_schema.sql`

Creates the core PostgreSQL schemas, reference/catalog data, immutable ingest
zone, versioned market data, external-data registry, backtest ledger, and the
currently reserved ML metadata structures. It also defines the initial market
partition helper and lookup seed rows.

This migration is intentionally idempotent and owns its transaction with
`BEGIN`/`COMMIT`.

### `002_smoke_test.sql`

Performs non-destructive structural and query-plan smoke checks against the
core schema. It validates table/index state, creates a temporary monthly bar
partition, prepares/explains representative backtest, dataset-window, and
strategy-report queries, and rolls the transaction back.

This file is a test, not a migration.

### `003_technical_backtest_completion.sql`

Adds the existing technical-backtesting completion layer: quote and
participant-flow snapshots, point-in-time decision inputs, reproducible market
series bindings, auditable order/fill lineage, position accounting, valuation
lineage, invariants, indexes, triggers, and technical-data partition automation.

It does not extend the deferred ML/DL implementation. This migration is
idempotent and owns its transaction with `BEGIN`/`COMMIT`.

### `004_technical_backtest_smoke_test.sql`

Runs an end-to-end, non-destructive technical-backtest database test. It
creates rolled-back fixtures and verifies frozen snapshots, point-in-time
look-ahead rejection, decision-to-fill lineage, order/fill invariants,
position accounting, valuation references, partitions, indexes, constraints,
and a representative quote-as-of query plan.

This file is a test, not a migration, and ends with `ROLLBACK`.

## Test execution order

After migrations `001` and `003` have been applied once, `scripts/db/test.sh`
executes:

1. `001_core_schema.sql` a second time
2. `003_technical_backtest_completion.sql` a second time
3. `002_smoke_test.sql`
4. `004_technical_backtest_smoke_test.sql`

All automated `psql` calls use `-X` and `ON_ERROR_STOP=1`. The SQL files are
not wrapped in another transaction because they already control their own
transactions.

Run the standard sequence from the repository root:

```bash
make db-up
make db-wait
make db-migrate
make db-test
```

To recreate the database and repeat the entire sequence, run:

```bash
make db-reset
```
