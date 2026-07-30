"""Domain-oriented SQLAlchemy Core repository implementations and protocols."""

from sqlalchemy.engine import Engine

from bisfin.db.unit_of_work import SqlAlchemyUnitOfWorkFactory
from bisfin.repositories.bar_repository import SqlAlchemyBarRepository
from bisfin.repositories.ingestion_batch_repository import (
    SqlAlchemyIngestionBatchRepository,
)
from bisfin.repositories.instrument_repository import SqlAlchemyInstrumentRepository
from bisfin.repositories.protocols import (
    BarRepository,
    IngestionBatchRepository,
    InstrumentRepository,
)


def create_unit_of_work_factory(
    engine: Engine,
) -> SqlAlchemyUnitOfWorkFactory[
    SqlAlchemyInstrumentRepository,
    SqlAlchemyIngestionBatchRepository,
    SqlAlchemyBarRepository,
]:
    """Wire all concrete repositories around each Unit of Work connection."""

    return SqlAlchemyUnitOfWorkFactory(
        engine,
        instruments=SqlAlchemyInstrumentRepository,
        ingestion_batches=SqlAlchemyIngestionBatchRepository,
        bars=SqlAlchemyBarRepository,
    )


__all__ = [
    "BarRepository",
    "IngestionBatchRepository",
    "InstrumentRepository",
    "SqlAlchemyBarRepository",
    "SqlAlchemyIngestionBatchRepository",
    "SqlAlchemyInstrumentRepository",
    "create_unit_of_work_factory",
]
