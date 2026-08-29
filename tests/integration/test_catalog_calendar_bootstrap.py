"""No-manual-seed fixture flow from catalog/bootstrap through PR-05 ingestion."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Engine

from bisfin.calendar import load_calendar_manifest, validate_calendar_manifest
from bisfin.calendar.importer import TradingCalendarImportService
from bisfin.catalog import load_catalog_manifest
from bisfin.catalog.bootstrap import CatalogBootstrapService, CatalogValidationMode
from bisfin.config import Settings
from bisfin.domain.ingestion import IngestionBatchStatus
from bisfin.domain.market_data import ReplayMode
from bisfin.ingestion.service import BrsApiDailyBarIngestionService
from bisfin.integrations.brsapi import FixtureBrsApiClient, FixtureBrsApiSymbolClient
from bisfin.repositories import create_unit_of_work_factory

_CATALOG = Path("tests/fixtures/catalog/catalog_bootstrap_success.json")
_CALENDAR = Path("tests/fixtures/calendar/tse_regular_success.json")
_SYMBOLS = Path("tests/fixtures/brsapi/symbols")
_CANDLES = Path("tests/fixtures/brsapi/candlestick_type2_success.json")


def test_fixture_bootstrap_removes_pr05_manual_catalog_seed(
    db_engine: Engine,
    db_settings: Settings,
) -> None:
    """The manifest and calendar are enough for a canonical RAW daily revision."""

    factory = create_unit_of_work_factory(db_engine)
    catalog = CatalogBootstrapService(unit_of_work_factory=factory.create_temporal_write).bootstrap(
        load_catalog_manifest(_CATALOG),
        validation_mode=CatalogValidationMode.FIXTURE_VALIDATE,
        symbol_client=FixtureBrsApiSymbolClient(_SYMBOLS),
        request_id=f"catalog-e2e-{uuid4()}",
    )
    assert catalog.status is IngestionBatchStatus.SUCCEEDED

    calendar = TradingCalendarImportService(unit_of_work_factory=factory).import_calendar(
        validate_calendar_manifest(load_calendar_manifest(_CALENDAR)),
        request_id=f"calendar-e2e-{uuid4()}",
    )
    assert calendar.status is IngestionBatchStatus.SUCCEEDED

    daily = BrsApiDailyBarIngestionService(
        client=FixtureBrsApiClient(_CANDLES),
        unit_of_work_factory=factory,
        settings=db_settings,
    ).ingest(symbol="فملی", request_id=f"daily-e2e-{uuid4()}")
    assert daily.status is IngestionBatchStatus.SUCCEEDED
    assert daily.bar_inserted_count + daily.bar_unchanged_count == 3

    first_counts = _canonical_counts(db_engine)
    with db_engine.connect() as connection:
        identifier_types = (
            connection.execute(
                text(
                    "SELECT identifier_type FROM catalog.instrument_identifier "
                    "WHERE identifier_value IN ('IRO1MSMI0001', 'فملی')"
                )
            )
            .scalars()
            .all()
        )
        session = (
            connection.execute(
                text(
                    "SELECT session_open_ts, session_close_ts FROM catalog.trading_session "
                    "WHERE trading_date = DATE '2025-02-18' AND session_code = 'REGULAR'"
                )
            )
            .mappings()
            .one()
        )
        revision = (
            connection.execute(
                text(
                    "SELECT series.bar_series_id, revision.* FROM market.bar_series AS series "
                    "JOIN market.bar_revision AS revision "
                    "ON revision.bar_series_id = series.bar_series_id "
                    "WHERE series.feed_id = (SELECT feed_id FROM catalog.data_feed "
                    "WHERE feed_code = 'TSETMC_CANDLE_DAILY_RAW') "
                    "AND series.instrument_id = (SELECT instrument_id "
                    "FROM catalog.instrument_identifier "
                    "WHERE identifier_value = 'IRO1MSMI0001' "
                    "ORDER BY valid_from DESC LIMIT 1) "
                    "AND revision.trading_date = DATE '2025-02-18' "
                    "AND revision.revision_no = 1"
                )
            )
            .mappings()
            .one()
        )
    assert sorted(identifier_types) == ["BRSAPI_L18", "ISIN"]
    assert revision["revision_no"] == 1
    assert revision["open_price"] == Decimal("7000")
    assert revision["high_price"] == Decimal("7200")
    assert revision["low_price"] == Decimal("6900")
    assert revision["close_price"] == Decimal("7100")
    assert revision["volume"] == Decimal("10000000")
    assert revision["bar_open_ts"] == session["session_open_ts"]
    assert revision["bar_close_ts"] == session["session_close_ts"]
    assert str(revision["trading_date"]) == "2025-02-18"

    with factory() as unit_of_work:
        kwargs = {
            "bar_series_id": revision["bar_series_id"],
            "from_ts": revision["bar_open_ts"],
            "to_ts": revision["bar_close_ts"],
        }
        assert not unit_of_work.bars.get_bars_as_of(
            **kwargs,
            knowledge_cutoff_ts=revision["available_at"] - timedelta(microseconds=1),
            replay_mode=ReplayMode.PUBLIC_REPLAY,
        )
        assert (
            len(
                unit_of_work.bars.get_bars_as_of(
                    **kwargs,
                    knowledge_cutoff_ts=revision["available_at"],
                    replay_mode=ReplayMode.PUBLIC_REPLAY,
                )
            )
            == 1
        )
        assert not unit_of_work.bars.get_bars_as_of(
            **kwargs,
            knowledge_cutoff_ts=revision["system_available_at"] - timedelta(microseconds=1),
            replay_mode=ReplayMode.ACTUAL_SYSTEM_REPLAY,
        )
        assert (
            len(
                unit_of_work.bars.get_bars_as_of(
                    **kwargs,
                    knowledge_cutoff_ts=revision["system_available_at"],
                    replay_mode=ReplayMode.ACTUAL_SYSTEM_REPLAY,
                )
            )
            == 1
        )

    CatalogBootstrapService(unit_of_work_factory=factory.create_temporal_write).bootstrap(
        load_catalog_manifest(_CATALOG),
        validation_mode=CatalogValidationMode.FIXTURE_VALIDATE,
        symbol_client=FixtureBrsApiSymbolClient(_SYMBOLS),
        request_id=f"catalog-e2e-rerun-{uuid4()}",
    )
    TradingCalendarImportService(unit_of_work_factory=factory).import_calendar(
        validate_calendar_manifest(load_calendar_manifest(_CALENDAR)),
        request_id=f"calendar-e2e-rerun-{uuid4()}",
    )
    rerun_daily = BrsApiDailyBarIngestionService(
        client=FixtureBrsApiClient(_CANDLES),
        unit_of_work_factory=factory,
        settings=db_settings,
    ).ingest(symbol="فملی", request_id=f"daily-e2e-rerun-{uuid4()}")
    assert rerun_daily.status is IngestionBatchStatus.SUCCEEDED
    assert rerun_daily.bar_inserted_count == 0
    assert _canonical_counts(db_engine) == first_counts


def _canonical_counts(engine: Engine) -> tuple[int, ...]:
    with engine.connect() as connection:
        return tuple(
            connection.execute(text(query)).scalar_one()
            for query in (
                "SELECT count(*) FROM catalog.data_provider",
                "SELECT count(*) FROM catalog.data_feed",
                "SELECT count(*) FROM catalog.venue",
                "SELECT count(*) FROM catalog.timeframe",
                "SELECT count(*) FROM catalog.instrument",
                "SELECT count(*) FROM catalog.instrument_identifier",
                "SELECT count(*) FROM catalog.instrument_spec_version",
                "SELECT count(*) FROM catalog.trading_session",
                "SELECT count(*) FROM market.bar_series",
                "SELECT count(*) FROM market.bar_revision",
            )
        )
