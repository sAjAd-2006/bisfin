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
|   |-- 0002_technical_backtest_completion.sql
|   `-- 0003_point_in_time_hardening.sql
`-- tests/
    |-- 0001_core_smoke.sql
    |-- 0002_technical_backtest_smoke.sql
    `-- 0003_point_in_time_hardening_smoke.sql
```

## Canonical migration order

Only the files under `migrations/` mutate a real database. Their immutable
order is:

1. Alembic revision `0001`: `migrations/0001_core_schema.sql`
2. Alembic revision `0002`: `migrations/0002_technical_backtest_completion.sql`
3. Alembic revision `0003`: `migrations/0003_point_in_time_hardening.sql`

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

### `migrations/0003_point_in_time_hardening.sql`

Prevents overlapping half-open validity intervals in
`catalog.instrument_identifier`, `catalog.instrument_spec_version`, and
`catalog.universe_member`. It uses transaction-scoped advisory locks derived
from each table's logical key, so concurrent conflicting checks serialize per
entity rather than globally. It also installs the stable
`market.bars_as_of(...)` historical interface and marks `market.current_bar`
as current-state-only in its database comment.

The migration validates existing data before installing the guards, is
idempotent, owns its `BEGIN`/`COMMIT`, uses no extension, and adds no index
because the existing logical-key/PIT indexes already match the predicates.

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

### `tests/0003_point_in_time_hardening_smoke.sql`

Uses rolled-back fixtures to exercise every protected temporal table with
separate, adjacent, partial, contained, identical, open-ended, different-key,
and update intervals. It validates both replay modes, late corrections,
future/incomplete-bar rejection, half-open range boundaries, deterministic
revision selection, invalid inputs, catalog integrity, and representative
`EXPLAIN (ANALYZE, BUFFERS)` plans.

The real same-key/different-key concurrency behavior is tested separately by
`tests/test_temporal_overlap_concurrency.py` through `make db-test-pit`.

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

The catalog overlap guards use a separate transaction-scoped advisory lock
whose key contains the qualified table name and logical-key values. Hash
collisions can conservatively serialize unrelated keys but cannot admit a
false overlap. Temporal writes require `READ COMMITTED` and reject stronger
isolation with SQLSTATE `0A000`: after a lock wait, the overlap query must see
a fresh snapshot of the preceding writer. SQLSTATE `23P01` identifies an
actual overlap. Multi-row interval reshaping should be issued in deterministic
key/time order; ordinary PostgreSQL deadlocks remain retryable.

## Point-in-time bar contract

The canonical historical interface is:

```sql
market.bars_as_of(
    p_bar_series_id BIGINT,
    p_from_ts TIMESTAMPTZ,
    p_to_ts TIMESTAMPTZ,
    p_knowledge_cutoff_ts TIMESTAMPTZ,
    p_replay_mode VARCHAR
)
```

The base schema has no shared replay enum/domain, so `VARCHAR` is retained and
validated without coercion. `PUBLIC_REPLAY` uses `available_at`, while
`ACTUAL_SYSTEM_REPLAY` uses `system_available_at`. Both branches require
`is_final`, `bar_close_ts <= p_knowledge_cutoff_ts`, and the half-open range
`[p_from_ts,p_to_ts)`, then select one latest eligible revision and return
bars chronologically. Non-raw adjusted series are rejected when their
adjustment knowledge cutoff is later than the requested cutoff.

For sequential replay, call the function at each decision boundary with
`LEAST(decision_ts, data_snapshot.knowledge_cutoff_ts)`. One end-of-run cutoff
can legitimately expose late corrections to earlier bars and therefore is
not a substitute for per-decision replay. `market.current_bar` has no cutoff,
can expose the newest correction or a non-final row, and must not be used for
historical backtests, point-in-time features, or historical ML datasets. A
repository search found no executable consumer of that view; only its schema
definition and documentation references existed before migration `0003`.

## Raw test execution order

After Alembic reaches head, `scripts/db/test.sh` executes:

1. `migrations/0001_core_schema.sql` again for idempotency
2. `migrations/0002_technical_backtest_completion.sql` again for idempotency
3. `migrations/0003_point_in_time_hardening.sql` again for idempotency
4. `tests/0001_core_smoke.sql`
5. `tests/0002_technical_backtest_smoke.sql`
6. `tests/0003_point_in_time_hardening_smoke.sql`

Automated `psql` calls use `-X` and `ON_ERROR_STOP=1`. No wrapper transaction
is added because every SQL file already controls its own transaction.

```bash
make db-up
make db-wait
make db-migrate
make db-test
make db-test-pit
```

To recreate the configured development database and repeat Alembic plus all
raw SQL test layers:

```bash
make db-reset
```
