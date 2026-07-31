"""Single-use Unit of Work lifecycle and atomicity tests."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.pool import StaticPool

from bisfin.db.errors import UnitOfWorkLifecycleError
from bisfin.db.unit_of_work import SqlAlchemyUnitOfWorkFactory


@dataclass(frozen=True, slots=True)
class _Repository:
    connection: Connection

    def add(self, value: str) -> None:
        self.connection.execute(
            text("INSERT INTO uow_values (value) VALUES (:value)"),
            {"value": value},
        )


@pytest.fixture
def engine() -> Iterator[Engine]:
    database = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
    )
    with database.begin() as connection:
        connection.execute(text("CREATE TABLE uow_values (value VARCHAR(64) NOT NULL)"))
    try:
        yield database
    finally:
        database.dispose()


def _factory(
    engine: Engine,
) -> SqlAlchemyUnitOfWorkFactory[
    _Repository,
    _Repository,
    _Repository,
    _Repository,
    _Repository,
    _Repository,
]:
    return SqlAlchemyUnitOfWorkFactory(
        engine,
        data_feeds=_Repository,
        instruments=_Repository,
        ingestion_batches=_Repository,
        raw_events=_Repository,
        bars=_Repository,
        bar_writer=_Repository,
    )


def _values(engine: Engine) -> list[str]:
    with engine.connect() as connection:
        return list(connection.execute(text("SELECT value FROM uow_values")).scalars())


def test_repositories_share_one_connection_and_explicit_commit(engine: Engine) -> None:
    with _factory(engine)() as unit_of_work:
        assert unit_of_work.instruments.connection is unit_of_work.connection
        assert unit_of_work.data_feeds.connection is unit_of_work.connection
        assert unit_of_work.ingestion_batches.connection is unit_of_work.connection
        assert unit_of_work.raw_events.connection is unit_of_work.connection
        assert unit_of_work.bars.connection is unit_of_work.connection
        assert unit_of_work.bar_writer.connection is unit_of_work.connection
        unit_of_work.bars.add("committed")
        unit_of_work.commit()

    assert _values(engine) == ["committed"]
    assert unit_of_work.is_closed is True


def test_exit_without_commit_rolls_back(engine: Engine) -> None:
    with _factory(engine)() as unit_of_work:
        unit_of_work.instruments.add("must roll back")
    assert _values(engine) == []


def test_exception_rolls_back_and_is_not_swallowed(engine: Engine) -> None:
    original = RuntimeError("preserve me")
    with pytest.raises(RuntimeError) as caught:
        with _factory(engine)() as unit_of_work:
            unit_of_work.ingestion_batches.add("must roll back")
            raise original
    assert caught.value is original
    assert _values(engine) == []


def test_explicit_rollback_completes_transaction(engine: Engine) -> None:
    with _factory(engine)() as unit_of_work:
        unit_of_work.bars.add("must roll back")
        unit_of_work.rollback()
        with pytest.raises(UnitOfWorkLifecycleError):
            unit_of_work.commit()
    assert _values(engine) == []


def test_nested_use_and_reuse_after_close_are_rejected(engine: Engine) -> None:
    unit_of_work = _factory(engine)()
    with unit_of_work:
        with pytest.raises(UnitOfWorkLifecycleError):
            with unit_of_work:
                pass
    with pytest.raises(UnitOfWorkLifecycleError):
        with unit_of_work:
            pass


def test_factory_creates_independent_single_use_instances(engine: Engine) -> None:
    factory = _factory(engine)
    assert factory() is not factory()
