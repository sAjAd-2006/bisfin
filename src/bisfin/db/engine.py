"""Explicit synchronous SQLAlchemy engine lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import create_engine as sqlalchemy_create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import ArgumentError

from bisfin.db.errors import ConfigurationError


class EngineSettings(Protocol):
    """Narrow configuration contract consumed by the database layer."""

    @property
    def sqlalchemy_database_url(self) -> str: ...

    @property
    def database_pool_size(self) -> int: ...

    @property
    def database_max_overflow(self) -> int: ...

    @property
    def database_pool_timeout_seconds(self) -> float: ...

    @property
    def database_statement_timeout_ms(self) -> int: ...

    @property
    def database_application_name(self) -> str: ...


@dataclass(frozen=True, slots=True)
class EngineConfiguration:
    """Non-secret engine settings useful for diagnostics and tests."""

    pool_pre_ping: bool
    pool_size: int
    max_overflow: int
    pool_timeout_seconds: float
    application_name: str
    statement_timeout_ms: int


def get_engine_configuration(settings: EngineSettings) -> EngineConfiguration:
    """Extract the validated, non-secret configuration applied to an engine."""

    return EngineConfiguration(
        pool_pre_ping=True,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout_seconds=settings.database_pool_timeout_seconds,
        application_name=settings.database_application_name,
        statement_timeout_ms=settings.database_statement_timeout_ms,
    )


def create_engine(settings: EngineSettings) -> Engine:
    """Build, but do not connect, a psycopg-backed synchronous engine.

    PostgreSQL's default isolation is deliberately left unchanged.  In this
    project that default is READ COMMITTED, which is required by temporal-write
    trigger functions installed by migration 0003.
    """

    configuration = get_engine_configuration(settings)
    connect_options = f"-c statement_timeout={configuration.statement_timeout_ms}"
    try:
        return sqlalchemy_create_engine(
            settings.sqlalchemy_database_url,
            pool_pre_ping=configuration.pool_pre_ping,
            pool_size=configuration.pool_size,
            max_overflow=configuration.max_overflow,
            pool_timeout=configuration.pool_timeout_seconds,
            connect_args={
                "application_name": configuration.application_name,
                "options": connect_options,
            },
        )
    except (ArgumentError, ValueError, TypeError) as error:
        raise ConfigurationError(
            "Unable to create the PostgreSQL engine from the configured settings."
        ) from error


def dispose_engine(engine: Engine) -> None:
    """Close checked-in pooled connections and invalidate the pool."""

    engine.dispose(close=True)


__all__ = [
    "EngineConfiguration",
    "EngineSettings",
    "create_engine",
    "dispose_engine",
    "get_engine_configuration",
]
