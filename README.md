# Bisfin Database

Bisfin currently provides a PostgreSQL-first database foundation for
reproducible **technical-strategy backtesting**. The current implementation
covers versioned market data, point-in-time replay, strategy runs, simulated
execution, accounting, and performance reporting.

ML/DL application and pipeline work is deferred. Existing reserved ML schema
objects are not extended by this infrastructure change. Python application
code, Alembic, TimescaleDB, and PostgreSQL extensions are intentionally out of
scope.

## Repository structure

```text
.
|-- .github/workflows/database-ci.yml  # PostgreSQL 16 CI
|-- db/postgresql/                     # Existing migrations and smoke tests
|-- docs/                              # Architecture and schema documentation
|-- refrences/                         # Local database-design reference notes
|-- scripts/db/                        # Non-interactive database automation
|-- .env.example                       # Safe local development defaults
|-- docker-compose.yml                 # Local PostgreSQL 16 service
`-- Makefile                           # Developer database commands
```

## Prerequisites

- Docker Engine or Docker Desktop with Docker Compose v2
- GNU Make
- Bash

No host installation of PostgreSQL or `psql` is required; all SQL commands use
the client included in the PostgreSQL 16 container.

On Windows, run the commands from WSL or Git Bash with GNU Make available.
PowerShell users can create the environment file with
`Copy-Item .env.example .env` instead of `cp`.

## Local setup

Create the ignored local environment file, start PostgreSQL, wait for it, and
apply the migrations:

```bash
cp .env.example .env
make db-up
make db-wait
make db-migrate
```

The values in `.env.example` are development-only defaults. Override them in
the untracked `.env` file; never put production credentials in this repository.
The database is exposed only on `127.0.0.1` at `POSTGRES_PORT`.

## Database tests

After migrations have been applied, run the idempotency checks and both smoke
tests:

```bash
make db-test
```

The test target executes, in order:

1. `001_core_schema.sql` again (idempotency)
2. `003_technical_backtest_completion.sql` again (idempotency)
3. `002_smoke_test.sql`
4. `004_technical_backtest_smoke_test.sql`

Both smoke-test files run inside transactions and roll back their fixtures.
Every `psql` invocation uses `ON_ERROR_STOP=1`, so the command fails on the
first SQL error.

To recreate the development database from an empty database and run the full
migration and test sequence:

```bash
make db-reset
```

`db-reset` protects the `postgres`, `template0`, and `template1` databases from
accidental deletion.

## Make targets

| Target | Purpose |
| --- | --- |
| `make db-up` | Start the PostgreSQL 16 service. |
| `make db-down` | Stop the stack while preserving the named data volume. |
| `make db-logs` | Follow the latest PostgreSQL logs. |
| `make db-wait` | Wait for an authenticated database query to succeed. |
| `make db-migrate` | Apply the two migrations in canonical order. |
| `make db-test` | Reapply migrations and execute both smoke tests. |
| `make db-reset` | Drop/recreate the development DB, migrate, and test it. |
| `make db-shell` | Open an interactive `psql` session in the container. |

Typical operational commands:

```bash
make db-shell
make db-logs
make db-down
```

## SQL execution order

Production/development migrations use only this order:

1. `db/postgresql/001_core_schema.sql`
2. `db/postgresql/003_technical_backtest_completion.sql`

Files `002` and `004` are tests, not migrations. See
[db/postgresql/README.md](db/postgresql/README.md) for the role of every SQL
file.

## Continuous integration

GitHub Actions uses the same Compose service, Make targets, and shell scripts
as local development. On every push and pull request it creates an empty
PostgreSQL 16 state, applies migrations, reapplies them for idempotency, and
runs both smoke tests.
