"""Real-PostgreSQL contract tests for all three repository implementations."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from tests.fixtures import unique_code

from bisfin.db.errors import (
    EntityNotFoundError,
    InvalidPointInTimeQueryError,
    InvalidStateTransitionError,
)
from bisfin.domain.ingestion import IngestionBatchStatus
from bisfin.domain.market_data import ReplayMode
from bisfin.repositories.bar_repository import SqlAlchemyBarRepository
from bisfin.repositories.ingestion_batch_repository import (
    SqlAlchemyIngestionBatchRepository,
)
from bisfin.repositories.instrument_repository import SqlAlchemyInstrumentRepository


def _insert_provider_and_feed(connection: Connection) -> tuple[int, int]:
    provider_code = unique_code("REPO_PROVIDER")
    provider_id = cast(
        int,
        connection.execute(
            text(
                """
                INSERT INTO catalog.data_provider
                    (provider_code, display_name, provider_kind)
                VALUES (:provider_code, :display_name, 'INTERNAL')
                RETURNING provider_id
                """
            ),
            {"provider_code": provider_code, "display_name": provider_code},
        ).scalar_one(),
    )
    feed_code = unique_code("REPO_FEED", max_length=96)
    feed_id = cast(
        int,
        connection.execute(
            text(
                """
                INSERT INTO catalog.data_feed
                    (provider_id, feed_code, display_name, data_kind, parser_version)
                VALUES (:provider_id, :feed_code, :display_name, 'BAR', 'repo-test-v1')
                RETURNING feed_id
                """
            ),
            {
                "provider_id": provider_id,
                "feed_code": feed_code,
                "display_name": feed_code,
            },
        ).scalar_one(),
    )
    return provider_id, feed_id


def _insert_instrument(connection: Connection) -> int:
    symbol = unique_code("REPO_SYMBOL", max_length=128)
    return cast(
        int,
        connection.execute(
            text(
                """
                INSERT INTO catalog.instrument
                    (asset_type_code, quote_currency_code, canonical_symbol, display_name)
                VALUES ('EQUITY', 'USD', :symbol, :display_name)
                RETURNING instrument_id
                """
            ),
            {"symbol": symbol, "display_name": symbol},
        ).scalar_one(),
    )


def test_instrument_repository_historical_resolution_and_spec(
    db_connection: Connection,
) -> None:
    provider_id, _ = _insert_provider_and_feed(db_connection)
    other_provider_id, _ = _insert_provider_and_feed(db_connection)
    instrument_id = _insert_instrument(db_connection)
    other_instrument_id = _insert_instrument(db_connection)
    identifier_value = "0000012345"

    db_connection.execute(
        text(
            """
            INSERT INTO catalog.instrument_identifier
                (provider_id, identifier_type, identifier_value,
                 valid_from, valid_to, instrument_id, is_primary)
            VALUES
                (:provider_id, 'TSETMC_ID', :identifier_value,
                 TIMESTAMPTZ '2024-01-01 00:00:00+00',
                 TIMESTAMPTZ '2025-01-01 00:00:00+00', :instrument_id, TRUE),
                (:provider_id, 'TSETMC_ID', :identifier_value,
                 TIMESTAMPTZ '2025-01-01 00:00:00+00', NULL, :instrument_id, TRUE),
                (:other_provider_id, 'TSETMC_ID', :identifier_value,
                 TIMESTAMPTZ '2024-01-01 00:00:00+00', NULL,
                 :other_instrument_id, TRUE)
            """
        ),
        {
            "provider_id": provider_id,
            "other_provider_id": other_provider_id,
            "identifier_value": identifier_value,
            "instrument_id": instrument_id,
            "other_instrument_id": other_instrument_id,
        },
    )
    # Exercise the database default -infinity and the repository's safe NULL projection.
    db_connection.execute(
        text(
            """
            INSERT INTO catalog.instrument_identifier
                (provider_id, identifier_type, identifier_value, instrument_id)
            VALUES (:provider_id, 'LEGACY_ID', '0000000000', :instrument_id)
            """
        ),
        {"provider_id": provider_id, "instrument_id": instrument_id},
    )
    db_connection.execute(
        text(
            """
            INSERT INTO catalog.instrument_spec_version
                (instrument_id, effective_from, effective_to,
                 price_tick, quantity_step, lot_size, contract_multiplier)
            VALUES
                (:instrument_id, TIMESTAMPTZ '2024-01-01 00:00:00+00',
                 TIMESTAMPTZ '2025-01-01 00:00:00+00', 0.01, 1, 1, 1),
                (:instrument_id, TIMESTAMPTZ '2025-01-01 00:00:00+00',
                 NULL, 0.05, 1, 1, 1)
            """
        ),
        {"instrument_id": instrument_id},
    )
    repository = SqlAlchemyInstrumentRepository(db_connection)

    before_boundary = repository.find_by_identifier(
        provider_id,
        "TSETMC_ID",
        identifier_value,
        datetime(2024, 12, 31, 23, 59, tzinfo=UTC),
    )
    at_boundary = repository.find_by_identifier(
        provider_id,
        "TSETMC_ID",
        identifier_value,
        datetime(2025, 1, 1, tzinfo=UTC),
    )
    separated = repository.find_by_identifier(
        other_provider_id,
        "TSETMC_ID",
        identifier_value,
        datetime(2025, 6, 1, tzinfo=UTC),
    )
    unbounded = repository.find_by_identifier(
        provider_id,
        "LEGACY_ID",
        "0000000000",
        datetime(2025, 6, 1, tzinfo=UTC),
    )

    assert before_boundary is not None
    assert at_boundary is not None
    assert before_boundary.identifier.valid_to == datetime(2025, 1, 1, tzinfo=UTC)
    assert at_boundary.identifier.valid_from == datetime(2025, 1, 1, tzinfo=UTC)
    assert at_boundary.identifier.identifier_value == "0000012345"
    assert separated is not None
    assert separated.instrument.instrument_id == other_instrument_id
    assert unbounded is not None
    assert unbounded.identifier.valid_from is None
    assert (
        repository.find_by_identifier(
            provider_id,
            "TSETMC_ID",
            identifier_value,
            datetime(2023, 12, 31, tzinfo=UTC),
        )
        is None
    )
    assert repository.get_by_id(instrument_id) is not None
    assert repository.get_by_id(9_223_372_036_854_775_000) is None

    old_spec = repository.get_active_spec(instrument_id, datetime(2024, 6, 1, tzinfo=UTC))
    new_spec = repository.get_active_spec(instrument_id, datetime(2025, 1, 1, tzinfo=UTC))
    assert old_spec is not None and old_spec.price_tick == Decimal("0.010000000000000000")
    assert new_spec is not None and new_spec.price_tick == Decimal("0.050000000000000000")
    assert repository.get_active_spec(other_instrument_id, datetime(2025, 1, 1, tzinfo=UTC)) is None


def test_ingestion_batch_lifecycle_failure_and_rollback(db_connection: Connection) -> None:
    _, feed_id = _insert_provider_and_feed(db_connection)
    repository = SqlAlchemyIngestionBatchRepository(db_connection)
    batch = repository.create_batch(
        feed_id=feed_id,
        parser_version="repo-test-v1",
        request_id=unique_code("REQUEST"),
        metadata={"source": "integration"},
    )

    assert batch.status is IngestionBatchStatus.RUNNING
    assert repository.mark_running(batch.ingestion_batch_id).status is IngestionBatchStatus.RUNNING
    completed = repository.mark_succeeded(
        batch.ingestion_batch_id,
        received_row_count=3,
        accepted_row_count=2,
        rejected_row_count=1,
        payload_sha256="a" * 64,
        metadata={"phase": "complete"},
    )
    assert completed.status is IngestionBatchStatus.SUCCEEDED
    assert completed.finished_at is not None
    assert completed.metadata == {"source": "integration", "phase": "complete"}
    with pytest.raises(InvalidStateTransitionError):
        repository.mark_succeeded(
            batch.ingestion_batch_id,
            received_row_count=3,
            accepted_row_count=2,
            rejected_row_count=1,
        )

    failed_batch = repository.create_batch(
        feed_id=feed_id,
        parser_version="repo-test-v1",
        request_id=unique_code("FAILED_REQUEST"),
    )
    failed = repository.mark_failed(
        failed_batch.ingestion_batch_id,
        error_code="SOURCE_FAILURE",
        error_message="authorization=Bearer TOP_SECRET",
        details={"password": "HIDDEN", "endpoint": "https://example.invalid"},
    )
    assert failed.status is IngestionBatchStatus.FAILED
    assert failed.finished_at is not None
    assert "TOP_SECRET" not in repr(failed)
    assert "HIDDEN" not in repr(failed)
    assert isinstance(failed.metadata["failure"], dict)

    savepoint = db_connection.begin_nested()
    rolled_back = repository.create_batch(
        feed_id=feed_id,
        parser_version="repo-test-v1",
        request_id=unique_code("ROLLBACK_REQUEST"),
    )
    savepoint.rollback()
    assert repository.get_by_id(rolled_back.ingestion_batch_id) is None


def test_ingestion_batch_cross_transaction_visibility(db_engine: Engine) -> None:
    with db_engine.begin() as setup:
        provider_id, feed_id = _insert_provider_and_feed(setup)
    batch_id: int | None = None
    try:
        with db_engine.connect() as writer:
            transaction = writer.begin()
            batch = SqlAlchemyIngestionBatchRepository(writer).create_batch(
                feed_id=feed_id,
                parser_version="repo-test-v1",
                request_id=unique_code("VISIBILITY_REQUEST"),
            )
            batch_id = batch.ingestion_batch_id
            with db_engine.connect() as reader:
                assert SqlAlchemyIngestionBatchRepository(reader).get_by_id(batch_id) is None
            transaction.commit()

        with db_engine.connect() as reader:
            visible = SqlAlchemyIngestionBatchRepository(reader).get_by_id(batch_id)
            assert visible is not None
            assert visible.status is IngestionBatchStatus.RUNNING
    finally:
        with db_engine.begin() as cleanup:
            if batch_id is not None:
                cleanup.execute(
                    text(
                        "DELETE FROM ingest.ingestion_batch "
                        "WHERE ingestion_batch_id = :ingestion_batch_id"
                    ),
                    {"ingestion_batch_id": batch_id},
                )
            cleanup.execute(
                text("DELETE FROM catalog.data_feed WHERE feed_id = :feed_id"),
                {"feed_id": feed_id},
            )
            cleanup.execute(
                text("DELETE FROM catalog.data_provider WHERE provider_id = :provider_id"),
                {"provider_id": provider_id},
            )


def _insert_bar_fixture(connection: Connection) -> tuple[int, int]:
    _, feed_id = _insert_provider_and_feed(connection)
    instrument_id = _insert_instrument(connection)
    timeframe_id = cast(
        int,
        connection.execute(
            text("SELECT timeframe_id FROM catalog.timeframe WHERE timeframe_code = '1m'")
        ).scalar_one(),
    )
    batch_id = cast(
        int,
        connection.execute(
            text(
                """
                INSERT INTO ingest.ingestion_batch
                    (feed_id, request_id, parser_version, status, finished_at)
                VALUES (:feed_id, :request_id, 'repo-test-v1', 'SUCCEEDED', CURRENT_TIMESTAMP)
                RETURNING ingestion_batch_id
                """
            ),
            {"feed_id": feed_id, "request_id": unique_code("BAR_REQUEST")},
        ).scalar_one(),
    )
    series_id = cast(
        int,
        connection.execute(
            text(
                """
                INSERT INTO market.bar_series
                    (feed_id, instrument_id, timeframe_id, price_basis, close_semantics)
                VALUES (:feed_id, :instrument_id, :timeframe_id, 'RAW', 'LAST_TRADE')
                RETURNING bar_series_id
                """
            ),
            {
                "feed_id": feed_id,
                "instrument_id": instrument_id,
                "timeframe_id": timeframe_id,
            },
        ).scalar_one(),
    )
    connection.execute(text("SELECT market.create_bar_month_partition(DATE '2092-05-01', 0)"))
    connection.execute(
        text(
            """
            INSERT INTO market.bar_revision
                (bar_open_ts, bar_series_id, revision_no, available_at,
                 system_available_at, bar_close_ts, trading_date,
                 open_price, high_price, low_price, close_price,
                 volume, trade_count, is_final, ingestion_batch_id)
            VALUES
                (TIMESTAMPTZ '2092-05-01 10:00:00+00', :series_id, 1,
                 TIMESTAMPTZ '2092-05-01 10:01:00+00',
                 TIMESTAMPTZ '2092-05-01 10:02:00+00',
                 TIMESTAMPTZ '2092-05-01 10:01:00+00', DATE '2092-05-01',
                 100.123456789012345678, 102, 99, 101.123456789012345678,
                 1000.500000000000000000, 10, TRUE, :batch_id),
                (TIMESTAMPTZ '2092-05-01 10:00:00+00', :series_id, 2,
                 TIMESTAMPTZ '2092-05-01 12:00:00+00',
                 TIMESTAMPTZ '2092-05-01 13:00:00+00',
                 TIMESTAMPTZ '2092-05-01 10:01:00+00', DATE '2092-05-01',
                 100.223456789012345678, 102, 99, 101.223456789012345678,
                 1001.500000000000000000, 11, TRUE, :batch_id),
                (TIMESTAMPTZ '2092-05-01 10:01:00+00', :series_id, 1,
                 TIMESTAMPTZ '2092-05-01 10:02:00+00',
                 TIMESTAMPTZ '2092-05-01 11:30:00+00',
                 TIMESTAMPTZ '2092-05-01 10:02:00+00', DATE '2092-05-01',
                 101, 103, 100, 102.000000000000000001,
                 1100, 12, TRUE, :batch_id),
                (TIMESTAMPTZ '2092-05-01 10:02:00+00', :series_id, 1,
                 TIMESTAMPTZ '2092-05-01 12:00:00+00',
                 TIMESTAMPTZ '2092-05-01 12:30:00+00',
                 TIMESTAMPTZ '2092-05-01 10:03:00+00', DATE '2092-05-01',
                 102, 104, 101, 103, 1200, 13, TRUE, :batch_id)
            """
        ),
        {"series_id": series_id, "batch_id": batch_id},
    )
    return series_id, batch_id


def test_bar_repository_public_system_late_corrections_and_order(
    db_connection: Connection,
) -> None:
    series_id, _ = _insert_bar_fixture(db_connection)
    repository = SqlAlchemyBarRepository(db_connection)
    from_ts = datetime(2092, 5, 1, 10, 0, tzinfo=UTC)
    to_ts = datetime(2092, 5, 1, 10, 2, tzinfo=UTC)

    public = repository.get_bars_as_of(
        series_id,
        from_ts,
        to_ts,
        datetime(2092, 5, 1, 11, 0, tzinfo=UTC),
        ReplayMode.PUBLIC_REPLAY,
    )
    actual = repository.get_bars_as_of(
        series_id,
        from_ts,
        to_ts,
        datetime(2092, 5, 1, 11, 0, tzinfo=UTC),
        ReplayMode.ACTUAL_SYSTEM_REPLAY,
    )
    corrected_public = repository.get_bars_as_of(
        series_id,
        from_ts,
        to_ts,
        datetime(2092, 5, 1, 12, 30, tzinfo=UTC),
        ReplayMode.PUBLIC_REPLAY,
    )
    not_yet_corrected_actual = repository.get_bars_as_of(
        series_id,
        from_ts,
        to_ts,
        datetime(2092, 5, 1, 12, 30, tzinfo=UTC),
        ReplayMode.ACTUAL_SYSTEM_REPLAY,
    )

    assert [bar.bar_open_ts for bar in public] == sorted(bar.bar_open_ts for bar in public)
    assert len(public) == 2
    assert len(actual) == 1
    assert public[0].revision_no == 1
    assert corrected_public[0].revision_no == 2
    assert not_yet_corrected_actual[0].revision_no == 1
    assert public[0].close_price == Decimal("101.123456789012345678")
    assert public[0].volume == Decimal("1000.500000000000000000")
    assert public[0].effective_available_at == public[0].available_at
    assert actual[0].effective_available_at == actual[0].system_available_at
    assert repository.get_series_by_id(series_id) is not None
    assert repository.get_series_by_id(9_223_372_036_854_775_000) is None

    no_look_ahead = repository.get_bars_as_of(
        series_id,
        datetime(2092, 5, 1, 10, 2, tzinfo=UTC),
        datetime(2092, 5, 1, 10, 3, tzinfo=UTC),
        datetime(2092, 5, 1, 11, 0, tzinfo=UTC),
        ReplayMode.PUBLIC_REPLAY,
    )
    assert no_look_ahead == ()


def test_bar_repository_preserves_pit_sqlstates(db_connection: Connection) -> None:
    series_id, _ = _insert_bar_fixture(db_connection)
    repository = SqlAlchemyBarRepository(db_connection)

    with pytest.raises(InvalidPointInTimeQueryError) as invalid:
        with db_connection.begin_nested():
            repository.get_bars_as_of(
                series_id,
                datetime(2092, 5, 1, 10, tzinfo=UTC),
                datetime(2092, 5, 1, 10, tzinfo=UTC),
                datetime(2092, 5, 1, 11, tzinfo=UTC),
                ReplayMode.PUBLIC_REPLAY,
            )
    assert invalid.value.sqlstate == "22023"

    with pytest.raises(EntityNotFoundError) as missing:
        with db_connection.begin_nested():
            repository.get_bars_as_of(
                9_223_372_036_854_775_000,
                datetime(2092, 5, 1, 10, tzinfo=UTC),
                datetime(2092, 5, 1, 10, 1, tzinfo=UTC),
                datetime(2092, 5, 1, 11, tzinfo=UTC),
                ReplayMode.PUBLIC_REPLAY,
            )
    assert missing.value.sqlstate == "P0002"
