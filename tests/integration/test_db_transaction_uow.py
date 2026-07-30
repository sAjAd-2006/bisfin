"""Real PostgreSQL transaction and Unit of Work lifecycle checks."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.pool import QueuePool

from bisfin.db.errors import UnsupportedDatabaseOperationError
from bisfin.db.transaction import TransactionManager
from bisfin.db.unit_of_work import SqlAlchemyUnitOfWorkFactory


def _asset_code(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


def _delete_asset_type(engine: Engine, asset_type_code: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM catalog.asset_type WHERE asset_type_code = :code"),
            {"code": asset_type_code},
        )


def _asset_exists(engine: Engine, asset_type_code: str) -> bool:
    with engine.connect() as connection:
        return bool(
            connection.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM catalog.asset_type WHERE asset_type_code = :code)"
                ),
                {"code": asset_type_code},
            ).scalar_one()
        )


def test_transaction_manager_commit_rollback_isolation_and_connection_reuse(
    db_engine: Engine,
) -> None:
    manager = TransactionManager(db_engine)
    committed_code = _asset_code("TXCOMMIT")
    rolled_back_code = _asset_code("TXROLLBACK")
    first_pid: int
    try:
        with manager.begin() as connection:
            first_pid = int(connection.execute(text("SELECT pg_backend_pid()")).scalar_one())
            isolation = str(connection.execute(text("SHOW transaction_isolation")).scalar_one())
            connection.execute(
                text(
                    "INSERT INTO catalog.asset_type "
                    "(asset_type_code, display_name) VALUES (:code, :name)"
                ),
                {"code": committed_code, "name": "transaction commit fixture"},
            )
        assert isolation == "read committed"
        assert _asset_exists(db_engine, committed_code) is True

        original = RuntimeError("preserve exact exception")
        with pytest.raises(RuntimeError) as caught:
            with manager.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO catalog.asset_type "
                        "(asset_type_code, display_name) VALUES (:code, :name)"
                    ),
                    {"code": rolled_back_code, "name": "transaction rollback fixture"},
                )
                raise original
        assert caught.value is original
        assert _asset_exists(db_engine, rolled_back_code) is False

        with manager.begin(read_only=True) as connection:
            second_pid = int(connection.execute(text("SELECT pg_backend_pid()")).scalar_one())
        assert second_pid == first_pid
        assert isinstance(db_engine.pool, QueuePool)
        assert db_engine.pool.checkedout() == 0
    finally:
        _delete_asset_type(db_engine, committed_code)
        _delete_asset_type(db_engine, rolled_back_code)


def test_temporal_write_rejects_invalid_isolation_without_pool_leak(
    db_engine: Engine,
) -> None:
    manager = TransactionManager(db_engine)
    assert isinstance(db_engine.pool, QueuePool)
    checked_out_before = db_engine.pool.checkedout()
    with pytest.raises(UnsupportedDatabaseOperationError) as caught:
        with manager.begin(isolation_level="SERIALIZABLE", temporal_write=True):
            pass
    assert caught.value.sqlstate == "0A000"
    assert db_engine.pool.checkedout() == checked_out_before


@dataclass(frozen=True, slots=True)
class _AssetRepository:
    connection: Connection

    def add(self, asset_type_code: str) -> None:
        self.connection.execute(
            text(
                "INSERT INTO catalog.asset_type "
                "(asset_type_code, display_name) VALUES (:code, :name)"
            ),
            {"code": asset_type_code, "name": "unit of work fixture"},
        )


def test_unit_of_work_controls_cross_transaction_visibility(db_engine: Engine) -> None:
    factory = SqlAlchemyUnitOfWorkFactory(
        db_engine,
        instruments=_AssetRepository,
        ingestion_batches=_AssetRepository,
        bars=_AssetRepository,
    )
    committed_code = _asset_code("UOWCOMMIT")
    rolled_back_code = _asset_code("UOWROLLBACK")
    try:
        with factory() as unit_of_work:
            assert unit_of_work.instruments.connection is unit_of_work.bars.connection
            unit_of_work.instruments.add(committed_code)
            # An independent transaction cannot see the uncommitted insert.
            assert _asset_exists(db_engine, committed_code) is False
            unit_of_work.commit()
        assert _asset_exists(db_engine, committed_code) is True

        with factory() as unit_of_work:
            unit_of_work.ingestion_batches.add(rolled_back_code)
        assert _asset_exists(db_engine, rolled_back_code) is False
    finally:
        _delete_asset_type(db_engine, committed_code)
        _delete_asset_type(db_engine, rolled_back_code)
