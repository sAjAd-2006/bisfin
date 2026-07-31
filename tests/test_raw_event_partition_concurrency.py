"""Real PostgreSQL concurrency checks for raw-event partition creation."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from typing import Any

import psycopg
import pytest
from migration_registry import build_database_url
from psycopg import sql

pytestmark = pytest.mark.integration

_RUN_INTEGRATION = "BISFIN_RUN_DB_INTEGRATION"
_SUITE_LOCK_KEY = 740_113_004_901
_PARTITION_MONTH = date(2198, 4, 1)
_PARTITION_SCHEMA = "ingest"
_PARTITION_NAME = "raw_event_y2198m04"

Connection = psycopg.Connection[tuple[Any, ...]]


@dataclass(frozen=True, slots=True)
class AttemptResult:
    committed: bool
    sqlstate: str | None
    message: str | None


def _database_conninfo() -> str:
    url = build_database_url().set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


def _connect(conninfo: str, application_name: str) -> Connection:
    connection = psycopg.connect(
        conninfo,
        connect_timeout=5,
        application_name=application_name,
    )
    connection.execute("SET LOCAL idle_in_transaction_session_timeout = '15s'")
    return connection


def _drop_test_partition(connection: Connection) -> None:
    row = connection.execute(
        """
        SELECT child.oid,
               child.relispartition,
               EXISTS (
                   SELECT 1
                   FROM pg_catalog.pg_inherits AS inheritance
                   WHERE inheritance.inhrelid = child.oid
                     AND inheritance.inhparent = 'ingest.raw_event'::regclass
               ) AS attached_to_raw_event
        FROM pg_catalog.pg_class AS child
        JOIN pg_catalog.pg_namespace AS namespace
          ON namespace.oid = child.relnamespace
        WHERE namespace.nspname = %s
          AND child.relname = %s
        """,
        (_PARTITION_SCHEMA, _PARTITION_NAME),
    ).fetchone()
    if row is None:
        return
    if row[1] is not True or row[2] is not True:
        raise AssertionError(
            f"Refusing to drop unrelated test relation {_PARTITION_SCHEMA}.{_PARTITION_NAME}."
        )
    connection.execute(
        sql.SQL("DROP TABLE {}.{}").format(
            sql.Identifier(_PARTITION_SCHEMA),
            sql.Identifier(_PARTITION_NAME),
        )
    )


@pytest.fixture(scope="module")
def partition_conninfo() -> Iterator[str]:
    if os.environ.get(_RUN_INTEGRATION) != "1":
        pytest.skip(f"set {_RUN_INTEGRATION}=1 to run PostgreSQL integration tests")

    conninfo = _database_conninfo()
    with psycopg.connect(conninfo, connect_timeout=5, autocommit=True) as control:
        control.execute("SET statement_timeout = '15s'")
        control.execute("SELECT pg_advisory_lock(%s)", (_SUITE_LOCK_KEY,))
        try:
            _drop_test_partition(control)
            yield conninfo
        finally:
            _drop_test_partition(control)
            released = control.execute(
                "SELECT pg_advisory_unlock(%s)", (_SUITE_LOCK_KEY,)
            ).fetchone()
            if released is None or released[0] is not True:
                raise AssertionError(
                    "Raw-event partition test suite advisory lock was not released."
                )


def _attempt_create_and_commit(connection: Connection) -> AttemptResult:
    try:
        connection.execute(
            "SELECT ingest.create_raw_event_month_partition(%s)",
            (_PARTITION_MONTH,),
        )
        connection.commit()
    except psycopg.Error as error:
        connection.rollback()
        return AttemptResult(False, error.sqlstate, str(error))
    return AttemptResult(True, None, None)


def _wait_until_advisory_blocked(
    observer: Connection,
    blocked_pid: int,
    future: Future[AttemptResult],
    timeout_seconds: float,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        row = observer.execute(
            """
            SELECT wait_event_type, wait_event
            FROM pg_catalog.pg_stat_activity
            WHERE pid = %s
            """,
            (blocked_pid,),
        ).fetchone()
        if row is not None and row[0] == "Lock" and str(row[1]).lower() == "advisory":
            return
        if future.done():
            raise AssertionError(
                "Second same-month call completed before waiting for the "
                f"transaction advisory lock: {future.result()}."
            )
        time.sleep(0.02)
    raise AssertionError(
        f"Backend {blocked_pid} did not enter an advisory-lock wait within {timeout_seconds:.1f}s."
    )


def _assert_partition_catalog_health(conninfo: str) -> None:
    with psycopg.connect(
        conninfo,
        connect_timeout=5,
        autocommit=True,
        application_name="raw04-catalog-observer",
    ) as connection:
        relation = connection.execute(
            """
            SELECT child.oid, child.relispartition,
                   count(inheritance.inhparent)
            FROM pg_catalog.pg_class AS child
            LEFT JOIN pg_catalog.pg_inherits AS inheritance
              ON inheritance.inhrelid = child.oid
             AND inheritance.inhparent = 'ingest.raw_event'::regclass
            WHERE child.oid = to_regclass(%s)
            GROUP BY child.oid, child.relispartition
            """,
            (f"{_PARTITION_SCHEMA}.{_PARTITION_NAME}",),
        ).fetchone()
        assert relation is not None
        partition_oid = int(relation[0])
        assert relation[1] is True
        assert relation[2] == 1

        index_state = connection.execute(
            """
            SELECT count(*),
                   count(*) FILTER (
                       WHERE NOT child_metadata.indisvalid
                          OR NOT child_metadata.indisready
                   )
            FROM pg_catalog.pg_inherits AS inheritance
            JOIN pg_catalog.pg_index AS parent_metadata
              ON parent_metadata.indexrelid = inheritance.inhparent
            JOIN pg_catalog.pg_index AS child_metadata
              ON child_metadata.indexrelid = inheritance.inhrelid
            WHERE parent_metadata.indrelid = 'ingest.raw_event'::regclass
              AND child_metadata.indrelid = %s
            """,
            (partition_oid,),
        ).fetchone()
        assert index_state == (4, 0)

        invalid_constraints = connection.execute(
            """
            SELECT count(*)
            FROM pg_catalog.pg_constraint
            WHERE conrelid = %s
              AND NOT convalidated
            """,
            (partition_oid,),
        ).fetchone()
        assert invalid_constraints == (0,)


def test_same_month_partition_calls_serialize_and_both_commit(
    partition_conninfo: str,
) -> None:
    connection_one = _connect(partition_conninfo, "raw04-partition-creator")
    connection_two = _connect(partition_conninfo, "raw04-partition-waiter")
    observer = psycopg.connect(
        partition_conninfo,
        connect_timeout=5,
        autocommit=True,
        application_name="raw04-lock-observer",
    )
    try:
        connection_one.execute("SET LOCAL lock_timeout = '8s'")
        connection_one.execute("SET LOCAL statement_timeout = '10s'")
        connection_two.execute("SET LOCAL lock_timeout = '8s'")
        connection_two.execute("SET LOCAL statement_timeout = '10s'")

        connection_one.execute(
            "SELECT ingest.create_raw_event_month_partition(%s)",
            (_PARTITION_MONTH,),
        )

        blocked_pid = connection_two.info.backend_pid
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_attempt_create_and_commit, connection_two)
            _wait_until_advisory_blocked(
                observer,
                blocked_pid,
                future,
                timeout_seconds=3.0,
            )

            connection_one.commit()
            result = future.result(timeout=5.0)

        assert result == AttemptResult(True, None, None)
        _assert_partition_catalog_health(partition_conninfo)
    finally:
        connection_one.rollback()
        connection_two.rollback()
        observer.close()
        connection_one.close()
        connection_two.close()
