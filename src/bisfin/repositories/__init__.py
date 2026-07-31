"""Domain-oriented SQLAlchemy Core repository implementations and protocols."""

from sqlalchemy.engine import Engine

from bisfin.db.unit_of_work import SqlAlchemyUnitOfWorkFactory
from bisfin.repositories.bar_repository import SqlAlchemyBarRepository
from bisfin.repositories.bar_writer_repository import SqlAlchemyBarWriterRepository
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


def create_unit_of_work_factory(
    engine: Engine,
) -> SqlAlchemyUnitOfWorkFactory[
    SqlAlchemyDataFeedRepository,
    SqlAlchemyInstrumentRepository,
    SqlAlchemyIngestionBatchRepository,
    SqlAlchemyRawEventRepository,
    SqlAlchemyBarRepository,
    SqlAlchemyBarWriterRepository,
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
    "SqlAlchemyDataFeedRepository",
    "SqlAlchemyIngestionBatchRepository",
    "SqlAlchemyInstrumentRepository",
    "SqlAlchemyRawEventRepository",
    "create_unit_of_work_factory",
]
