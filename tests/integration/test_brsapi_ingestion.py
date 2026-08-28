"""End-to-end BrsApi daily-bar ingestion contracts on real PostgreSQL 16."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Barrier, Lock
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine, RowMapping
from tests.fixtures import unique_code

from bisfin.config import Settings
from bisfin.db.unit_of_work import SqlAlchemyUnitOfWorkFactory
from bisfin.domain.ingestion import IngestionBatchStatus
from bisfin.domain.market_data import (
    BarRevisionCandidate,
    BarRevisionWriteResult,
    ReplayMode,
)
from bisfin.ingestion.service import (
    BrsApiDailyBarIngestionService,
    DailyBarCanonicalizationError,
    RequestIdConflictError,
)
from bisfin.integrations.brsapi import (
    BrsApiClient,
    BrsApiRawResponse,
    FixtureBrsApiClient,
    response_payload_sha256,
    row_payload_sha256,
)
from bisfin.integrations.brsapi.contracts import JsonObject
from bisfin.repositories import create_unit_of_work_factory
from bisfin.repositories.bar_repository import SqlAlchemyBarRepository
from bisfin.repositories.bar_writer_repository import SqlAlchemyBarWriterRepository
from bisfin.repositories.catalog_writer_repository import SqlAlchemyCatalogWriterRepository
from bisfin.repositories.data_feed_repository import SqlAlchemyDataFeedRepository
from bisfin.repositories.ingestion_batch_repository import (
    SqlAlchemyIngestionBatchRepository,
)
from bisfin.repositories.instrument_repository import SqlAlchemyInstrumentRepository
from bisfin.repositories.raw_event_repository import SqlAlchemyRawEventRepository
from bisfin.repositories.trading_calendar_repository import SqlAlchemyTradingCalendarRepository

_FIXTURES = Path("tests/fixtures/brsapi")
_SYMBOL = "\u0641\u0645\u0644\u06cc"
_TRADING_DATES = (date(2025, 2, 18), date(2025, 2, 19), date(2025, 3, 1))
_RECEIVED_1 = datetime(2026, 1, 10, 10, 0, tzinfo=UTC)
_RECEIVED_2 = datetime(2026, 1, 11, 10, 0, tzinfo=UTC)
_RECEIVED_3 = datetime(2026, 1, 12, 10, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class _Catalog:
    provider_id: int
    provider_code: str
    feed_id: int
    feed_code: str
    venue_id: int
    instrument_id: int


@dataclass(frozen=True, slots=True)
class _ServiceSettings:
    brsapi_provider_code: str
    brsapi_daily_raw_feed_code: str
    brsapi_identifier_type: str = "BRSAPI_L18"


class _SequenceClock:
    def __init__(self, values: Sequence[datetime]) -> None:
        self._values = tuple(values)
        self._index = 0
        self._lock = Lock()

    def __call__(self) -> datetime:
        with self._lock:
            if self._index >= len(self._values):
                raise AssertionError("an unexpected clock value was requested")
            value = self._values[self._index]
            self._index += 1
            return value


class _NeverClient:
    def __init__(self) -> None:
        self.calls = 0

    def fetch_unadjusted_daily_candles(self, symbol: str) -> BrsApiRawResponse:
        self.calls += 1
        raise AssertionError(f"network boundary must not be called for {symbol}")


class _BarrierClient:
    def __init__(self, delegate: BrsApiClient, barrier: Barrier) -> None:
        self._delegate = delegate
        self._barrier = barrier

    def fetch_unadjusted_daily_candles(self, symbol: str) -> BrsApiRawResponse:
        self._barrier.wait(timeout=10)
        return self._delegate.fetch_unadjusted_daily_candles(symbol)


class _FailingBarWriter(SqlAlchemyBarWriterRepository):
    def append_revision_if_changed(
        self,
        candidate: BarRevisionCandidate,
    ) -> BarRevisionWriteResult:
        del candidate
        raise RuntimeError("injected canonicalization failure")


def _settings(catalog: _Catalog) -> _ServiceSettings:
    return _ServiceSettings(
        brsapi_provider_code=catalog.provider_code,
        brsapi_daily_raw_feed_code=catalog.feed_code,
    )


def _fixture_client(
    fixture_name: str,
    *,
    received_at: datetime,
) -> FixtureBrsApiClient:
    return FixtureBrsApiClient(
        _FIXTURES / fixture_name,
        clock=_SequenceClock((received_at - timedelta(seconds=1), received_at)),
    )


def _service(
    engine: Engine,
    catalog: _Catalog,
    fixture_name: str,
    *,
    received_at: datetime,
    service_times: Sequence[datetime],
) -> BrsApiDailyBarIngestionService:
    return BrsApiDailyBarIngestionService(
        client=_fixture_client(fixture_name, received_at=received_at),
        unit_of_work_factory=create_unit_of_work_factory(engine),
        settings=_settings(catalog),
        clock=_SequenceClock(service_times),
    )


def _successful_service_times(received_at: datetime) -> tuple[datetime, ...]:
    return (
        received_at - timedelta(minutes=2),
        received_at + timedelta(seconds=1),
        received_at + timedelta(seconds=2),
        received_at + timedelta(seconds=3),
        received_at + timedelta(seconds=10),
    )


@contextmanager
def _committed_catalog(
    engine: Engine,
    *,
    include_identifier: bool = True,
    include_sessions: bool = True,
) -> Iterator[_Catalog]:
    provider_code = unique_code("BRSAPI_E2E", max_length=64)
    feed_code = unique_code("DAILY_RAW_E2E", max_length=96)
    venue_code = f"TSE_{uuid4().hex[:28].upper()}"
    canonical_symbol = unique_code("BRSAPI_INSTRUMENT", max_length=128)

    with engine.begin() as connection:
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
        feed_id = cast(
            int,
            connection.execute(
                text(
                    """
                    INSERT INTO catalog.data_feed
                        (provider_id, feed_code, display_name, data_kind, parser_version)
                    VALUES (:provider_id, :code, :display_name, 'BAR', 'brsapi-e2e-v1')
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
        venue_id = cast(
            int,
            connection.execute(
                text(
                    """
                    INSERT INTO catalog.venue
                        (venue_code, display_name, country_code, timezone_name,
                         base_currency_code)
                    VALUES (:code, :display_name, 'IR', 'Asia/Tehran', 'IRR')
                    RETURNING venue_id
                    """
                ),
                {"code": venue_code, "display_name": venue_code},
            ).scalar_one(),
        )
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
                {
                    "venue_id": venue_id,
                    "symbol": canonical_symbol,
                    "display_name": canonical_symbol,
                },
            ).scalar_one(),
        )
        if include_identifier:
            connection.execute(
                text(
                    """
                    INSERT INTO catalog.instrument_identifier
                        (provider_id, identifier_type, identifier_value,
                         valid_from, instrument_id, is_primary)
                    VALUES (:provider_id, 'BRSAPI_L18', :symbol,
                            TIMESTAMPTZ '2020-01-01 00:00:00+00',
                            :instrument_id, TRUE)
                    """
                ),
                {
                    "provider_id": provider_id,
                    "instrument_id": instrument_id,
                    "symbol": _SYMBOL,
                },
            )
        if include_sessions:
            for trading_date in _TRADING_DATES:
                open_at = datetime.combine(trading_date, datetime.min.time(), UTC) + timedelta(
                    hours=5
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO catalog.trading_session
                            (venue_id, trading_date, session_code, is_trading_day,
                             session_open_ts, session_close_ts)
                        VALUES (:venue_id, :trading_date, 'REGULAR', TRUE,
                                :open_at, :close_at)
                        """
                    ),
                    {
                        "venue_id": venue_id,
                        "trading_date": trading_date,
                        "open_at": open_at,
                        "close_at": open_at + timedelta(hours=4),
                    },
                )

    catalog = _Catalog(
        provider_id=provider_id,
        provider_code=provider_code,
        feed_id=feed_id,
        feed_code=feed_code,
        venue_id=venue_id,
        instrument_id=instrument_id,
    )
    try:
        yield catalog
    finally:
        _cleanup_catalog(engine, catalog)


def _cleanup_catalog(engine: Engine, catalog: _Catalog) -> None:
    """Delete only rows reachable from this test's collision-resistant feed."""

    parameters = {
        "feed_id": catalog.feed_id,
        "provider_id": catalog.provider_id,
        "venue_id": catalog.venue_id,
        "instrument_id": catalog.instrument_id,
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                DELETE FROM market.bar_revision AS revision
                USING market.bar_series AS series
                WHERE revision.bar_series_id = series.bar_series_id
                  AND series.feed_id = :feed_id
                """
            ),
            parameters,
        )
        connection.execute(
            text("DELETE FROM market.bar_series WHERE feed_id = :feed_id"), parameters
        )
        connection.execute(
            text("DELETE FROM ingest.raw_event WHERE feed_id = :feed_id"), parameters
        )
        connection.execute(
            text("DELETE FROM ingest.ingestion_batch WHERE feed_id = :feed_id"), parameters
        )
        connection.execute(
            text(
                """
                DELETE FROM catalog.instrument_identifier
                WHERE provider_id = :provider_id
                  AND instrument_id = :instrument_id
                """
            ),
            parameters,
        )
        connection.execute(
            text("DELETE FROM catalog.trading_session WHERE venue_id = :venue_id"), parameters
        )
        connection.execute(
            text("DELETE FROM catalog.instrument WHERE instrument_id = :instrument_id"),
            parameters,
        )
        connection.execute(
            text("DELETE FROM catalog.data_feed WHERE feed_id = :feed_id"), parameters
        )
        connection.execute(
            text("DELETE FROM catalog.data_provider WHERE provider_id = :provider_id"),
            parameters,
        )
        connection.execute(text("DELETE FROM catalog.venue WHERE venue_id = :venue_id"), parameters)


def _batch(engine: Engine, batch_id: int) -> RowMapping:
    with engine.connect() as connection:
        return (
            connection.execute(
                text("SELECT * FROM ingest.ingestion_batch WHERE ingestion_batch_id = :id"),
                {"id": batch_id},
            )
            .mappings()
            .one()
        )


def _raw_rows(engine: Engine, batch_id: int) -> Sequence[RowMapping]:
    with engine.connect() as connection:
        return (
            connection.execute(
                text(
                    """
                SELECT raw.*, raw.raw_payload::text AS raw_payload_text,
                       raw.validation_errors::text AS validation_errors_text,
                       raw.tableoid::regclass::text AS partition_name
                FROM ingest.raw_event AS raw
                WHERE ingestion_batch_id = :batch_id
                ORDER BY source_sequence
                """
                ),
                {"batch_id": batch_id},
            )
            .mappings()
            .all()
        )


def _series_id(engine: Engine, catalog: _Catalog) -> int:
    with engine.connect() as connection:
        return cast(
            int,
            connection.execute(
                text(
                    """
                    SELECT series.bar_series_id
                    FROM market.bar_series AS series
                    JOIN catalog.timeframe AS timeframe
                      ON timeframe.timeframe_id = series.timeframe_id
                    WHERE series.feed_id = :feed_id
                      AND series.instrument_id = :instrument_id
                      AND timeframe.timeframe_code = '1d'
                      AND series.price_basis = 'RAW'
                      AND series.adjustment_set_id IS NULL
                      AND series.close_semantics = 'LAST_TRADE'
                      AND series.session_code = 'REGULAR'
                    """
                ),
                {"feed_id": catalog.feed_id, "instrument_id": catalog.instrument_id},
            ).scalar_one(),
        )


def _revision_rows(engine: Engine, series_id: int) -> Sequence[RowMapping]:
    with engine.connect() as connection:
        return (
            connection.execute(
                text(
                    """
                SELECT revision.*,
                       revision.tableoid::regclass::text AS partition_name
                FROM market.bar_revision AS revision
                WHERE bar_series_id = :series_id
                ORDER BY trading_date, revision_no
                """
                ),
                {"series_id": series_id},
            )
            .mappings()
            .all()
        )


def test_success_persists_exact_raw_rows_and_daily_revision_one(
    db_engine: Engine,
) -> None:
    with _committed_catalog(db_engine) as catalog:
        fixture_path = _FIXTURES / "candlestick_type2_success.json"
        result = _service(
            db_engine,
            catalog,
            fixture_path.name,
            received_at=_RECEIVED_1,
            service_times=_successful_service_times(_RECEIVED_1),
        ).ingest(symbol=_SYMBOL, request_id=unique_code("SUCCESS_REQUEST"))

        assert result.status is IngestionBatchStatus.SUCCEEDED
        assert (
            result.received_count,
            result.accepted_count,
            result.rejected_count,
            result.raw_inserted_count,
            result.bar_inserted_count,
            result.bar_corrected_count,
            result.bar_unchanged_count,
        ) == (3, 3, 0, 3, 3, 0, 0)
        assert result.source_watermark == "2025-03-01"
        assert result.payload_sha256 == response_payload_sha256(fixture_path.read_bytes())

        batch = _batch(db_engine, result.ingestion_batch_id)
        assert batch["status"] == "SUCCEEDED"
        assert batch["payload_sha256"] == result.payload_sha256
        assert (batch["received_row_count"], batch["accepted_row_count"]) == (3, 3)
        assert batch["rejected_row_count"] == 0
        assert batch["metadata"]["provider_status"] == "rows"
        assert batch["metadata"]["inserted_raw_count"] == 3

        expected_rows = cast(
            "list[JsonObject]",
            json.loads(fixture_path.read_text(encoding="utf-8"), parse_float=Decimal),
        )
        raw_rows = _raw_rows(db_engine, result.ingestion_batch_id)
        assert len(raw_rows) == 3
        assert [
            json.loads(str(row["raw_payload_text"]), parse_float=Decimal) for row in raw_rows
        ] == expected_rows
        assert [row["payload_sha256"] for row in raw_rows] == [
            row_payload_sha256(row) for row in expected_rows
        ]
        assert [row["source_sequence"] for row in raw_rows] == [1, 2, 3]
        assert {row["validation_status"] for row in raw_rows} == {"ACCEPTED"}
        assert {row["ingested_at"] for row in raw_rows} == {_RECEIVED_1}
        assert {row["observed_at"] for row in raw_rows} == {_RECEIVED_1}
        assert {str(row["partition_name"]) for row in raw_rows} == {"ingest.raw_event_y2026m01"}

        series_id = _series_id(db_engine, catalog)
        revisions = _revision_rows(db_engine, series_id)
        assert len(revisions) == 3
        assert [row["trading_date"] for row in revisions] == list(_TRADING_DATES)
        assert [row["revision_no"] for row in revisions] == [1, 1, 1]
        assert [row["close_price"] for row in revisions] == [
            Decimal("7100"),
            Decimal("7250"),
            Decimal("7350"),
        ]
        assert [row["volume"] for row in revisions] == [
            Decimal("10000000"),
            Decimal("12000000"),
            Decimal("15000000"),
        ]
        assert {row["available_at"] for row in revisions} == {_RECEIVED_1}
        assert [row["system_available_at"] for row in revisions] == [
            _RECEIVED_1 + timedelta(seconds=1),
            _RECEIVED_1 + timedelta(seconds=2),
            _RECEIVED_1 + timedelta(seconds=3),
        ]
        assert {row["ingestion_batch_id"] for row in revisions} == {result.ingestion_batch_id}
        assert {str(row["partition_name"]) for row in revisions} == {
            "market.bar_revision_y2025m02",
            "market.bar_revision_y2025m03",
        }
        assert all(row["available_at"] >= row["bar_close_ts"] for row in revisions)


def test_no_data_succeeds_without_raw_or_canonical_rows(db_engine: Engine) -> None:
    with _committed_catalog(db_engine) as catalog:
        fixture_path = _FIXTURES / "candlestick_type2_no_data.json"
        result = _service(
            db_engine,
            catalog,
            fixture_path.name,
            received_at=_RECEIVED_1,
            service_times=(
                _RECEIVED_1 - timedelta(minutes=2),
                _RECEIVED_1 + timedelta(seconds=1),
            ),
        ).ingest(symbol=_SYMBOL, request_id=unique_code("NO_DATA_REQUEST"))

        assert result.status is IngestionBatchStatus.SUCCEEDED
        assert (
            result.received_count,
            result.accepted_count,
            result.rejected_count,
            result.raw_inserted_count,
            result.bar_inserted_count,
        ) == (0, 0, 0, 0, 0)
        assert result.payload_sha256 == response_payload_sha256(fixture_path.read_bytes())
        assert _raw_rows(db_engine, result.ingestion_batch_id) == []
        batch = _batch(db_engine, result.ingestion_batch_id)
        assert batch["metadata"]["provider_status"] == "no_data"
        with db_engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM market.bar_series WHERE feed_id = :feed_id"),
                    {"feed_id": catalog.feed_id},
                ).scalar_one()
                == 0
            )


def test_partial_response_preserves_all_rows_and_rejects_invalid_rows(
    db_engine: Engine,
) -> None:
    with _committed_catalog(db_engine) as catalog:
        result = _service(
            db_engine,
            catalog,
            "candlestick_type2_partial_invalid.json",
            received_at=_RECEIVED_1,
            service_times=(
                _RECEIVED_1 - timedelta(minutes=2),
                _RECEIVED_1 + timedelta(seconds=1),
                _RECEIVED_1 + timedelta(seconds=10),
            ),
        ).ingest(symbol=_SYMBOL, request_id=unique_code("PARTIAL_REQUEST"))

        assert result.status is IngestionBatchStatus.PARTIAL
        assert (
            result.received_count,
            result.accepted_count,
            result.rejected_count,
            result.raw_inserted_count,
            result.bar_inserted_count,
        ) == (4, 1, 3, 4, 1)
        raw_rows = _raw_rows(db_engine, result.ingestion_batch_id)
        assert [row["validation_status"] for row in raw_rows] == [
            "ACCEPTED",
            "REJECTED",
            "REJECTED",
            "REJECTED",
        ]
        rejected_codes = {
            str(issue["code"])
            for row in raw_rows[1:]
            for issue in json.loads(str(row["validation_errors_text"]))
        }
        assert {
            "NEGATIVE_VOLUME",
            "INVALID_SOURCE_TIME",
            "INVALID_JALALI_DATE",
        } <= rejected_codes
        assert len(_revision_rows(db_engine, _series_id(db_engine, catalog))) == 1


def test_malformed_response_is_quarantined_with_hash_and_bounded_diagnostic(
    db_engine: Engine,
) -> None:
    with _committed_catalog(db_engine) as catalog:
        fixture_path = _FIXTURES / "candlestick_malformed_json.txt"
        result = _service(
            db_engine,
            catalog,
            fixture_path.name,
            received_at=_RECEIVED_1,
            service_times=(
                _RECEIVED_1 - timedelta(minutes=2),
                _RECEIVED_1 + timedelta(seconds=1),
            ),
        ).ingest(symbol=_SYMBOL, request_id=unique_code("MALFORMED_REQUEST"))

        assert result.status is IngestionBatchStatus.QUARANTINED
        assert result.payload_sha256 == response_payload_sha256(fixture_path.read_bytes())
        assert result.received_count == result.raw_inserted_count == 0
        assert _raw_rows(db_engine, result.ingestion_batch_id) == []
        batch = _batch(db_engine, result.ingestion_batch_id)
        assert batch["metadata"]["provider_status"] == "quarantined_contract"
        assert batch["metadata"]["failure_code"] == ("BRSAPI_MALFORMED_OR_AMBIGUOUS_RESPONSE")
        preview = str(batch["metadata"]["body_diagnostic_preview"])
        assert preview == fixture_path.read_text(encoding="utf-8")
        assert len(preview) <= 512


@pytest.mark.parametrize(
    ("include_identifier", "include_sessions", "expected_code"),
    (
        (False, True, "INSTRUMENT_NOT_FOUND"),
        (True, False, "TRADING_SESSION_NOT_FOUND"),
    ),
)
def test_missing_catalog_reference_quarantines_every_candidate(
    db_engine: Engine,
    include_identifier: bool,
    include_sessions: bool,
    expected_code: str,
) -> None:
    with _committed_catalog(
        db_engine,
        include_identifier=include_identifier,
        include_sessions=include_sessions,
    ) as catalog:
        result = _service(
            db_engine,
            catalog,
            "candlestick_type2_success.json",
            received_at=_RECEIVED_1,
            service_times=(
                _RECEIVED_1 - timedelta(minutes=2),
                _RECEIVED_1 + timedelta(seconds=1),
            ),
        ).ingest(symbol=_SYMBOL, request_id=unique_code("MISSING_CATALOG_REQUEST"))

        assert result.status is IngestionBatchStatus.QUARANTINED
        assert (result.received_count, result.accepted_count, result.rejected_count) == (
            3,
            0,
            3,
        )
        for row in _raw_rows(db_engine, result.ingestion_batch_id):
            diagnostics = json.loads(str(row["validation_errors_text"]))
            assert expected_code in {issue["code"] for issue in diagnostics}
        with db_engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM market.bar_series WHERE feed_id = :feed_id"),
                    {"feed_id": catalog.feed_id},
                ).scalar_one()
                == 0
            )


def test_identical_repeat_then_correction_is_append_only_and_point_in_time(
    db_engine: Engine,
) -> None:
    with _committed_catalog(db_engine) as catalog:
        first = _service(
            db_engine,
            catalog,
            "candlestick_type2_success.json",
            received_at=_RECEIVED_1,
            service_times=_successful_service_times(_RECEIVED_1),
        ).ingest(symbol=_SYMBOL, request_id=unique_code("BASE_REQUEST"))
        repeated = _service(
            db_engine,
            catalog,
            "candlestick_type2_success.json",
            received_at=_RECEIVED_2,
            service_times=_successful_service_times(_RECEIVED_2),
        ).ingest(symbol=_SYMBOL, request_id=unique_code("REPEAT_REQUEST"))
        series_id = _series_id(db_engine, catalog)

        assert repeated.status is IngestionBatchStatus.SUCCEEDED
        assert (
            repeated.bar_inserted_count,
            repeated.bar_corrected_count,
            repeated.bar_unchanged_count,
        ) == (0, 0, 3)
        assert len(_revision_rows(db_engine, series_id)) == 3

        corrected = _service(
            db_engine,
            catalog,
            "candlestick_type2_corrected.json",
            received_at=_RECEIVED_3,
            service_times=_successful_service_times(_RECEIVED_3),
        ).ingest(symbol=_SYMBOL, request_id=unique_code("CORRECTION_REQUEST"))
        assert (
            corrected.bar_inserted_count,
            corrected.bar_corrected_count,
            corrected.bar_unchanged_count,
        ) == (0, 1, 2)

        revisions = _revision_rows(db_engine, series_id)
        assert len(revisions) == 4
        corrected_date_rows = [row for row in revisions if row["trading_date"] == date(2025, 2, 19)]
        assert [row["revision_no"] for row in corrected_date_rows] == [1, 2]
        assert [row["close_price"] for row in corrected_date_rows] == [
            Decimal("7250"),
            Decimal("7280"),
        ]
        assert [row["ingestion_batch_id"] for row in corrected_date_rows] == [
            first.ingestion_batch_id,
            corrected.ingestion_batch_id,
        ]
        assert [row["available_at"] for row in corrected_date_rows] == [
            _RECEIVED_1,
            _RECEIVED_3,
        ]

        window_start = datetime(2025, 2, 19, 5, tzinfo=UTC)
        window_end = datetime(2025, 2, 20, tzinfo=UTC)
        with create_unit_of_work_factory(db_engine)() as unit_of_work:
            public_before = unit_of_work.bars.get_bars_as_of(
                series_id,
                window_start,
                window_end,
                _RECEIVED_3 - timedelta(seconds=1),
                ReplayMode.PUBLIC_REPLAY,
            )
            public_after = unit_of_work.bars.get_bars_as_of(
                series_id,
                window_start,
                window_end,
                _RECEIVED_3,
                ReplayMode.PUBLIC_REPLAY,
            )
            actual_before = unit_of_work.bars.get_bars_as_of(
                series_id,
                window_start,
                window_end,
                _RECEIVED_3 + timedelta(seconds=1),
                ReplayMode.ACTUAL_SYSTEM_REPLAY,
            )
            actual_after = unit_of_work.bars.get_bars_as_of(
                series_id,
                window_start,
                window_end,
                _RECEIVED_3 + timedelta(seconds=2),
                ReplayMode.ACTUAL_SYSTEM_REPLAY,
            )

        assert [bar.close_price for bar in public_before] == [Decimal("7250")]
        assert [bar.close_price for bar in public_after] == [Decimal("7280")]
        assert [bar.close_price for bar in actual_before] == [Decimal("7250")]
        assert [bar.close_price for bar in actual_after] == [Decimal("7280")]


def test_terminal_request_replays_and_running_request_conflicts_without_network(
    db_engine: Engine,
) -> None:
    with _committed_catalog(db_engine) as catalog:
        request_id = unique_code("TERMINAL_REPLAY")
        first = _service(
            db_engine,
            catalog,
            "candlestick_type2_success.json",
            received_at=_RECEIVED_1,
            service_times=_successful_service_times(_RECEIVED_1),
        ).ingest(symbol=_SYMBOL, request_id=request_id)

        replay_client = _NeverClient()
        replay = BrsApiDailyBarIngestionService(
            client=replay_client,
            unit_of_work_factory=create_unit_of_work_factory(db_engine),
            settings=_settings(catalog),
            clock=_SequenceClock((_RECEIVED_2,)),
        ).ingest(symbol=_SYMBOL, request_id=request_id)
        assert replay.idempotent_replay is True
        assert replay.ingestion_batch_id == first.ingestion_batch_id
        assert replay_client.calls == 0

        running_request_id = unique_code("RUNNING_CONFLICT")
        with db_engine.begin() as connection:
            SqlAlchemyIngestionBatchRepository(connection).create_batch(
                feed_id=catalog.feed_id,
                parser_version="brsapi-candlestick-type2-v1",
                request_id=running_request_id,
                started_at=_RECEIVED_2,
                metadata={"symbol": _SYMBOL},
            )
        conflict_client = _NeverClient()
        conflict_service = BrsApiDailyBarIngestionService(
            client=conflict_client,
            unit_of_work_factory=create_unit_of_work_factory(db_engine),
            settings=_settings(catalog),
            clock=_SequenceClock((_RECEIVED_2 + timedelta(minutes=1),)),
        )
        with pytest.raises(RequestIdConflictError, match="RUNNING"):
            conflict_service.ingest(symbol=_SYMBOL, request_id=running_request_id)
        assert conflict_client.calls == 0


def test_canonicalization_failure_keeps_transaction_b_raw_rows(
    db_engine: Engine,
) -> None:
    with _committed_catalog(db_engine) as catalog:
        failing_factory = SqlAlchemyUnitOfWorkFactory(
            db_engine,
            data_feeds=SqlAlchemyDataFeedRepository,
            instruments=SqlAlchemyInstrumentRepository,
            ingestion_batches=SqlAlchemyIngestionBatchRepository,
            raw_events=SqlAlchemyRawEventRepository,
            bars=SqlAlchemyBarRepository,
            bar_writer=_FailingBarWriter,
            catalog_writer=SqlAlchemyCatalogWriterRepository,
            trading_calendar=SqlAlchemyTradingCalendarRepository,
        )
        request_id = unique_code("CANONICAL_FAILURE")
        service = BrsApiDailyBarIngestionService(
            client=_fixture_client(
                "candlestick_type2_success.json",
                received_at=_RECEIVED_1,
            ),
            unit_of_work_factory=failing_factory,
            settings=_settings(catalog),
            clock=_SequenceClock(
                (
                    _RECEIVED_1 - timedelta(minutes=2),
                    _RECEIVED_1 + timedelta(seconds=1),
                    _RECEIVED_1 + timedelta(seconds=10),
                )
            ),
        )

        with pytest.raises(DailyBarCanonicalizationError):
            service.ingest(symbol=_SYMBOL, request_id=request_id)

        with db_engine.connect() as connection:
            batch_id = cast(
                int,
                connection.execute(
                    text(
                        """
                        SELECT ingestion_batch_id
                        FROM ingest.ingestion_batch
                        WHERE feed_id = :feed_id AND request_id = :request_id
                        """
                    ),
                    {"feed_id": catalog.feed_id, "request_id": request_id},
                ).scalar_one(),
            )
        batch = _batch(db_engine, batch_id)
        assert batch["status"] == "FAILED"
        assert batch["metadata"]["failure_code"] == "CANONICALIZATION_FAILED"
        raw_rows = _raw_rows(db_engine, batch_id)
        assert len(raw_rows) == 3
        assert {row["validation_status"] for row in raw_rows} == {"PENDING"}
        with db_engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM market.bar_series WHERE feed_id = :feed_id"),
                    {"feed_id": catalog.feed_id},
                ).scalar_one()
                == 0
            )


@pytest.mark.parametrize(
    ("second_fixture", "expected_revision_count", "expected_aggregate"),
    (
        ("candlestick_type2_success.json", 3, (3, 0, 3)),
        ("candlestick_type2_corrected.json", 4, (3, 1, 2)),
    ),
)
def test_concurrent_ingestions_serialize_identical_and_corrected_writes(
    db_engine: Engine,
    second_fixture: str,
    expected_revision_count: int,
    expected_aggregate: tuple[int, int, int],
) -> None:
    with _committed_catalog(db_engine) as catalog:
        barrier = Barrier(2)

        def ingest(fixture_name: str, request_id: str) -> tuple[int, int, int]:
            fixture_client = _fixture_client(fixture_name, received_at=_RECEIVED_1)
            service = BrsApiDailyBarIngestionService(
                client=_BarrierClient(fixture_client, barrier),
                unit_of_work_factory=create_unit_of_work_factory(db_engine),
                settings=_settings(catalog),
                clock=_SequenceClock(_successful_service_times(_RECEIVED_1)),
            )
            result = service.ingest(symbol=_SYMBOL, request_id=request_id)
            assert result.status is IngestionBatchStatus.SUCCEEDED
            return (
                result.bar_inserted_count,
                result.bar_corrected_count,
                result.bar_unchanged_count,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = (
                executor.submit(
                    ingest,
                    "candlestick_type2_success.json",
                    unique_code("CONCURRENT_ONE"),
                ),
                executor.submit(
                    ingest,
                    second_fixture,
                    unique_code("CONCURRENT_TWO"),
                ),
            )
            results = tuple(future.result(timeout=30) for future in futures)

        aggregate = tuple(sum(result[index] for result in results) for index in range(3))
        assert aggregate == expected_aggregate
        revisions = _revision_rows(db_engine, _series_id(db_engine, catalog))
        assert len(revisions) == expected_revision_count
        assert max(row["revision_no"] for row in revisions) == (
            1 if expected_revision_count == 3 else 2
        )


def test_cli_fixture_mode_end_to_end(db_engine: Engine, db_settings: Settings) -> None:
    with _committed_catalog(db_engine) as catalog:
        executable = shutil.which("bisfin")
        if executable is None:
            executable_name = "bisfin.exe" if os.name == "nt" else "bisfin"
            installed_script = Path(sys.executable).resolve().parent / executable_name
            executable = str(installed_script) if installed_script.is_file() else None
        assert executable is not None

        request_id = unique_code("CLI_FIXTURE_REQUEST")
        environment = os.environ.copy()
        environment.update(
            {
                "DATABASE_URL": db_settings.psycopg_database_url,
                "BISFIN_ENV": "test",
                "BISFIN_LOG_FORMAT": "json",
                "BRSAPI_PROVIDER_CODE": catalog.provider_code,
                "BRSAPI_DAILY_RAW_FEED_CODE": catalog.feed_code,
                "BRSAPI_IDENTIFIER_TYPE": "BRSAPI_L18",
            }
        )
        environment.pop("BRSAPI_API_KEY", None)
        completed = subprocess.run(
            [
                executable,
                "ingest",
                "brsapi-daily-bars",
                "--symbol",
                _SYMBOL,
                "--request-id",
                request_id,
                "--fixture",
                str((_FIXTURES / "candlestick_type2_success.json").resolve()),
                "--output-format",
                "json",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=environment,
        )

        assert completed.returncode == 0, completed.stderr or completed.stdout
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
        assert payload["status"] == "SUCCEEDED"
        assert payload["symbol"] == _SYMBOL
        assert payload["received_count"] == payload["accepted_count"] == 3
        assert payload["bar_inserted_count"] == 3
        assert payload["bar_corrected_count"] == payload["bar_unchanged_count"] == 0
        assert "BRSAPI_API_KEY" not in completed.stdout + completed.stderr
        assert len(_raw_rows(db_engine, int(payload["ingestion_batch_id"]))) == 3
