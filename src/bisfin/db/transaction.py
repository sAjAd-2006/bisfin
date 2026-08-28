"""Explicit SQLAlchemy transaction boundaries for worker and CLI workloads."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Final, Literal, cast

from sqlalchemy.engine import Connection, Engine

from bisfin.db.errors import UnsupportedDatabaseOperationError

type IsolationLevel = Literal[
    "READ COMMITTED",
    "READ UNCOMMITTED",
    "REPEATABLE READ",
    "SERIALIZABLE",
]

DEFAULT_ISOLATION_LEVEL: Final[IsolationLevel] = "READ COMMITTED"
_SUPPORTED_ISOLATION_LEVELS: Final[frozenset[str]] = frozenset(
    {"READ COMMITTED", "READ UNCOMMITTED", "REPEATABLE READ", "SERIALIZABLE"}
)


def _normalize_isolation_level(isolation_level: str) -> IsolationLevel:
    normalized = " ".join(isolation_level.upper().replace("_", " ").split())
    if normalized not in _SUPPORTED_ISOLATION_LEVELS:
        raise UnsupportedDatabaseOperationError(
            "Unsupported transaction isolation level.",
            operation="begin transaction",
        )
    return cast("IsolationLevel", normalized)


class TransactionManager:
    """Own one engine and vend short-lived, explicit transactions."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @contextmanager
    def begin(
        self,
        *,
        isolation_level: IsolationLevel | str = DEFAULT_ISOLATION_LEVEL,
        temporal_write: bool = False,
        read_only: bool = False,
    ) -> Iterator[Connection]:
        """Yield a connection, committing success and rolling back any failure.

        ``temporal_write`` must be selected by callers that mutate any catalog
        table protected by migration 0003.  Its trigger functions require a
        fresh READ COMMITTED snapshot after their per-key advisory lock.
        """

        normalized = _normalize_isolation_level(isolation_level)
        if temporal_write and normalized != DEFAULT_ISOLATION_LEVEL:
            raise UnsupportedDatabaseOperationError(
                "Temporal catalog writes require READ COMMITTED isolation.",
                sqlstate="0A000",
                operation="begin temporal catalog write",
            )
        if temporal_write and read_only:
            raise UnsupportedDatabaseOperationError(
                "A temporal write transaction cannot be read-only.",
                operation="begin temporal catalog write",
            )

        connection = self._engine.connect()
        transaction = None
        try:
            connection = connection.execution_options(isolation_level=normalized)
            transaction = connection.begin()
            if read_only:
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            try:
                yield connection
            except BaseException:
                if transaction.is_active:
                    transaction.rollback()
                raise
            else:
                transaction.commit()
        finally:
            if transaction is not None and transaction.is_active:
                transaction.rollback()
            connection.close()

    def begin_temporal_write(self) -> AbstractContextManager[Connection]:
        """Return the READ COMMITTED context manager for protected catalog writes."""

        return self.begin(temporal_write=True)


__all__ = ["DEFAULT_ISOLATION_LEVEL", "IsolationLevel", "TransactionManager"]
