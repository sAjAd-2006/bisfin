"""Shared marker policy and real-PostgreSQL fixtures for the application tests."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Connection, Engine

from bisfin.config import Settings
from bisfin.db.engine import create_engine, dispose_engine


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Classify tests by directory and guard real database tests explicitly."""

    integration_enabled = os.getenv("BISFIN_RUN_DB_INTEGRATION") == "1"
    for item in items:
        path_parts = item.path.parts
        if "integration" in path_parts:
            item.add_marker(pytest.mark.integration)
            if not integration_enabled:
                item.add_marker(
                    pytest.mark.skip(
                        reason=(
                            "set BISFIN_RUN_DB_INTEGRATION=1 to run PostgreSQL integration tests"
                        )
                    )
                )
        elif "unit" in path_parts:
            item.add_marker(pytest.mark.unit)


@pytest.fixture(scope="session")
def db_settings() -> Settings:
    """Load the same typed settings used by the application CLI."""

    return Settings()


@pytest.fixture(scope="session")
def db_engine(db_settings: Settings) -> Iterator[Engine]:
    """Share one explicit Engine while still isolating every Connection."""

    engine = create_engine(db_settings)
    try:
        yield engine
    finally:
        dispose_engine(engine)


@pytest.fixture
def db_connection(db_engine: Engine) -> Iterator[Connection]:
    """Roll back all fixture rows created through one integration-test connection."""

    with db_engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            if transaction.is_active:
                transaction.rollback()
