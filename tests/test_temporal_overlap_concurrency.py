"""Real PostgreSQL concurrency checks for per-key temporal overlap locking."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import psycopg
import pytest

from migration_registry import build_database_url, read_verified_sql

pytestmark = pytest.mark.integration

_RUN_INTEGRATION = "BISFIN_RUN_DB_INTEGRATION"
_SUITE_LOCK_KEY = 740_113_003_901
_INSTRUMENT_A = "PIT03_CONCURRENCY_A"
_INSTRUMENT_B = "PIT03_CONCURRENCY_B"

Connection = psycopg.Connection[tuple[Any, ...]]


@dataclass(frozen=True, slots=True)
class TemporalFixture:
    conninfo: str
    instrument_a: int
    instrument_b: int


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


def _required_scalar(row: tuple[Any, ...] | None, label: str) -> int:
    if row is None:
        raise AssertionError(f"Expected one row while reading {label}.")
    return int(row[0])


def _delete_fixture_rows(connection: Connection) -> None:
    connection.execute(
        """
        DELETE FROM catalog.instrument_spec_version AS specification
        USING catalog.instrument AS instrument
        WHERE specification.instrument_id = instrument.instrument_id
          AND instrument.canonical_symbol IN (%s, %s)
        """,
        (_INSTRUMENT_A, _INSTRUMENT_B),
    )
    connection.execute(
        """
        DELETE FROM catalog.instrument
        WHERE canonical_symbol IN (%s, %s)
          AND venue_id IS NULL
        """,
        (_INSTRUMENT_A, _INSTRUMENT_B),
    )


@pytest.fixture(scope="module")
def temporal_fixture() -> Iterator[TemporalFixture]:
    if os.environ.get(_RUN_INTEGRATION) != "1":
        pytest.skip(f"set {_RUN_INTEGRATION}=1 to run PostgreSQL integration tests")

    conninfo = _database_conninfo()
    with psycopg.connect(conninfo, connect_timeout=5, autocommit=True) as control:
        control.execute("SET statement_timeout = '15s'")
        control.execute("SELECT pg_advisory_lock(%s)", (_SUITE_LOCK_KEY,))
        try:
            _delete_fixture_rows(control)
            instrument_a = _required_scalar(
                control.execute(
                    """
                    INSERT INTO catalog.instrument
                        (asset_type_code, quote_currency_code, canonical_symbol,
                         display_name, status, active_from)
                    VALUES
                        ('EQUITY', 'IRR', %s, 'PIT concurrency instrument A',
                         'ACTIVE', TIMESTAMPTZ '2039-01-01 00:00:00+00')
                    RETURNING instrument_id
                    """,
                    (_INSTRUMENT_A,),
                ).fetchone(),
                "instrument A",
            )
            instrument_b = _required_scalar(
                control.execute(
                    """
                    INSERT INTO catalog.instrument
                        (asset_type_code, quote_currency_code, canonical_symbol,
                         display_name, status, active_from)
                    VALUES
                        ('EQUITY', 'IRR', %s, 'PIT concurrency instrument B',
                         'ACTIVE', TIMESTAMPTZ '2039-01-01 00:00:00+00')
                    RETURNING instrument_id
                    """,
                    (_INSTRUMENT_B,),
                ).fetchone(),
                "instrument B",
            )

            yield TemporalFixture(
                conninfo=conninfo,
                instrument_a=instrument_a,
                instrument_b=instrument_b,
            )
        finally:
            _delete_fixture_rows(control)
            released = control.execute(
                "SELECT pg_advisory_unlock(%s)", (_SUITE_LOCK_KEY,)
            ).fetchone()
            if released is None or released[0] is not True:
                raise AssertionError("Concurrency test suite advisory lock was not released.")


def _clear_specifications(fixture: TemporalFixture) -> None:
    with psycopg.connect(
        fixture.conninfo,
        connect_timeout=5,
        autocommit=True,
    ) as connection:
        connection.execute(
            """
            DELETE FROM catalog.instrument_spec_version
            WHERE instrument_id IN (%s, %s)
            """,
            (fixture.instrument_a, fixture.instrument_b),
        )


def _insert_specification(
    connection: Connection,
    instrument_id: int,
    effective_from: str,
    effective_to: str,
) -> None:
    connection.execute(
        """
        INSERT INTO catalog.instrument_spec_version
            (instrument_id, effective_from, effective_to)
        VALUES (%s, %s::timestamptz, %s::timestamptz)
        """,
        (instrument_id, effective_from, effective_to),
    )


def _attempt_and_commit(
    connection: Connection,
    instrument_id: int,
    effective_from: str,
    effective_to: str,
) -> AttemptResult:
    try:
        _insert_specification(
            connection,
            instrument_id,
            effective_from,
            effective_to,
        )
        connection.commit()
    except psycopg.Error as error:
        connection.rollback()
        return AttemptResult(
            committed=False,
            sqlstate=error.sqlstate,
            message=str(error),
        )
    return AttemptResult(committed=True, sqlstate=None, message=None)


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
            FROM pg_stat_activity
            WHERE pid = %s
            """,
            (blocked_pid,),
        ).fetchone()
        if (
            row is not None
            and row[0] == "Lock"
            and str(row[1]).lower() == "advisory"
        ):
            return
        if future.done():
            result = future.result()
            raise AssertionError(
                "Second same-key transaction completed before waiting for the "
                f"advisory lock: {result}."
            )
        time.sleep(0.02)
    raise AssertionError(
        f"Backend {blocked_pid} did not enter an advisory-lock wait within "
        f"{timeout_seconds:.1f}s."
    )


def _specification_count(fixture: TemporalFixture, instrument_id: int) -> int:
    with psycopg.connect(
        fixture.conninfo,
        connect_timeout=5,
        autocommit=True,
    ) as connection:
        return _required_scalar(
            connection.execute(
                """
                SELECT count(*)
                FROM catalog.instrument_spec_version
                WHERE instrument_id = %s
                """,
                (instrument_id,),
            ).fetchone(),
            "instrument specification count",
        )


def test_same_key_overlap_waits_then_exactly_one_transaction_commits(
    temporal_fixture: TemporalFixture,
) -> None:
    """A same-key writer waits, then receives 23P01 after the winner commits."""

    _clear_specifications(temporal_fixture)
    connection_one = _connect(temporal_fixture.conninfo, "pit03-conflict-winner")
    connection_two = _connect(temporal_fixture.conninfo, "pit03-conflict-waiter")
    observer = psycopg.connect(
        temporal_fixture.conninfo,
        connect_timeout=5,
        autocommit=True,
        application_name="pit03-conflict-observer",
    )
    try:
        connection_one.execute("SET LOCAL lock_timeout = '8s'")
        connection_one.execute("SET LOCAL statement_timeout = '10s'")
        connection_two.execute("SET LOCAL lock_timeout = '8s'")
        connection_two.execute("SET LOCAL statement_timeout = '10s'")

        _insert_specification(
            connection_one,
            temporal_fixture.instrument_a,
            "2045-01-01 00:00:00+00",
            "2045-03-01 00:00:00+00",
        )

        blocked_pid = connection_two.info.backend_pid
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                _attempt_and_commit,
                connection_two,
                temporal_fixture.instrument_a,
                "2045-02-01 00:00:00+00",
                "2045-04-01 00:00:00+00",
            )
            _wait_until_advisory_blocked(
                observer,
                blocked_pid,
                future,
                timeout_seconds=3.0,
            )

            connection_one.commit()
            result = future.result(timeout=5.0)

        assert sum((True, result.committed)) == 1
        assert result.committed is False
        assert result.sqlstate == "23P01"
        assert result.message is not None
        assert "catalog.instrument_spec_version" in result.message
        assert f"instrument_id={temporal_fixture.instrument_a}" in result.message
        assert "2045-02-01" in result.message
        assert "2045-04-01" in result.message
        assert _specification_count(
            temporal_fixture, temporal_fixture.instrument_a
        ) == 1
    finally:
        connection_one.rollback()
        connection_two.rollback()
        observer.close()
        connection_one.close()
        connection_two.close()


def test_different_logical_key_finishes_while_first_transaction_is_open(
    temporal_fixture: TemporalFixture,
) -> None:
    """Per-key locking lets a different instrument commit without global blocking."""

    _clear_specifications(temporal_fixture)
    connection_one = _connect(temporal_fixture.conninfo, "pit03-independent-holder")
    connection_two = _connect(temporal_fixture.conninfo, "pit03-independent-writer")
    try:
        connection_one.execute("SET LOCAL lock_timeout = '750ms'")
        connection_one.execute("SET LOCAL statement_timeout = '3s'")
        connection_two.execute("SET LOCAL lock_timeout = '750ms'")
        connection_two.execute("SET LOCAL statement_timeout = '3s'")

        _insert_specification(
            connection_one,
            temporal_fixture.instrument_a,
            "2046-01-01 00:00:00+00",
            "2046-03-01 00:00:00+00",
        )

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                _attempt_and_commit,
                connection_two,
                temporal_fixture.instrument_b,
                "2046-01-01 00:00:00+00",
                "2046-03-01 00:00:00+00",
            )
            result = future.result(timeout=4.0)

        # Connection one is deliberately still uncommitted here. A table-wide
        # lock would have made connection two hit lock_timeout instead.
        assert connection_one.info.transaction_status.name == "INTRANS"
        assert result == AttemptResult(committed=True, sqlstate=None, message=None)

        connection_one.commit()
        assert _specification_count(
            temporal_fixture, temporal_fixture.instrument_a
        ) == 1
        assert _specification_count(
            temporal_fixture, temporal_fixture.instrument_b
        ) == 1
    finally:
        connection_one.rollback()
        connection_two.rollback()
        connection_one.close()
        connection_two.close()


def test_temporal_write_rejects_stale_snapshot_isolation(
    temporal_fixture: TemporalFixture,
) -> None:
    """The documented guard rejects isolation that cannot refresh post-lock."""

    _clear_specifications(temporal_fixture)
    connection = psycopg.connect(
        temporal_fixture.conninfo,
        connect_timeout=5,
        application_name="pit03-repeatable-read-guard",
    )
    connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
    try:
        connection.execute("SET LOCAL statement_timeout = '3s'")
        with pytest.raises(psycopg.Error) as error:
            _insert_specification(
                connection,
                temporal_fixture.instrument_a,
                "2047-01-01 00:00:00+00",
                "2047-02-01 00:00:00+00",
            )

        assert error.value.sqlstate == "0A000"
        assert "requires READ COMMITTED isolation" in str(error.value)
        connection.rollback()
        assert _specification_count(
            temporal_fixture, temporal_fixture.instrument_a
        ) == 0
    finally:
        connection.rollback()
        connection.close()


def test_migration_preflight_rejects_existing_overlap(
    temporal_fixture: TemporalFixture,
) -> None:
    """The migration refuses legacy overlap before reinstalling its guards."""

    _clear_specifications(temporal_fixture)
    connection = _connect(temporal_fixture.conninfo, "pit03-preflight-overlap")
    try:
        connection.execute("SET LOCAL statement_timeout = '10s'")
        connection.execute(
            """
            ALTER TABLE catalog.instrument_spec_version
            DISABLE TRIGGER trg_instrument_spec_version_no_overlap
            """
        )
        _insert_specification(
            connection,
            temporal_fixture.instrument_a,
            "2048-01-01 00:00:00+00",
            "2048-03-01 00:00:00+00",
        )
        _insert_specification(
            connection,
            temporal_fixture.instrument_a,
            "2048-02-01 00:00:00+00",
            "2048-04-01 00:00:00+00",
        )

        with pytest.raises(psycopg.Error) as error:
            connection.execute(read_verified_sql("0003"))

        assert error.value.sqlstate == "23P01"
        assert "catalog.instrument_spec_version" in str(error.value)
        assert f"instrument_id={temporal_fixture.instrument_a}" in str(error.value)
        connection.rollback()

        trigger_state = connection.execute(
            """
            SELECT tgenabled
            FROM pg_trigger
            WHERE tgrelid = 'catalog.instrument_spec_version'::regclass
              AND tgname = 'trg_instrument_spec_version_no_overlap'
            """
        ).fetchone()
        assert trigger_state == ("O",)
        connection.commit()
        assert _specification_count(
            temporal_fixture, temporal_fixture.instrument_a
        ) == 0
    finally:
        connection.rollback()
        connection.close()
