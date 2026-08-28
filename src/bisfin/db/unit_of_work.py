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
class UnitOfWork[
    DataFeedRepositoryT,
    InstrumentRepositoryT,
    IngestionBatchRepositoryT,
    RawEventRepositoryT,
    BarRepositoryT,
    BarWriterRepositoryT,
    CatalogWriterRepositoryT,
    TradingCalendarRepositoryT,
](Protocol):
    """Small application-facing contract for explicit atomic work."""

    @property
    def data_feeds(self) -> DataFeedRepositoryT: ...

    @property
    def instruments(self) -> InstrumentRepositoryT: ...

    @property
    def ingestion_batches(self) -> IngestionBatchRepositoryT: ...

    @property
    def raw_events(self) -> RawEventRepositoryT: ...

    @property
    def bars(self) -> BarRepositoryT: ...

    @property
    def bar_writer(self) -> BarWriterRepositoryT: ...

    @property
    def catalog_writer(self) -> CatalogWriterRepositoryT: ...

    @property
    def trading_calendar(self) -> TradingCalendarRepositoryT: ...

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
class RepositoryFactories[
    DataFeedRepositoryT,
    InstrumentRepositoryT,
    IngestionBatchRepositoryT,
    RawEventRepositoryT,
    BarRepositoryT,
    BarWriterRepositoryT,
    CatalogWriterRepositoryT,
    TradingCalendarRepositoryT,
]:
    """Construct repositories around exactly one supplied connection."""

    data_feeds: Callable[[Connection], DataFeedRepositoryT]
    instruments: Callable[[Connection], InstrumentRepositoryT]
    ingestion_batches: Callable[[Connection], IngestionBatchRepositoryT]
    raw_events: Callable[[Connection], RawEventRepositoryT]
    bars: Callable[[Connection], BarRepositoryT]
    bar_writer: Callable[[Connection], BarWriterRepositoryT]
    catalog_writer: Callable[[Connection], CatalogWriterRepositoryT]
    trading_calendar: Callable[[Connection], TradingCalendarRepositoryT]


class _Lifecycle(Enum):
    NEW = "new"
    ACTIVE = "active"
    COMPLETED = "completed"
    CLOSED = "closed"


class SqlAlchemyUnitOfWork[
    DataFeedRepositoryT,
    InstrumentRepositoryT,
    IngestionBatchRepositoryT,
    RawEventRepositoryT,
    BarRepositoryT,
    BarWriterRepositoryT,
    CatalogWriterRepositoryT,
    TradingCalendarRepositoryT,
]:
    """One SQLAlchemy connection, one transaction, and typed repositories."""

    def __init__(
        self,
        engine: Engine,
        factories: RepositoryFactories[
            DataFeedRepositoryT,
            InstrumentRepositoryT,
            IngestionBatchRepositoryT,
            RawEventRepositoryT,
            BarRepositoryT,
            BarWriterRepositoryT,
            CatalogWriterRepositoryT,
            TradingCalendarRepositoryT,
        ],
        *,
        temporal_write: bool = False,
    ) -> None:
        self._engine = engine
        self._factories = factories
        self._temporal_write = temporal_write
        self._lifecycle = _Lifecycle.NEW
        self._connection: Connection | None = None
        self._transaction: RootTransaction | None = None
        self._data_feeds: DataFeedRepositoryT | None = None
        self._instruments: InstrumentRepositoryT | None = None
        self._ingestion_batches: IngestionBatchRepositoryT | None = None
        self._raw_events: RawEventRepositoryT | None = None
        self._bars: BarRepositoryT | None = None
        self._bar_writer: BarWriterRepositoryT | None = None
        self._catalog_writer: CatalogWriterRepositoryT | None = None
        self._trading_calendar: TradingCalendarRepositoryT | None = None

    def __enter__(self) -> Self:
        if self._lifecycle is not _Lifecycle.NEW:
            raise UnitOfWorkLifecycleError(
                "A Unit of Work is single-use and cannot be nested or re-entered."
            )

        connection = self._engine.connect()
        try:
            if self._temporal_write:
                # Migration 0003 temporal triggers require this exact isolation
                # level after their transaction advisory locks are acquired.
                connection = connection.execution_options(isolation_level="READ COMMITTED")
            transaction = connection.begin()
            self._data_feeds = self._factories.data_feeds(connection)
            self._instruments = self._factories.instruments(connection)
            self._ingestion_batches = self._factories.ingestion_batches(connection)
            self._raw_events = self._factories.raw_events(connection)
            self._bars = self._factories.bars(connection)
            self._bar_writer = self._factories.bar_writer(connection)
            self._catalog_writer = self._factories.catalog_writer(connection)
            self._trading_calendar = self._factories.trading_calendar(connection)
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
    def data_feeds(self) -> DataFeedRepositoryT:
        self._require_active()
        assert self._data_feeds is not None
        return self._data_feeds

    @property
    def ingestion_batches(self) -> IngestionBatchRepositoryT:
        self._require_active()
        assert self._ingestion_batches is not None
        return self._ingestion_batches

    @property
    def raw_events(self) -> RawEventRepositoryT:
        self._require_active()
        assert self._raw_events is not None
        return self._raw_events

    @property
    def bars(self) -> BarRepositoryT:
        self._require_active()
        assert self._bars is not None
        return self._bars

    @property
    def bar_writer(self) -> BarWriterRepositoryT:
        self._require_active()
        assert self._bar_writer is not None
        return self._bar_writer

    @property
    def catalog_writer(self) -> CatalogWriterRepositoryT:
        self._require_active()
        assert self._catalog_writer is not None
        return self._catalog_writer

    @property
    def trading_calendar(self) -> TradingCalendarRepositoryT:
        self._require_active()
        assert self._trading_calendar is not None
        return self._trading_calendar

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


class SqlAlchemyUnitOfWorkFactory[
    DataFeedRepositoryT,
    InstrumentRepositoryT,
    IngestionBatchRepositoryT,
    RawEventRepositoryT,
    BarRepositoryT,
    BarWriterRepositoryT,
    CatalogWriterRepositoryT,
    TradingCalendarRepositoryT,
]:
    """Dependency-injectable creator of independent, single-use Units of Work."""

    def __init__(
        self,
        engine: Engine,
        *,
        data_feeds: Callable[[Connection], DataFeedRepositoryT],
        instruments: Callable[[Connection], InstrumentRepositoryT],
        ingestion_batches: Callable[[Connection], IngestionBatchRepositoryT],
        raw_events: Callable[[Connection], RawEventRepositoryT],
        bars: Callable[[Connection], BarRepositoryT],
        bar_writer: Callable[[Connection], BarWriterRepositoryT],
        catalog_writer: Callable[[Connection], CatalogWriterRepositoryT],
        trading_calendar: Callable[[Connection], TradingCalendarRepositoryT],
    ) -> None:
        self._engine = engine
        self._factories = RepositoryFactories(
            data_feeds=data_feeds,
            instruments=instruments,
            ingestion_batches=ingestion_batches,
            raw_events=raw_events,
            bars=bars,
            bar_writer=bar_writer,
            catalog_writer=catalog_writer,
            trading_calendar=trading_calendar,
        )

    def create(
        self,
    ) -> SqlAlchemyUnitOfWork[
        DataFeedRepositoryT,
        InstrumentRepositoryT,
        IngestionBatchRepositoryT,
        RawEventRepositoryT,
        BarRepositoryT,
        BarWriterRepositoryT,
        CatalogWriterRepositoryT,
        TradingCalendarRepositoryT,
    ]:
        return SqlAlchemyUnitOfWork(self._engine, self._factories)

    def create_temporal_write(
        self,
    ) -> SqlAlchemyUnitOfWork[
        DataFeedRepositoryT,
        InstrumentRepositoryT,
        IngestionBatchRepositoryT,
        RawEventRepositoryT,
        BarRepositoryT,
        BarWriterRepositoryT,
        CatalogWriterRepositoryT,
        TradingCalendarRepositoryT,
    ]:
        """Create a UoW that explicitly uses READ COMMITTED for temporal writes."""

        return SqlAlchemyUnitOfWork(self._engine, self._factories, temporal_write=True)

    def __call__(
        self,
    ) -> SqlAlchemyUnitOfWork[
        DataFeedRepositoryT,
        InstrumentRepositoryT,
        IngestionBatchRepositoryT,
        RawEventRepositoryT,
        BarRepositoryT,
        BarWriterRepositoryT,
        CatalogWriterRepositoryT,
        TradingCalendarRepositoryT,
    ]:
        return self.create()


__all__ = [
    "RepositoryFactories",
    "SqlAlchemyUnitOfWork",
    "SqlAlchemyUnitOfWorkFactory",
    "UnitOfWork",
]
