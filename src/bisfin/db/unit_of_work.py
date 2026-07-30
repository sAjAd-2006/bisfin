"""Unit of Work lifecycle with injected, connection-bound repositories."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from types import TracebackType
from typing import Protocol, Self, runtime_checkable

from sqlalchemy.engine import Connection, Engine
from sqlalchemy.engine.base import RootTransaction

from bisfin.db.errors import UnitOfWorkLifecycleError


@runtime_checkable
class UnitOfWork[InstrumentRepositoryT, IngestionBatchRepositoryT, BarRepositoryT](Protocol):
    """Small application-facing contract for explicit atomic work."""

    @property
    def instruments(self) -> InstrumentRepositoryT: ...

    @property
    def ingestion_batches(self) -> IngestionBatchRepositoryT: ...

    @property
    def bars(self) -> BarRepositoryT: ...

    def __enter__(self) -> Self: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class RepositoryFactories[InstrumentRepositoryT, IngestionBatchRepositoryT, BarRepositoryT]:
    """Construct repositories around exactly one supplied connection."""

    instruments: Callable[[Connection], InstrumentRepositoryT]
    ingestion_batches: Callable[[Connection], IngestionBatchRepositoryT]
    bars: Callable[[Connection], BarRepositoryT]


class _Lifecycle(Enum):
    NEW = "new"
    ACTIVE = "active"
    COMPLETED = "completed"
    CLOSED = "closed"


class SqlAlchemyUnitOfWork[InstrumentRepositoryT, IngestionBatchRepositoryT, BarRepositoryT]:
    """One SQLAlchemy connection, one transaction, and three repositories."""

    def __init__(
        self,
        engine: Engine,
        factories: RepositoryFactories[
            InstrumentRepositoryT, IngestionBatchRepositoryT, BarRepositoryT
        ],
    ) -> None:
        self._engine = engine
        self._factories = factories
        self._lifecycle = _Lifecycle.NEW
        self._connection: Connection | None = None
        self._transaction: RootTransaction | None = None
        self._instruments: InstrumentRepositoryT | None = None
        self._ingestion_batches: IngestionBatchRepositoryT | None = None
        self._bars: BarRepositoryT | None = None

    def __enter__(self) -> Self:
        if self._lifecycle is not _Lifecycle.NEW:
            raise UnitOfWorkLifecycleError(
                "A Unit of Work is single-use and cannot be nested or re-entered."
            )

        connection = self._engine.connect()
        try:
            # The engine factory intentionally retains PostgreSQL's READ COMMITTED
            # default.  Repositories therefore share that default without a global
            # isolation override.
            transaction = connection.begin()
            self._instruments = self._factories.instruments(connection)
            self._ingestion_batches = self._factories.ingestion_batches(connection)
            self._bars = self._factories.bars(connection)
        except BaseException:
            if connection.in_transaction():
                connection.rollback()
            connection.close()
            self._lifecycle = _Lifecycle.CLOSED
            raise

        self._connection = connection
        self._transaction = transaction
        self._lifecycle = _Lifecycle.ACTIVE
        return self

    @property
    def connection(self) -> Connection:
        """Active shared connection, primarily for composition and diagnostics."""

        self._require_active()
        assert self._connection is not None
        return self._connection

    @property
    def instruments(self) -> InstrumentRepositoryT:
        self._require_active()
        assert self._instruments is not None
        return self._instruments

    @property
    def ingestion_batches(self) -> IngestionBatchRepositoryT:
        self._require_active()
        assert self._ingestion_batches is not None
        return self._ingestion_batches

    @property
    def bars(self) -> BarRepositoryT:
        self._require_active()
        assert self._bars is not None
        return self._bars

    def commit(self) -> None:
        """Commit exactly once; repository methods never commit independently."""

        self._require_active()
        assert self._transaction is not None
        self._transaction.commit()
        self._lifecycle = _Lifecycle.COMPLETED

    def rollback(self) -> None:
        """Explicitly abort active work and mark the unit complete."""

        self._require_active()
        assert self._transaction is not None
        if self._transaction.is_active:
            self._transaction.rollback()
        self._lifecycle = _Lifecycle.COMPLETED

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        if self._lifecycle is _Lifecycle.NEW:
            raise UnitOfWorkLifecycleError("The Unit of Work was never entered.")
        if self._lifecycle is _Lifecycle.CLOSED:
            raise UnitOfWorkLifecycleError("The Unit of Work is already closed.")

        try:
            if self._lifecycle is _Lifecycle.ACTIVE:
                assert self._transaction is not None
                if self._transaction.is_active:
                    self._transaction.rollback()
        finally:
            assert self._connection is not None
            self._connection.close()
            self._lifecycle = _Lifecycle.CLOSED

    @property
    def is_active(self) -> bool:
        return self._lifecycle is _Lifecycle.ACTIVE

    @property
    def is_closed(self) -> bool:
        return self._lifecycle is _Lifecycle.CLOSED

    def _require_active(self) -> None:
        if self._lifecycle is not _Lifecycle.ACTIVE:
            raise UnitOfWorkLifecycleError(
                "The Unit of Work operation requires an active, uncompleted transaction."
            )


class SqlAlchemyUnitOfWorkFactory[InstrumentRepositoryT, IngestionBatchRepositoryT, BarRepositoryT]:
    """Dependency-injectable creator of independent, single-use Units of Work."""

    def __init__(
        self,
        engine: Engine,
        *,
        instruments: Callable[[Connection], InstrumentRepositoryT],
        ingestion_batches: Callable[[Connection], IngestionBatchRepositoryT],
        bars: Callable[[Connection], BarRepositoryT],
    ) -> None:
        self._engine = engine
        self._factories = RepositoryFactories(
            instruments=instruments,
            ingestion_batches=ingestion_batches,
            bars=bars,
        )

    def create(
        self,
    ) -> SqlAlchemyUnitOfWork[InstrumentRepositoryT, IngestionBatchRepositoryT, BarRepositoryT]:
        return SqlAlchemyUnitOfWork(self._engine, self._factories)

    def __call__(
        self,
    ) -> SqlAlchemyUnitOfWork[InstrumentRepositoryT, IngestionBatchRepositoryT, BarRepositoryT]:
        return self.create()


__all__ = [
    "RepositoryFactories",
    "SqlAlchemyUnitOfWork",
    "SqlAlchemyUnitOfWorkFactory",
    "UnitOfWork",
]
