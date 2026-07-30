"""Structured health-report behavior without a database dependency."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from typing import Self, cast

from sqlalchemy.engine import Engine
from sqlalchemy.sql import Executable

from bisfin.db.health import REQUIRED_SCHEMAS, DatabaseHealthChecker


class _Result:
    def __init__(self, *, scalar: object | None = None, values: Iterable[object] = ()) -> None:
        self._scalar = scalar
        self._values = tuple(values)

    def scalar_one(self) -> object:
        return self._scalar

    def scalars(self) -> Iterator[object]:
        return iter(self._values)


class _Connection:
    def __init__(self, *, revision: str = "0003", pit_exists: bool = True) -> None:
        self.revision = revision
        self.pit_exists = pit_exists
        self.closed = False

    def execution_options(self, *, isolation_level: str) -> Self:
        assert isolation_level == "AUTOCOMMIT"
        return self

    def execute(
        self,
        statement: Executable,
        parameters: Mapping[str, object] | None = None,
    ) -> _Result:
        sql = str(statement)
        if sql == "SELECT 1":
            return _Result(scalar=1)
        if "server_version_num" in sql:
            return _Result(scalar=160_014)
        if "alembic_version" in sql:
            return _Result(values=(self.revision,))
        if "pg_catalog.pg_namespace" in sql and "SELECT nspname" in sql:
            return _Result(values=REQUIRED_SCHEMAS)
        if "to_regprocedure" in sql:
            assert parameters is not None
            assert "market.bars_as_of" in str(parameters["signature"])
            return _Result(scalar=self.pit_exists)
        if "pg_catalog.pg_index" in sql:
            return _Result(scalar=0)
        if "pg_catalog.pg_constraint" in sql:
            return _Result(scalar=0)
        raise AssertionError(f"Unexpected health SQL: {sql}")

    def close(self) -> None:
        self.closed = True


class _Engine:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    def connect(self) -> _Connection:
        return self.connection


def _checker(
    *,
    revision: str = "0003",
    expected_revision: str = "0003",
    pit_exists: bool = True,
) -> tuple[DatabaseHealthChecker, _Connection]:
    connection = _Connection(revision=revision, pit_exists=pit_exists)
    engine = cast("Engine", _Engine(connection))
    return DatabaseHealthChecker(engine, expected_revision=expected_revision), connection


def test_healthy_report_is_structured_and_concise() -> None:
    checker, connection = _checker()
    report = checker.check()

    assert report.healthy is True
    assert report.failed_checks == ()
    assert report.current_revision == "0003"
    assert report.postgresql_major_version == 16
    assert report.summary().startswith("healthy: PostgreSQL 16 reachable")
    assert connection.closed is True


def test_revision_mismatch_is_a_named_failure() -> None:
    checker, _ = _checker(revision="0002")
    report = checker.check()
    check = next(item for item in report.checks if item.name == "alembic_revision")

    assert report.healthy is False
    assert check.healthy is False
    assert check.details == {
        "expected_revision": "0003",
        "current_revisions": ("0002",),
    }
    assert "alembic_revision" in report.summary()


def test_missing_point_in_time_function_is_a_named_failure() -> None:
    checker, _ = _checker(pit_exists=False)
    report = checker.check()
    check = next(item for item in report.checks if item.name == "point_in_time_function")

    assert report.healthy is False
    assert check.healthy is False
    assert check.message == "Required function market.bars_as_of is missing."
    assert "point_in_time_function" in report.summary()
