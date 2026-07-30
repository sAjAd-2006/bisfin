"""Cheap, structured PostgreSQL readiness and schema-contract checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from bisfin.db.errors import map_database_error
from bisfin.schema_contract import ALEMBIC_HEAD_REVISION

REQUIRED_SCHEMAS: Final[frozenset[str]] = frozenset(
    {"catalog", "ingest", "market", "backtest", "ml"}
)
PIT_FUNCTION_SIGNATURE: Final[str] = (
    "market.bars_as_of(bigint,timestamp with time zone,timestamp with time zone,"
    "timestamp with time zone,character varying)"
)


def registered_head_revision() -> str:
    """Return the package-owned head shared with the migration registry."""

    return ALEMBIC_HEAD_REVISION


@dataclass(frozen=True, slots=True)
class HealthCheckResult:
    """One independently understandable health invariant."""

    name: str
    healthy: bool
    message: str
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DatabaseHealthReport:
    """Complete health assessment suitable for CLI and structured logs."""

    checked_at: datetime
    expected_revision: str
    checks: tuple[HealthCheckResult, ...]
    current_revision: str | None = None
    postgresql_major_version: int | None = None

    @property
    def healthy(self) -> bool:
        return all(check.healthy for check in self.checks)

    @property
    def failed_checks(self) -> tuple[HealthCheckResult, ...]:
        return tuple(check for check in self.checks if not check.healthy)

    def summary(self) -> str:
        """Return a concise summary without connection or credential details."""

        if self.healthy:
            return (
                "healthy: PostgreSQL 16 reachable; revision "
                f"{self.current_revision}; schemas, PIT function, indexes, and "
                "constraints verified"
            )
        failed = ", ".join(check.name for check in self.failed_checks)
        return f"unhealthy: failed checks: {failed}"


_CHECK_NAMES: Final[tuple[str, ...]] = (
    "connectivity",
    "postgresql_version",
    "alembic_revision",
    "required_schemas",
    "point_in_time_function",
    "valid_indexes",
    "validated_constraints",
)


class DatabaseHealthChecker:
    """Run catalog-only checks; no application table is scanned."""

    def __init__(
        self,
        engine: Engine,
        *,
        expected_revision: str | None = None,
        required_schemas: frozenset[str] = REQUIRED_SCHEMAS,
    ) -> None:
        self._engine = engine
        self._expected_revision = expected_revision or registered_head_revision()
        self._required_schemas = required_schemas

    @property
    def expected_revision(self) -> str:
        return self._expected_revision

    def check(self) -> DatabaseHealthReport:
        """Return a report even when PostgreSQL cannot be reached."""

        checked_at = datetime.now(UTC)
        try:
            connection = self._engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        except SQLAlchemyError as error:
            mapped = map_database_error(error, operation="database health connectivity")
            checks = [
                HealthCheckResult("connectivity", False, str(mapped)),
            ]
            checks.extend(
                HealthCheckResult(
                    name,
                    False,
                    "Skipped because PostgreSQL is unreachable.",
                    {"skipped": True},
                )
                for name in _CHECK_NAMES[1:]
            )
            return DatabaseHealthReport(
                checked_at=checked_at,
                expected_revision=self._expected_revision,
                checks=tuple(checks),
            )

        try:
            return self._check_connected(connection, checked_at)
        finally:
            connection.close()

    def _check_connected(
        self,
        connection: Connection,
        checked_at: datetime,
    ) -> DatabaseHealthReport:
        checks: list[HealthCheckResult] = []
        current_revision: str | None = None
        major_version: int | None = None

        try:
            scalar = connection.execute(text("SELECT 1")).scalar_one()
            healthy = scalar == 1
            checks.append(
                HealthCheckResult(
                    "connectivity",
                    healthy,
                    "SELECT 1 succeeded." if healthy else "SELECT 1 returned an unexpected value.",
                )
            )
        except SQLAlchemyError as error:
            checks.append(self._query_failure("connectivity", error))

        try:
            version_number = int(
                connection.execute(
                    text("SELECT current_setting('server_version_num')::integer")
                ).scalar_one()
            )
            major_version = version_number // 10_000
            checks.append(
                HealthCheckResult(
                    "postgresql_version",
                    major_version == 16,
                    (
                        "PostgreSQL major version is 16."
                        if major_version == 16
                        else f"Expected PostgreSQL 16; received major version {major_version}."
                    ),
                    {"major_version": major_version, "server_version_num": version_number},
                )
            )
        except SQLAlchemyError as error:
            checks.append(self._query_failure("postgresql_version", error))

        try:
            revisions = tuple(
                str(value)
                for value in connection.execute(
                    text("SELECT version_num FROM alembic_version ORDER BY version_num")
                ).scalars()
            )
            current_revision = ",".join(revisions) if revisions else None
            revision_matches = revisions == (self._expected_revision,)
            checks.append(
                HealthCheckResult(
                    "alembic_revision",
                    revision_matches,
                    (
                        f"Alembic revision is {self._expected_revision}."
                        if revision_matches
                        else (
                            f"Expected Alembic revision {self._expected_revision}; "
                            f"received {current_revision or 'none'}."
                        )
                    ),
                    {
                        "expected_revision": self._expected_revision,
                        "current_revisions": revisions,
                    },
                )
            )
        except SQLAlchemyError as error:
            checks.append(self._query_failure("alembic_revision", error))

        try:
            existing_schemas = frozenset(
                str(value)
                for value in connection.execute(
                    text("SELECT nspname FROM pg_catalog.pg_namespace")
                ).scalars()
            )
            missing_schemas = sorted(self._required_schemas - existing_schemas)
            checks.append(
                HealthCheckResult(
                    "required_schemas",
                    not missing_schemas,
                    (
                        "All required schemas exist."
                        if not missing_schemas
                        else f"Missing required schemas: {', '.join(missing_schemas)}."
                    ),
                    {
                        "required_schemas": sorted(self._required_schemas),
                        "missing_schemas": missing_schemas,
                    },
                )
            )
        except SQLAlchemyError as error:
            checks.append(self._query_failure("required_schemas", error))

        try:
            function_exists = bool(
                connection.execute(
                    text("SELECT pg_catalog.to_regprocedure(:signature) IS NOT NULL"),
                    {"signature": PIT_FUNCTION_SIGNATURE},
                ).scalar_one()
            )
            checks.append(
                HealthCheckResult(
                    "point_in_time_function",
                    function_exists,
                    (
                        "market.bars_as_of exists with the required signature."
                        if function_exists
                        else "Required function market.bars_as_of is missing."
                    ),
                    {"signature": PIT_FUNCTION_SIGNATURE},
                )
            )
        except SQLAlchemyError as error:
            checks.append(self._query_failure("point_in_time_function", error))

        try:
            invalid_indexes = int(
                connection.execute(
                    text(
                        """
                        SELECT count(*)
                        FROM pg_catalog.pg_index AS index
                        JOIN pg_catalog.pg_class AS relation
                          ON relation.oid = index.indexrelid
                        JOIN pg_catalog.pg_namespace AS namespace
                          ON namespace.oid = relation.relnamespace
                        WHERE NOT index.indisvalid
                          AND namespace.nspname <> 'information_schema'
                          AND namespace.nspname NOT LIKE 'pg\\_%' ESCAPE '\\'
                        """
                    )
                ).scalar_one()
            )
            checks.append(
                HealthCheckResult(
                    "valid_indexes",
                    invalid_indexes == 0,
                    (
                        "No invalid indexes exist."
                        if invalid_indexes == 0
                        else f"Found {invalid_indexes} invalid indexes."
                    ),
                    {"invalid_index_count": invalid_indexes},
                )
            )
        except SQLAlchemyError as error:
            checks.append(self._query_failure("valid_indexes", error))

        try:
            unvalidated_constraints = int(
                connection.execute(
                    text(
                        """
                        SELECT count(*)
                        FROM pg_catalog.pg_constraint AS constraint_record
                        JOIN pg_catalog.pg_namespace AS namespace
                          ON namespace.oid = constraint_record.connamespace
                        WHERE NOT constraint_record.convalidated
                          AND namespace.nspname <> 'information_schema'
                          AND namespace.nspname NOT LIKE 'pg\\_%' ESCAPE '\\'
                        """
                    )
                ).scalar_one()
            )
            checks.append(
                HealthCheckResult(
                    "validated_constraints",
                    unvalidated_constraints == 0,
                    (
                        "No unvalidated constraints exist."
                        if unvalidated_constraints == 0
                        else f"Found {unvalidated_constraints} unvalidated constraints."
                    ),
                    {"unvalidated_constraint_count": unvalidated_constraints},
                )
            )
        except SQLAlchemyError as error:
            checks.append(self._query_failure("validated_constraints", error))

        return DatabaseHealthReport(
            checked_at=checked_at,
            expected_revision=self._expected_revision,
            current_revision=current_revision,
            postgresql_major_version=major_version,
            checks=tuple(checks),
        )

    @staticmethod
    def _query_failure(name: str, error: SQLAlchemyError) -> HealthCheckResult:
        mapped = map_database_error(error, operation=f"database health {name}")
        return HealthCheckResult(name, False, str(mapped))


__all__ = [
    "DatabaseHealthChecker",
    "DatabaseHealthReport",
    "HealthCheckResult",
    "PIT_FUNCTION_SIGNATURE",
    "REQUIRED_SCHEMAS",
    "registered_head_revision",
]
