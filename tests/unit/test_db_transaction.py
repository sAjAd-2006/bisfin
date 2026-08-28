"""Transaction lifecycle tests with deterministic connection doubles."""

from __future__ import annotations

from typing import Self, cast

import pytest
from sqlalchemy.engine import Connection, Engine

from bisfin.db.errors import UnsupportedDatabaseOperationError
from bisfin.db.transaction import TransactionManager


class _Transaction:
    def __init__(self) -> None:
        self.is_active = True
        self.committed = False
        self.rolled_back = False

    def commit(self) -> None:
        self.committed = True
        self.is_active = False

    def rollback(self) -> None:
        self.rolled_back = True
        self.is_active = False


class _Connection:
    def __init__(self) -> None:
        self.transaction = _Transaction()
        self.isolation_level: str | None = None
        self.statements: list[str] = []
        self.closed = False

    def execution_options(self, *, isolation_level: str) -> Self:
        self.isolation_level = isolation_level
        return self

    def begin(self) -> _Transaction:
        return self.transaction

    def exec_driver_sql(self, statement: str) -> None:
        self.statements.append(statement)

    def close(self) -> None:
        self.closed = True


class _Engine:
    def __init__(self) -> None:
        self.connections: list[_Connection] = []

    def connect(self) -> Connection:
        connection = _Connection()
        self.connections.append(connection)
        return cast("Connection", connection)


class _SentinelError(Exception):
    pass


def _manager() -> tuple[TransactionManager, _Engine]:
    engine = _Engine()
    return TransactionManager(cast("Engine", engine)), engine


def test_success_commits_and_always_closes_connection() -> None:
    manager, engine = _manager()
    with manager.begin() as yielded:
        assert yielded is cast("Connection", engine.connections[0])

    connection = engine.connections[0]
    assert connection.isolation_level == "READ COMMITTED"
    assert connection.transaction.committed is True
    assert connection.transaction.rolled_back is False
    assert connection.closed is True


def test_exception_rolls_back_closes_and_preserves_original() -> None:
    manager, engine = _manager()
    original = _SentinelError("exact object")

    with pytest.raises(_SentinelError) as caught:
        with manager.begin():
            raise original

    assert caught.value is original
    connection = engine.connections[0]
    assert connection.transaction.rolled_back is True
    assert connection.transaction.committed is False
    assert connection.closed is True


def test_read_only_is_local_to_requested_transaction() -> None:
    manager, engine = _manager()
    with manager.begin(isolation_level="REPEATABLE_READ", read_only=True):
        pass
    assert engine.connections[0].isolation_level == "REPEATABLE READ"
    assert engine.connections[0].statements == ["SET TRANSACTION READ ONLY"]


@pytest.mark.parametrize(
    "isolation_level",
    ["READ UNCOMMITTED", "REPEATABLE READ", "SERIALIZABLE"],
)
def test_temporal_write_rejects_stale_snapshot_before_checkout(
    isolation_level: str,
) -> None:
    manager, engine = _manager()
    with pytest.raises(UnsupportedDatabaseOperationError) as caught:
        with manager.begin(isolation_level=isolation_level, temporal_write=True):
            pass
    assert caught.value.sqlstate == "0A000"
    assert engine.connections == []
