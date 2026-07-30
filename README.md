# Bisfin

Bisfin is a PostgreSQL-first financial-data foundation for deterministic
backtesting and point-in-time-correct research. The current scope includes:

- an immutable raw-SQL/Alembic migration chain through revision `0003`;
- catalog, ingestion, revisioned market-bar, snapshot, and backtest storage;
- audited historical reads through `market.bars_as_of(...)`;
- a synchronous Python 3.12 application foundation built with Pydantic 2,
  SQLAlchemy 2 Core, and psycopg 3;
- typed catalog, ingestion, and market-data DTOs and repositories;
- explicit transaction and Unit of Work boundaries;
- structured logging, database health checks, and operational CLI commands;
- database-independent unit tests and real PostgreSQL 16 integration tests.

This release does **not** make BrsApi requests, ingest bars, create instruments,
run strategies, calculate features, train ML models, or expose an API server.
Those behaviors remain outside PR-04.

## Repository structure

```text
.
|-- .github/workflows/database-ci.yml     # Locked Python and PostgreSQL 16 CI
|-- alembic/                              # Alembic environment and revisions
|-- db/postgresql/
|   |-- migrations/                       # Immutable registered raw SQL
|   `-- tests/                            # Transactional SQL smoke tests
|-- docs/
|   |-- python_application_architecture_fa.md
|   `-- trading_database_design_fa.md
|-- scripts/db/                           # Database lifecycle/check scripts
|-- src/bisfin/
|   |-- config/                           # Typed settings and secret handling
|   |-- db/                               # Engine, transactions, UoW, health
|   |-- domain/                           # Frozen persistence-neutral DTOs
|   |-- logging/                          # Console/JSON structured logging
|   |-- repositories/                     # Protocols and SQLAlchemy Core access
|   |-- schema_contract.py                # Shared packaged Alembic head
|   `-- cli.py                            # Application composition and CLI
|-- tests/
|   |-- fixtures/                         # Isolated fixture helpers
|   |-- integration/                      # Real PostgreSQL tests
|   `-- unit/                             # Infrastructure-free tests
|-- .env.example                          # Safe local configuration template
|-- alembic.ini                           # Alembic configuration
|-- docker-compose.yml                    # Local PostgreSQL 16 service
|-- migration_registry.py                 # Ordered SQL checksums and DB URL
|-- pyproject.toml                        # Package, tools, and test markers
|-- uv.lock                               # Reproducible dependency lock
`-- Makefile                              # Developer commands
```

The Python dependency direction is `config -> db primitives -> repositories ->
entry points`; domain DTOs remain independent of SQLAlchemy and psycopg. A
lightweight architecture test enforces the important boundaries and forbids
`metadata.create_all()`.

## Prerequisites

- Docker Engine or Docker Desktop with Docker Compose v2;
- Python 3.12;
- uv `0.11.32` (the repository rejects a different uv version);
- GNU Make and Bash.

No host installation of PostgreSQL or `psql` is required. On Windows, run Make
targets from Git Bash or WSL. PowerShell can be used to copy the environment
file:

```powershell
Copy-Item .env.example .env
```

## Local setup

```bash
cp .env.example .env
uv sync --locked --dev
uv lock --check
make db-up
make db-wait
make db-migrate
```

The values in `.env.example` are development-only. Override them in the ignored
`.env`; never commit production credentials. `DATABASE_URL`, when set, takes
precedence over the individual `POSTGRES_*` values. Otherwise the application
constructs a percent-encoded `postgresql+psycopg://` URL. CLI output, validation
errors, and logs omit the password and complete connection URL.

Supported application variables are:

```text
BISFIN_ENV
BISFIN_LOG_LEVEL
BISFIN_LOG_FORMAT
DATABASE_URL
POSTGRES_HOST
POSTGRES_PORT
POSTGRES_DB
POSTGRES_USER
POSTGRES_PASSWORD
DATABASE_POOL_SIZE
DATABASE_MAX_OVERFLOW
DATABASE_POOL_TIMEOUT_SECONDS
DATABASE_STATEMENT_TIMEOUT_MS
DATABASE_APPLICATION_NAME
```

## Python and application checks

The regular test command is deliberately database-independent:

```bash
make python-lint
make python-format-check
make python-test
```

With PostgreSQL already running and migrated through `0003`, run the dedicated
integration suite and CLI checks:

```bash
make python-test-integration
make app-config-check
make app-db-health
make app-db-current
```

Equivalent installed-console commands are:

```bash
uv run --frozen bisfin config-check
uv run --frozen bisfin db-health
uv run --frozen bisfin db-current
```

`db-health` checks connectivity, PostgreSQL major version 16, the Alembic head,
the actual required schemas (`catalog`, `ingest`, `market`, `backtest`, `ml`),
the `market.bars_as_of` function, invalid indexes, and unvalidated constraints.
`db-current` exits non-zero when the current revision differs from the registered
head.

## Database and migration checks

```bash
make db-migrate
make migration-current
make migration-history
make migration-check
make db-test
make db-test-pit
```

`db-migrate` waits for PostgreSQL and runs `uv run --frozen alembic upgrade
head`; running it again at head is a no-op. `migration-check` verifies the
Alembic graph, registered head, raw-SQL file identity, and SHA-256 checksums.

`db-test` reapplies each idempotent migration and then executes every SQL smoke
test in this order:

1. `db/postgresql/migrations/0001_core_schema.sql`
2. `db/postgresql/migrations/0002_technical_backtest_completion.sql`
3. `db/postgresql/migrations/0003_point_in_time_hardening.sql`
4. `db/postgresql/tests/0001_core_smoke.sql`
5. `db/postgresql/tests/0002_technical_backtest_smoke.sql`
6. `db/postgresql/tests/0003_point_in_time_hardening_smoke.sql`

Every `psql` call uses `ON_ERROR_STOP=1`. The smoke fixtures are transactional
and roll themselves back. `db-test-pit` runs the bounded, two-connection
PostgreSQL concurrency test.

To recreate only the configured development database and repeat migrations and
raw database tests:

```bash
make db-reset
```

The reset script refuses to delete `postgres`, `template0`, or `template1`.

## Make targets

| Target | Purpose |
| --- | --- |
| `make db-up` | Start the PostgreSQL 16 service. |
| `make db-down` | Stop the stack while retaining its named volume. |
| `make db-logs` | Follow recent PostgreSQL logs. |
| `make db-wait` | Wait for an authenticated query. |
| `make db-migrate` | Upgrade to the registered Alembic head. |
| `make db-test` | Reapply raw migrations and run SQL smoke tests. |
| `make db-test-pit` | Run the temporal concurrency integration test. |
| `make db-reset` | Recreate the development DB, migrate, and SQL-test it. |
| `make db-shell` | Open interactive containerized `psql`. |
| `make migration-current` | Report the current Alembic revision. |
| `make migration-history` | Show ordered Alembic history. |
| `make migration-check` | Validate graph, head, files, and checksums. |
| `make python-lint` | Run Ruff and strict mypy. |
| `make python-format-check` | Check formatting for `src` and new tests. |
| `make python-test` | Run only non-integration Python tests. |
| `make python-test-integration` | Run `tests/integration` against PostgreSQL. |
| `make app-config-check` | Validate and safely summarize settings. |
| `make app-db-health` | Run the structured DB health check. |
| `make app-db-current` | Compare current and expected DB revisions. |
| `make check` | Run lint, format, unit, migration, and SQL checks. |

## Point-in-time and transaction rules

Historical bar consumers call the database authority:

```sql
market.bars_as_of(
    p_bar_series_id BIGINT,
    p_from_ts TIMESTAMPTZ,
    p_to_ts TIMESTAMPTZ,
    p_knowledge_cutoff_ts TIMESTAMPTZ,
    p_replay_mode VARCHAR
)
```

`PUBLIC_REPLAY` uses `available_at`; `ACTUAL_SYSTEM_REPLAY` uses
`system_available_at`. The Python repository does not reproduce revision
selection and never reads `market.current_bar` for history.

The engine preserves PostgreSQL's `READ COMMITTED` default. Temporal catalog
writes must remain at that isolation level because the overlap triggers in
migration `0003` require a fresh snapshot after their advisory lock. Explicit
`REPEATABLE READ` or `SERIALIZABLE` temporal writes are rejected; unrelated
read-only transactions may still request those levels.

Repositories share the one connection owned by a Unit of Work and never commit
independently. `commit()` is explicit; leaving a Unit of Work without committing,
or leaving because of an exception, rolls it back.

## Continuous integration

The GitHub Actions workflow runs on `ubuntu-24.04` for pushes and pull requests.
It installs pinned uv and Python 3.12, synchronizes the locked environment, runs
Ruff/format/mypy/unit tests, starts an empty PostgreSQL 16 database, validates and
replays migrations through `0003`, executes all SQL and PIT concurrency tests,
runs the Python integration and CLI checks, resets the database, repeats health
and integration checks, publishes failure logs, and always removes the stack.

See [the Python architecture guide](docs/python_application_architecture_fa.md),
[the database design](docs/trading_database_design_fa.md), and
[the SQL artifact guide](db/postgresql/README.md) for the detailed contracts.
