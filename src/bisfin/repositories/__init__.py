"""Domain-oriented SQLAlchemy Core repository implementations and protocols."""

from sqlalchemy.engine import Engine

from bisfin.db.unit_of_work import SqlAlchemyUnitOfWorkFactory
from bisfin.repositories.backtest_ledger_repository import SqlAlchemyBacktestLedgerRepository
from bisfin.repositories.backtest_run_repository import SqlAlchemyBacktestRunRepository
from bisfin.repositories.bar_repository import SqlAlchemyBarRepository
from bisfin.repositories.bar_writer_repository import SqlAlchemyBarWriterRepository
from bisfin.repositories.catalog_writer_repository import SqlAlchemyCatalogWriterRepository
from bisfin.repositories.data_feed_repository import SqlAlchemyDataFeedRepository
from bisfin.repositories.ingestion_batch_repository import (
    SqlAlchemyIngestionBatchRepository,
)
from bisfin.repositories.instrument_repository import SqlAlchemyInstrumentRepository
from bisfin.repositories.protocols import (
    BarRepository,
    BarWriterRepository,
    DataFeedRepository,
    IngestionBatchRepository,
    InstrumentRepository,
    RawEventRepository,
)
from bisfin.repositories.raw_event_repository import SqlAlchemyRawEventRepository
from bisfin.repositories.trading_calendar_repository import SqlAlchemyTradingCalendarRepository


def create_unit_of_work_factory(
    engine: Engine,
) -> SqlAlchemyUnitOfWorkFactory[
    SqlAlchemyDataFeedRepository,
    SqlAlchemyInstrumentRepository,
    SqlAlchemyIngestionBatchRepository,
    SqlAlchemyRawEventRepository,
    SqlAlchemyBarRepository,
    SqlAlchemyBarWriterRepository,
    SqlAlchemyCatalogWriterRepository,
    SqlAlchemyTradingCalendarRepository,
]:
    """Wire all concrete repositories around each Unit of Work connection."""

    return SqlAlchemyUnitOfWorkFactory(
        engine,
        data_feeds=SqlAlchemyDataFeedRepository,
        instruments=SqlAlchemyInstrumentRepository,
        ingestion_batches=SqlAlchemyIngestionBatchRepository,
        raw_events=SqlAlchemyRawEventRepository,
        bars=SqlAlchemyBarRepository,
        bar_writer=SqlAlchemyBarWriterRepository,
        catalog_writer=SqlAlchemyCatalogWriterRepository,
        trading_calendar=SqlAlchemyTradingCalendarRepository,
    )


__all__ = [
    "BarRepository",
    "BarWriterRepository",
    "DataFeedRepository",
    "IngestionBatchRepository",
    "InstrumentRepository",
    "RawEventRepository",
    "SqlAlchemyBarRepository",
    "SqlAlchemyBarWriterRepository",
    "SqlAlchemyBacktestLedgerRepository",
    "SqlAlchemyBacktestRunRepository",
    "SqlAlchemyCatalogWriterRepository",
    "SqlAlchemyDataFeedRepository",
    "SqlAlchemyIngestionBatchRepository",
    "SqlAlchemyInstrumentRepository",
    "SqlAlchemyRawEventRepository",
    "SqlAlchemyTradingCalendarRepository",
    "create_unit_of_work_factory",
]
