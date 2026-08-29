# Implementation Plan: PR-08 Deterministic Reference Backtest Engine

## Overview

Implement an artifact-backed, deterministic, long-only reference backtest engine
without changing the PostgreSQL schema. The frozen PR-07 snapshot remains the
only market-data source during simulation; PostgreSQL persists and protects
lineage.

## Architecture Decisions

- Keep Alembic at `0004`; use existing backtest tables and PIT triggers.
- Parse run manifests strictly and use canonical JSON plus SHA-256 for semantic
  identity.
- Verify snapshot artifacts before any `backtest.run` row is created.
- Simulate in memory, then persist the complete ledger atomically.
- Treat explicit `run_instrument` rows as operational truth; `universe_id` is
  provenance only.

## Task List

### Phase 1: Foundation

- [x] Task 1: Inspect and map all existing backtest tables and safeguards.
- [x] Task 2: Add strict run-manifest, canonical hashing, artifact loader, PIT
  selector, and deterministic SMA strategy with unit tests.
- [x] Task 3: Add SQLAlchemy Core mappings and focused run/ledger repositories.

### Checkpoint: Foundation

- [x] Unit tests and metadata mapping checks pass with no migration `0005`.

### Phase 2: Reference-engine vertical slice

- [x] Task 4: Implement deterministic scheduling, next-bar execution, Decimal
  costs, accounting, valuation, summary, and semantic result hash.
- [x] Task 5: Implement lifecycle/idempotency and atomic persistence through
  existing database triggers.
- [x] Task 6: Add PostgreSQL integration coverage for artifact-only execution,
  persisted PIT lineage, database-trigger validation, and run idempotency.

### Checkpoint: Engine

- [x] Integration tests prove artifact-only replay and database safeguards.

### Phase 3: Delivery

- [x] Task 7: Add CLI commands, Make targets, Persian documentation,
  and CI pre/post-reset acceptance coverage.
- [~] Task 8: Run complete verification, review protected SQL hashes, commit,
  push, and wait for hosted CI on the final SHA.

## Risks and Mitigations

| Risk | Mitigation |
| --- | --- |
| Existing trigger shape is insufficient | Prove with a focused failing test before considering a migration. |
| Snapshot artifact differs from live database | Use artifact JSONL exclusively for simulation; DB only validates identities and persists lineage. |
| Financial rounding drift | Use `Decimal` throughout and hash canonical exact strings. |
| Shared-cash nondeterminism | Sort events explicitly by timestamp, priority, instrument, series and bar. |
