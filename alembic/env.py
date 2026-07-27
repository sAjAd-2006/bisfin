"""Alembic environment for checksum-verified raw PostgreSQL migrations."""

from __future__ import annotations

from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.pool import NullPool

from alembic import context
from migration_registry import (
    AdvisoryLockError,
    build_database_url,
    validate_registry,
)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ADVISORY_LOCK_KEY = 740_113_006_001


def _acquire_advisory_lock(connection: Connection) -> None:
    with connection.begin():
        acquired = bool(
            connection.execute(
                text("SELECT pg_try_advisory_lock(:lock_key)"),
                {"lock_key": ADVISORY_LOCK_KEY},
            ).scalar_one()
        )
    if not acquired:
        raise AdvisoryLockError(
            "Another Alembic migration run holds the Bisfin advisory lock."
        )


def _release_advisory_lock(connection: Connection) -> None:
    if connection.in_transaction():
        connection.rollback()
    with connection.begin():
        released = bool(
            connection.execute(
                text("SELECT pg_advisory_unlock(:lock_key)"),
                {"lock_key": ADVISORY_LOCK_KEY},
            ).scalar_one()
        )
    if not released:
        raise AdvisoryLockError("The Bisfin migration advisory lock was not held.")


def run_migrations_online() -> None:
    """Run registered migrations through one locked synchronous connection."""

    validate_registry(REPOSITORY_ROOT)
    database_url = build_database_url()
    engine = create_engine(database_url, poolclass=NullPool)

    try:
        with engine.connect() as connection:
            _acquire_advisory_lock(connection)
            try:
                context.configure(
                    connection=connection,
                    target_metadata=None,
                    transaction_per_migration=True,
                    version_table="alembic_version",
                    version_table_pk=True,
                )
                with context.begin_transaction():
                    context.run_migrations()
            finally:
                _release_advisory_lock(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    raise RuntimeError(
        "Offline Alembic mode is unsupported because migrations execute "
        "checksum-verified raw SQL files."
    )

run_migrations_online()
