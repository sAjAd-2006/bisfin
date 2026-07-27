# Bisfin Database

Bisfin currently provides a PostgreSQL-first foundation for reproducible
technical-strategy backtesting. It covers versioned market data,
point-in-time replay, strategy runs, simulated execution, accounting, and
performance reporting. Temporal catalog rows are protected against overlap,
and historical bar consumers use the audited `market.bars_as_of(...)`
interface instead of an unbounded latest-revision view.

The Python 3.12 code in this repository is limited to migration
infrastructure: Alembic executes the existing raw SQL and a small registry
verifies its identity. There is no Python application or SQLAlchemy domain
model. ML/DL pipelines remain deferred, and no PostgreSQL extension is
required.

## Repository structure

```text
.
|-- .github/workflows/database-ci.yml     # PostgreSQL 16 and Python CI
|-- alembic/                              # Alembic environment and revisions
|-- db/postgresql/
|   |-- migrations/                       # Immutable, registered raw SQL
|   `-- tests/                            # Raw SQL smoke tests
|-- docs/                                 # Architecture and schema docs
|-- scripts/db/                           # Database automation and checks
|-- tests/                                # Python migration-registry tests
|-- .env.example                          # Safe local defaults
|-- alembic.ini                           # Alembic configuration
|-- docker-compose.yml                    # Local PostgreSQL 16 service
|-- migration_registry.py                 # Ordered checksums and DB URL config
|-- pyproject.toml                         # Python 3.12 project configuration
|-- uv.lock                               # Reproducible Python dependency lock
`-- Makefile                              # Developer commands
```

## Prerequisites

- Docker Engine or Docker Desktop with Docker Compose v2
- GNU Make
- Bash
- Python 3.12
- [uv](https://docs.astral.sh/uv/)

No host installation of PostgreSQL or `psql` is required. Raw SQL tests use
the PostgreSQL 16 container. Alembic uses synchronous `psycopg` from the uv
environment and connects to the container through its localhost port.

On Windows, use WSL or Git Bash with GNU Make available. PowerShell users can
create the environment file with `Copy-Item .env.example .env` instead of
`cp`.

## Local setup

Create the ignored development environment, synchronize the locked Python
environment, start PostgreSQL, and migrate the empty database:

```bash
cp .env.example .env
uv sync --locked --dev
make db-up
make db-wait
make db-migrate
```

The values in `.env.example` are development-only. Override them in the
untracked `.env`; never commit production credentials. PostgreSQL is exposed
only on `127.0.0.1` at `POSTGRES_PORT`.

Alembic uses `DATABASE_URL` when it is present. Otherwise it constructs a
synchronous `postgresql+psycopg` URL for `localhost` from `POSTGRES_DB`,
`POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_PORT`.

## Migration commands

```bash
make db-migrate
make migration-current
make migration-history
make migration-check
```

`db-migrate` waits for PostgreSQL and runs `uv run alembic upgrade head`.
Running it again at head is a no-op. `migration-current` reports the database
revision and its head marker, `migration-history` displays the revision chain,
and `migration-check` rejects an unknown/non-head database revision,
a registry/Alembic graph mismatch, a missing SQL file, or a checksum change.

Downgrades are intentionally unsupported because the existing schema has no
safe destructive rollback. Corrections must be introduced as forward
migrations.

## Database and Python tests

After migration, re-execute all idempotent raw migrations and all SQL smoke
tests:

```bash
make db-test
```

The raw sequence is:

1. `db/postgresql/migrations/0001_core_schema.sql` again
2. `db/postgresql/migrations/0002_technical_backtest_completion.sql` again
3. `db/postgresql/migrations/0003_point_in_time_hardening.sql` again
4. `db/postgresql/tests/0001_core_smoke.sql`
5. `db/postgresql/tests/0002_technical_backtest_smoke.sql`
6. `db/postgresql/tests/0003_point_in_time_hardening_smoke.sql`

The smoke tests manage their own transactions and roll back their fixtures.
Every `psql` invocation uses `ON_ERROR_STOP=1`, so it fails on the first SQL
error.

Run Python validation independently with:

```bash
make python-lint
make python-test
```

The regular Python suite skips the live PostgreSQL concurrency module unless
it is explicitly enabled. With PostgreSQL migrated through `0003`, run its
bounded two-connection test with:

```bash
make db-test-pit
```

To drop and recreate only the configured development database, then run
Alembic and all raw database tests again:

```bash
make db-reset
```

`db-reset` refuses to delete `postgres`, `template0`, or `template1`.

## Make targets

| Target | Purpose |
| --- | --- |
| `make db-up` | Start PostgreSQL 16. |
| `make db-down` | Stop the stack while preserving its named volume. |
| `make db-logs` | Follow recent PostgreSQL logs. |
| `make db-wait` | Wait for an authenticated query to succeed. |
| `make db-migrate` | Upgrade the database to Alembic head. |
| `make db-test` | Reapply all raw migrations and run all SQL smoke tests. |
| `make db-test-pit` | Run the real PostgreSQL temporal-concurrency test. |
| `make db-reset` | Recreate the development DB, migrate, and test it. |
| `make db-shell` | Open an interactive containerized `psql`. |
| `make migration-current` | Verify/report the current Alembic head. |
| `make migration-history` | Show ordered Alembic history. |
| `make migration-check` | Validate DB state, registry, files, and checksums. |
| `make python-lint` | Run Ruff and mypy. |
| `make python-test` | Run database-independent pytest tests. |

## Raw SQL order and safety model

The authoritative schema remains raw SQL; Alembic revisions do not reproduce
tables, functions, triggers, constraints, indexes, views, or seed data with
SQLAlchemy metadata. The only production/development order is:

1. Alembic revision `0001` -> `db/postgresql/migrations/0001_core_schema.sql`
2. Alembic revision `0002` -> `db/postgresql/migrations/0002_technical_backtest_completion.sql`
3. Alembic revision `0003` -> `db/postgresql/migrations/0003_point_in_time_hardening.sql`

Before execution, the registry verifies the file exists and that its SHA-256
matches the recorded value. A session-level PostgreSQL advisory lock prevents
two migration runners from applying the chain concurrently. SQL is executed
as one batch so a PostgreSQL error stops the revision immediately.

Each inherited migration owns its existing `BEGIN`/`COMMIT`, which is
deliberately preserved byte-for-byte. Consequently, there is a narrow crash
window after that inner `COMMIT` and before Alembic records the new revision.
If this occurs, investigate the database state and rerun the idempotent
migration; do not bypass checksum validation or blindly stamp a revision.

See [db/postgresql/README.md](db/postgresql/README.md) for every SQL artifact.

## Continuous integration

GitHub Actions runs on Ubuntu 24.04 for every push and pull request. It installs
Python 3.12 dependencies with the maintained uv action, runs Ruff, mypy, and
pytest, starts an empty PostgreSQL 16 instance, upgrades it with Alembic,
checks current/head and checksums, runs raw idempotency and smoke tests, then
runs the real two-connection overlap test and repeats the complete database
sequence through `make db-reset`.

## Point-in-time safeguards

Migration `0003` enforces non-overlapping half-open intervals for the logical
keys of `catalog.instrument_identifier`,
`catalog.instrument_spec_version`, and `catalog.universe_member`. A
transaction-scoped advisory lock derived from the table and logical key
serializes competing checks without a table-wide writer bottleneck. Temporal
writes deliberately require PostgreSQL `READ COMMITTED`; other isolation
levels receive SQLSTATE `0A000` because a stale transaction snapshot cannot
provide the same guarantee after waiting for an advisory lock.

Historical code must call:

```sql
market.bars_as_of(
    p_bar_series_id BIGINT,
    p_from_ts TIMESTAMPTZ,
    p_to_ts TIMESTAMPTZ,
    p_knowledge_cutoff_ts TIMESTAMPTZ,
    p_replay_mode VARCHAR
)
```

`PUBLIC_REPLAY` filters by `available_at`; `ACTUAL_SYSTEM_REPLAY` filters by
`system_available_at`. Both modes also require a final bar, completion by the
cutoff, a half-open event range, and deterministically select one latest
eligible revision. The existing `market.current_bar` view is retained only
for current-state/operational use and is unsafe for historical backtests,
point-in-time features, or historical ML datasets.
