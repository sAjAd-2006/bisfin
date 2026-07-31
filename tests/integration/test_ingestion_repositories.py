"""Real PostgreSQL contracts for PR-05 catalog, raw-event, and bar writes."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from decimal import Decimal
from threading import Barrier
from typing import cast
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from tests.fixtures import unique_code

from bisfin.domain.ingestion import IngestionBatchStatus, RawEventValidationStatus
from bisfin.domain.market_data import BarRevisionCandidate, BarRevisionWriteStatus
from bisfin.repositories.bar_writer_repository import SqlAlchemyBarWriterRepository
from bisfin.repositories.data_feed_repository import SqlAlchemyDataFeedRepository
from bisfin.repositories.ingestion_batch_repository import (
    SqlAlchemyIngestionBatchRepository,
)
from bisfin.repositories.instrument_repository import SqlAlchemyInstrumentRepository
from bisfin.repositories.raw_event_repository import SqlAlchemyRawEventRepository


def _catalog_fixture(connection: Connection) -> tuple[int, int, int, int]:
    provider_code = unique_code("BRSAPI_REPO", max_length=64)
    provider_id = cast(
        int,
        connection.execute(
            text(
                """
                INSERT INTO catalog.data_provider
                    (provider_code, display_name, provider_kind, default_timezone)
                VALUES (:code, :display_name, 'PUBLIC', 'Asia/Tehran')
                RETURNING provider_id
                """
            ),
            {"code": provider_code, "display_name": provider_code},
        ).scalar_one(),
    )
    feed_code = unique_code("DAILY_RAW", max_length=96)
    feed_id = cast(
        int,
        connection.execute(
            text(
                """
                INSERT INTO catalog.data_feed
                    (provider_id, feed_code, display_name, data_kind, parser_version)
                VALUES (:provider_id, :code, :display_name, 'BAR', 'brsapi-test-v1')
                RETURNING feed_id
                """
            ),
            {
                "provider_id": provider_id,
                "code": feed_code,
                "display_name": feed_code,
            },
        ).scalar_one(),
    )
    venue_code = f"TSE_{uuid4().hex[:20].upper()}"
    venue_id = cast(
        int,
        connection.execute(
            text(
                """
                INSERT INTO catalog.venue
                    (venue_code, display_name, timezone_name, base_currency_code)
                VALUES (:code, :display_name, 'Asia/Tehran', 'IRR')
                RETURNING venue_id
                """
            ),
            {"code": venue_code, "display_name": venue_code},
        ).scalar_one(),
    )
    symbol = unique_code("INSTRUMENT", max_length=128)
    instrument_id = cast(
        int,
        connection.execute(
            text(
                """
                INSERT INTO catalog.instrument
                    (asset_type_code, venue_id, quote_currency_code,
                     canonical_symbol, display_name)
                VALUES ('EQUITY', :venue_id, 'IRR', :symbol, :display_name)
                RETURNING instrument_id
                """
            ),
            {"venue_id": venue_id, "symbol": symbol, "display_name": symbol},
        ).scalar_one(),
    )
    connection.execute(
        text(
            """
            INSERT INTO catalog.instrument_identifier
                (provider_id, identifier_type, identifier_value,
                 valid_from, instrument_id, is_primary)
            VALUES (:provider_id, 'BRSAPI_L18', '۰۰۰فملی',
                    TIMESTAMPTZ '2088-01-01 00:00:00+00', :instrument_id, TRUE)
            """
        ),
        {"provider_id": provider_id, "instrument_id": instrument_id},
    )
    connection.execute(
        text(
            """
            INSERT INTO catalog.trading_session
                (venue_id, trading_date, session_code, is_trading_day,
                 session_open_ts, session_close_ts)
            VALUES (:venue_id, DATE '2088-07-01', 'REGULAR', TRUE,
                    TIMESTAMPTZ '2088-07-01 05:30:00+00',
                    TIMESTAMPTZ '2088-07-01 09:00:00+00')
            """
        ),
        {"venue_id": venue_id},
    )
    return provider_id, feed_id, venue_id, instrument_id


def test_ingestion_repository_vertical_persistence_contract(
    db_connection: Connection,
) -> None:
    provider_id, feed_id, venue_id, instrument_id = _catalog_fixture(db_connection)
    catalog = SqlAlchemyDataFeedRepository(db_connection)
    instruments = SqlAlchemyInstrumentRepository(db_connection)
    batches = SqlAlchemyIngestionBatchRepository(db_connection)
    raw_events = SqlAlchemyRawEventRepository(db_connection)
    writer = SqlAlchemyBarWriterRepository(db_connection)

    provider = catalog.get_provider_by_code(
        str(
            db_connection.execute(
                text("SELECT provider_code FROM catalog.data_provider WHERE provider_id=:id"),
                {"id": provider_id},
            ).scalar_one()
        )
    )
    feed = catalog.get_feed_by_code(
        provider.provider_id,
        str(
            db_connection.execute(
                text("SELECT feed_code FROM catalog.data_feed WHERE feed_id=:id"),
                {"id": feed_id},
            ).scalar_one()
        ),
    )
    timeframe = catalog.get_timeframe_by_code("1d")
    session = catalog.get_regular_trading_session(venue_id, date(2088, 7, 1))
    resolved = instruments.find_by_identifier_for_regular_session(
        provider_id,
        "BRSAPI_L18",
        "۰۰۰فملی",
        date(2088, 7, 1),
    )
    assert feed.feed_id == feed_id
    assert timeframe.calendar_unit == "SESSION"
    assert session.session_open_ts == datetime(2088, 7, 1, 5, 30, tzinfo=UTC)
    assert resolved is not None and resolved.instrument.instrument_id == instrument_id

    started = batches.create_batch_if_absent(
        feed_id=feed_id,
        parser_version="brsapi-test-v1",
        request_id=unique_code("REQUEST"),
        started_at=datetime(2088, 7, 2, 10, tzinfo=UTC),
    )
    assert started.created is True
    batch = started.batch

    acquired_at = datetime(2088, 7, 2, 10, tzinfo=UTC)
    raw_events.ensure_month_partition(acquired_at)
    raw = raw_events.insert_response_record(
        ingested_at=acquired_at,
        ingestion_batch_id=batch.ingestion_batch_id,
        feed_id=feed_id,
        source_record_key="brsapi|candlestick|type=2|۰۰۰فملی|1467-04-11",
        source_date_text="۱۴۶۷/۰۴/۱۱",
        source_sequence=0,
        observed_at=acquired_at,
        payload_sha256="a" * 64,
        raw_payload={
            "l18": "۰۰۰فملی",
            "date": "۱۴۶۷/۰۴/۱۱",
            "close": Decimal("101.2500"),
        },
    )
    identical = raw_events.find_identical_record(
        feed_id=feed_id,
        source_record_key="brsapi|candlestick|type=2|۰۰۰فملی|1467-04-11",
        payload_sha256="a" * 64,
    )
    assert identical is not None and identical.identity == raw.identity
    assert identical.raw_payload["close"] == Decimal("101.2500")

    batches.record_acquisition(
        batch.ingestion_batch_id,
        payload_sha256="b" * 64,
        received_row_count=1,
        metadata={"http_status": 200},
    )
    accepted = raw_events.update_validation_result(
        raw.identity,
        validation_status=RawEventValidationStatus.ACCEPTED,
    )
    assert accepted.validation_status is RawEventValidationStatus.ACCEPTED

    series = writer.get_or_create_daily_raw_series(
        feed_id=feed_id,
        instrument_id=instrument_id,
        timeframe_id=timeframe.timeframe_id,
    )
    same_series = writer.get_or_create_daily_raw_series(
        feed_id=feed_id,
        instrument_id=instrument_id,
        timeframe_id=timeframe.timeframe_id,
    )
    assert same_series.bar_series_id == series.bar_series_id
    writer.ensure_month_partitions((date(2088, 7, 1),))

    candidate = BarRevisionCandidate(
        bar_open_ts=session.session_open_ts,
        bar_series_id=series.bar_series_id,
        available_at=acquired_at,
        system_available_at=datetime(2088, 7, 2, 10, 0, 1, tzinfo=UTC),
        bar_close_ts=cast(datetime, session.session_close_ts),
        trading_date=session.trading_date,
        open_price=Decimal("100"),
        high_price=Decimal("102"),
        low_price=Decimal("99"),
        close_price=Decimal("101.25"),
        volume=Decimal("1000"),
        ingestion_batch_id=batch.ingestion_batch_id,
    )
    inserted = writer.append_revision_if_changed(candidate)
    unchanged = writer.append_revision_if_changed(
        candidate.model_copy(
            update={
                "available_at": datetime(2088, 7, 2, 11, tzinfo=UTC),
                "system_available_at": datetime(2088, 7, 2, 11, 0, 1, tzinfo=UTC),
            }
        )
    )
    corrected = writer.append_revision_if_changed(
        candidate.model_copy(
            update={
                "close_price": Decimal("101.50"),
                "available_at": datetime(2088, 7, 2, 12, tzinfo=UTC),
                "system_available_at": datetime(2088, 7, 2, 12, 0, 1, tzinfo=UTC),
            }
        )
    )
    assert inserted.status is BarRevisionWriteStatus.INSERTED
    assert unchanged.status is BarRevisionWriteStatus.UNCHANGED
    assert corrected.status is BarRevisionWriteStatus.CORRECTED
    assert corrected.revision.revision_no == 2

    finalized = batches.finalize_batch(
        batch.ingestion_batch_id,
        status=IngestionBatchStatus.SUCCEEDED,
        received_row_count=1,
        accepted_row_count=1,
        rejected_row_count=0,
        payload_sha256="b" * 64,
        finished_at=datetime(2088, 7, 2, 12, 0, 2, tzinfo=UTC),
    )
    assert finalized.status is IngestionBatchStatus.SUCCEEDED
    assert raw_events.list_by_batch(batch.ingestion_batch_id) == (accepted,)


def _committed_bar_fixture(engine: Engine) -> tuple[int, int, int, int, int, int]:
    with engine.begin() as connection:
        provider_id, feed_id, venue_id, instrument_id = _catalog_fixture(connection)
        timeframe_id = (
            SqlAlchemyDataFeedRepository(connection).get_timeframe_by_code("1d").timeframe_id
        )
        batch = SqlAlchemyIngestionBatchRepository(connection).create_batch(
            feed_id=feed_id,
            parser_version="concurrency-v1",
            request_id=unique_code("CONCURRENT_REQUEST"),
        )
        writer = SqlAlchemyBarWriterRepository(connection)
        series = writer.get_or_create_daily_raw_series(
            feed_id=feed_id,
            instrument_id=instrument_id,
            timeframe_id=timeframe_id,
        )
        writer.ensure_month_partitions((date(2088, 7, 1),))
    return (
        provider_id,
        feed_id,
        venue_id,
        instrument_id,
        batch.ingestion_batch_id,
        series.bar_series_id,
    )


def _concurrent_candidate(
    series_id: int,
    batch_id: int,
    *,
    open_minute: int = 30,
    close_price: str = "101.25",
) -> BarRevisionCandidate:
    return BarRevisionCandidate(
        bar_open_ts=datetime(2088, 7, 1, 5, open_minute, tzinfo=UTC),
        bar_series_id=series_id,
        available_at=datetime(2088, 7, 2, 10, tzinfo=UTC),
        system_available_at=datetime(2088, 7, 2, 10, 0, 1, tzinfo=UTC),
        bar_close_ts=datetime(2088, 7, 1, 9, tzinfo=UTC),
        trading_date=date(2088, 7, 1),
        open_price=Decimal("100"),
        high_price=Decimal("102"),
        low_price=Decimal("99"),
        close_price=Decimal(close_price),
        volume=Decimal("1000"),
        ingestion_batch_id=batch_id,
    )


def _cleanup_committed_bar_fixture(
    engine: Engine,
    fixture: tuple[int, int, int, int, int, int],
) -> None:
    provider_id, feed_id, venue_id, instrument_id, batch_id, series_id = fixture
    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM market.bar_revision WHERE bar_series_id=:series_id"),
            {"series_id": series_id},
        )
        connection.execute(
            text("DELETE FROM market.bar_series WHERE bar_series_id=:series_id"),
            {"series_id": series_id},
        )
        connection.execute(
            text("DELETE FROM ingest.ingestion_batch WHERE ingestion_batch_id=:batch_id"),
            {"batch_id": batch_id},
        )
        connection.execute(
            text(
                "DELETE FROM catalog.instrument_identifier "
                "WHERE provider_id=:provider_id AND instrument_id=:instrument_id"
            ),
            {"provider_id": provider_id, "instrument_id": instrument_id},
        )
        connection.execute(
            text("DELETE FROM catalog.trading_session WHERE venue_id=:venue_id"),
            {"venue_id": venue_id},
        )
        connection.execute(
            text("DELETE FROM catalog.instrument WHERE instrument_id=:instrument_id"),
            {"instrument_id": instrument_id},
        )
        connection.execute(
            text("DELETE FROM catalog.data_feed WHERE feed_id=:feed_id"),
            {"feed_id": feed_id},
        )
        connection.execute(
            text("DELETE FROM catalog.data_provider WHERE provider_id=:provider_id"),
            {"provider_id": provider_id},
        )
        connection.execute(
            text("DELETE FROM catalog.venue WHERE venue_id=:venue_id"),
            {"venue_id": venue_id},
        )


def _cleanup_catalog_only(
    engine: Engine,
    *,
    provider_id: int,
    feed_id: int,
    venue_id: int,
    instrument_id: int,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "DELETE FROM catalog.instrument_identifier "
                "WHERE provider_id=:provider_id AND instrument_id=:instrument_id"
            ),
            {"provider_id": provider_id, "instrument_id": instrument_id},
        )
        connection.execute(
            text("DELETE FROM catalog.trading_session WHERE venue_id=:venue_id"),
            {"venue_id": venue_id},
        )
        connection.execute(
            text("DELETE FROM catalog.instrument WHERE instrument_id=:instrument_id"),
            {"instrument_id": instrument_id},
        )
        connection.execute(
            text("DELETE FROM catalog.data_feed WHERE feed_id=:feed_id"),
            {"feed_id": feed_id},
        )
        connection.execute(
            text("DELETE FROM catalog.data_provider WHERE provider_id=:provider_id"),
            {"provider_id": provider_id},
        )
        connection.execute(
            text("DELETE FROM catalog.venue WHERE venue_id=:venue_id"),
            {"venue_id": venue_id},
        )


def test_concurrent_daily_series_creation_returns_one_identity(db_engine: Engine) -> None:
    with db_engine.begin() as connection:
        provider_id, feed_id, venue_id, instrument_id = _catalog_fixture(connection)
        timeframe_id = (
            SqlAlchemyDataFeedRepository(connection).get_timeframe_by_code("1d").timeframe_id
        )
    barrier = Barrier(2)

    def create_series() -> int:
        with db_engine.begin() as connection:
            barrier.wait(timeout=10)
            return (
                SqlAlchemyBarWriterRepository(connection)
                .get_or_create_daily_raw_series(
                    feed_id=feed_id,
                    instrument_id=instrument_id,
                    timeframe_id=timeframe_id,
                )
                .bar_series_id
            )

    series_ids: set[int] = set()
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = (executor.submit(create_series), executor.submit(create_series))
            series_ids = {future.result(timeout=15) for future in futures}
        assert len(series_ids) == 1
        with db_engine.connect() as connection:
            count = connection.execute(
                text(
                    "SELECT count(*) FROM market.bar_series "
                    "WHERE feed_id=:feed_id AND instrument_id=:instrument_id"
                ),
                {"feed_id": feed_id, "instrument_id": instrument_id},
            ).scalar_one()
        assert count == 1
    finally:
        with db_engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM market.bar_series "
                    "WHERE feed_id=:feed_id AND instrument_id=:instrument_id"
                ),
                {"feed_id": feed_id, "instrument_id": instrument_id},
            )
        _cleanup_catalog_only(
            db_engine,
            provider_id=provider_id,
            feed_id=feed_id,
            venue_id=venue_id,
            instrument_id=instrument_id,
        )


def test_concurrent_request_id_start_creates_one_batch(db_engine: Engine) -> None:
    with db_engine.begin() as connection:
        provider_id, feed_id, venue_id, instrument_id = _catalog_fixture(connection)
    request_id = unique_code("IDEMPOTENT_REQUEST")
    barrier = Barrier(2)

    def start() -> tuple[int, bool]:
        with db_engine.begin() as connection:
            barrier.wait(timeout=10)
            result = SqlAlchemyIngestionBatchRepository(connection).create_batch_if_absent(
                feed_id=feed_id,
                parser_version="concurrency-v1",
                request_id=request_id,
            )
            return result.batch.ingestion_batch_id, result.created

    batch_ids: set[int] = set()
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = (executor.submit(start), executor.submit(start))
            results = {future.result(timeout=15) for future in futures}
        batch_ids = {batch_id for batch_id, _ in results}
        assert len(batch_ids) == 1
        assert {created for _, created in results} == {True, False}
    finally:
        with db_engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM ingest.ingestion_batch "
                    "WHERE feed_id=:feed_id AND request_id=:request_id"
                ),
                {"feed_id": feed_id, "request_id": request_id},
            )
        _cleanup_catalog_only(
            db_engine,
            provider_id=provider_id,
            feed_id=feed_id,
            venue_id=venue_id,
            instrument_id=instrument_id,
        )


def test_concurrent_identical_bar_writes_create_one_revision(db_engine: Engine) -> None:
    fixture = _committed_bar_fixture(db_engine)
    *_, batch_id, series_id = fixture
    candidate = _concurrent_candidate(series_id, batch_id)
    barrier = Barrier(2)

    def write() -> BarRevisionWriteStatus:
        with db_engine.begin() as connection:
            barrier.wait(timeout=10)
            return (
                SqlAlchemyBarWriterRepository(connection)
                .append_revision_if_changed(candidate)
                .status
            )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = (executor.submit(write), executor.submit(write))
            statuses = {future.result(timeout=15) for future in futures}
        assert statuses == {
            BarRevisionWriteStatus.INSERTED,
            BarRevisionWriteStatus.UNCHANGED,
        }
        with db_engine.connect() as connection:
            count = connection.execute(
                text("SELECT count(*) FROM market.bar_revision WHERE bar_series_id=:series_id"),
                {"series_id": series_id},
            ).scalar_one()
        assert count == 1
    finally:
        _cleanup_committed_bar_fixture(db_engine, fixture)


def test_concurrent_conflicting_bar_writes_append_sequential_revisions(
    db_engine: Engine,
) -> None:
    fixture = _committed_bar_fixture(db_engine)
    *_, batch_id, series_id = fixture
    candidates = (
        _concurrent_candidate(series_id, batch_id, close_price="101.25"),
        _concurrent_candidate(series_id, batch_id, close_price="101.50"),
    )
    barrier = Barrier(2)

    def write(candidate: BarRevisionCandidate) -> tuple[BarRevisionWriteStatus, int]:
        with db_engine.begin() as connection:
            barrier.wait(timeout=10)
            result = SqlAlchemyBarWriterRepository(connection).append_revision_if_changed(candidate)
            return result.status, result.revision.revision_no

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = tuple(executor.submit(write, candidate) for candidate in candidates)
            results = {future.result(timeout=15) for future in futures}
        assert results == {
            (BarRevisionWriteStatus.INSERTED, 1),
            (BarRevisionWriteStatus.CORRECTED, 2),
        }
    finally:
        _cleanup_committed_bar_fixture(db_engine, fixture)


def test_different_bar_locks_do_not_serialize_globally(db_engine: Engine) -> None:
    fixture = _committed_bar_fixture(db_engine)
    *_, batch_id, series_id = fixture
    first = _concurrent_candidate(series_id, batch_id, open_minute=30)
    second = _concurrent_candidate(series_id, batch_id, open_minute=31)

    try:
        with db_engine.connect() as held_connection:
            transaction = held_connection.begin()
            SqlAlchemyBarWriterRepository(held_connection).append_revision_if_changed(first)

            def write_other_bar() -> BarRevisionWriteStatus:
                with db_engine.begin() as connection:
                    return (
                        SqlAlchemyBarWriterRepository(connection)
                        .append_revision_if_changed(second)
                        .status
                    )

            with ThreadPoolExecutor(max_workers=1) as executor:
                status = executor.submit(write_other_bar).result(timeout=5)
            assert status is BarRevisionWriteStatus.INSERTED
            transaction.commit()
    finally:
        _cleanup_committed_bar_fixture(db_engine, fixture)
