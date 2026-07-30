"""Typed, synchronous PostgreSQL primitives for Bisfin."""

from bisfin.db.engine import create_engine, dispose_engine
from bisfin.db.errors import (
    BisfinError,
    ConfigurationError,
    DatabaseError,
    DatabaseUnavailableError,
    EntityNotFoundError,
    IntegrityViolationError,
    InvalidPointInTimeQueryError,
    InvalidStateTransitionError,
    RepositoryError,
    TemporalOverlapError,
    UnitOfWorkLifecycleError,
    UnsupportedDatabaseOperationError,
    map_database_error,
)
from bisfin.db.health import DatabaseHealthChecker, DatabaseHealthReport, HealthCheckResult
from bisfin.db.transaction import TransactionManager
from bisfin.db.unit_of_work import (
    RepositoryFactories,
    SqlAlchemyUnitOfWork,
    SqlAlchemyUnitOfWorkFactory,
    UnitOfWork,
)

__all__ = [
    "BisfinError",
    "ConfigurationError",
    "DatabaseError",
    "DatabaseHealthChecker",
    "DatabaseHealthReport",
    "DatabaseUnavailableError",
    "EntityNotFoundError",
    "HealthCheckResult",
    "IntegrityViolationError",
    "InvalidPointInTimeQueryError",
    "InvalidStateTransitionError",
    "RepositoryError",
    "RepositoryFactories",
    "SqlAlchemyUnitOfWork",
    "SqlAlchemyUnitOfWorkFactory",
    "TemporalOverlapError",
    "TransactionManager",
    "UnitOfWork",
    "UnitOfWorkLifecycleError",
    "UnsupportedDatabaseOperationError",
    "create_engine",
    "dispose_engine",
    "map_database_error",
]
