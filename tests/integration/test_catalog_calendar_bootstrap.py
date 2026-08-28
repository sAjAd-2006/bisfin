"""No-manual-seed fixture flow from catalog/bootstrap through PR-05 ingestion."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from sqlalchemy.engine import Engine

from bisfin.calendar import load_calendar_manifest, validate_calendar_manifest
from bisfin.calendar.importer import TradingCalendarImportService
from bisfin.catalog import load_catalog_manifest
from bisfin.catalog.bootstrap import CatalogBootstrapService, CatalogValidationMode
from bisfin.config import Settings
from bisfin.domain.ingestion import IngestionBatchStatus
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
