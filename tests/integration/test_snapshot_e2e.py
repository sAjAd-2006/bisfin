"""Fixture-backed snapshot E2E coverage with no manual catalog prerequisites."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Engine
from tests.fixtures import unique_code

from bisfin.calendar import load_calendar_manifest, validate_calendar_manifest
from bisfin.calendar.importer import TradingCalendarImportService
from bisfin.catalog import load_catalog_manifest
from bisfin.catalog.bootstrap import CatalogBootstrapService, CatalogValidationMode
from bisfin.config import Settings
from bisfin.domain.ingestion import IngestionBatchStatus
from bisfin.ingestion.service import BrsApiDailyBarIngestionService
from bisfin.integrations.brsapi import FixtureBrsApiClient, FixtureBrsApiSymbolClient
from bisfin.repositories import create_unit_of_work_factory
from bisfin.snapshots.builder import SnapshotBuilder
from bisfin.snapshots.manifest import parse_snapshot_manifest_bytes
from bisfin.snapshots.verifier import SnapshotVerifier

_CATALOG = Path("tests/fixtures/catalog/catalog_bootstrap_success.json")
_CALENDAR = Path("tests/fixtures/calendar/tse_regular_success.json")
_SYMBOLS = Path("tests/fixtures/brsapi/symbols")
_CANDLES = Path("tests/fixtures/brsapi/candlestick_type2_success.json")
_CUTOFF = datetime(2030, 1, 1, tzinfo=UTC)


def _manifest(
    code: str,
    series_id: int,
    event_from: datetime,
    event_to: datetime,
    mode: str,
) -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "snapshot_code": code,
            "knowledge_cutoff_ts": _CUTOFF.isoformat().replace("+00:00", "Z"),
            "availability_mode": mode,
            "components": [
                {
                    "component_key": "daily",
                    "kind": "BAR_REVISION",
                    "bar_series_id": series_id,
                    "event_from": event_from.isoformat().replace("+00:00", "Z"),
                    "event_to": event_to.isoformat().replace("+00:00", "Z"),
                }
            ],
        }
    ).encode("utf-8")


def _bootstrap_and_add_correction(
    engine: Engine, settings: Settings
) -> tuple[int, datetime, datetime]:
    """Create canonical prerequisites through their public fixture workflows."""

    factory = create_unit_of_work_factory(engine)
    catalog = CatalogBootstrapService(unit_of_work_factory=factory.create_temporal_write).bootstrap(
        load_catalog_manifest(_CATALOG),
        validation_mode=CatalogValidationMode.FIXTURE_VALIDATE,
        symbol_client=FixtureBrsApiSymbolClient(_SYMBOLS),
        request_id=f"snapshot-catalog-{uuid4()}",
    )
    assert catalog.status is IngestionBatchStatus.SUCCEEDED
    calendar = TradingCalendarImportService(unit_of_work_factory=factory).import_calendar(
        validate_calendar_manifest(load_calendar_manifest(_CALENDAR)),
        request_id=f"snapshot-calendar-{uuid4()}",
    )
    assert calendar.status is IngestionBatchStatus.SUCCEEDED
    daily = BrsApiDailyBarIngestionService(
        client=FixtureBrsApiClient(_CANDLES),
        unit_of_work_factory=factory,
        settings=settings,
    ).ingest(symbol="فملی", request_id=f"snapshot-daily-{uuid4()}")
    assert daily.status is IngestionBatchStatus.SUCCEEDED

    with engine.begin() as connection:
        source = (
            connection.execute(
                text(
                    """
                SELECT revision.*, series.bar_series_id
                FROM market.bar_revision AS revision
                JOIN market.bar_series AS series
                  ON series.bar_series_id = revision.bar_series_id
                JOIN catalog.data_feed AS feed ON feed.feed_id = series.feed_id
                WHERE feed.feed_code = 'TSETMC_CANDLE_DAILY_RAW'
                ORDER BY revision.bar_open_ts
                LIMIT 1
                """
                )
            )
            .mappings()
            .one()
        )
        connection.execute(
            text(
                """
                INSERT INTO market.bar_revision (
                    bar_open_ts, bar_series_id, revision_no, available_at,
                    system_available_at, bar_close_ts, trading_date, open_price,
                    high_price, low_price, close_price, volume, is_final,
                    quality_flags, ingestion_batch_id, recorded_at
                ) VALUES (
                    :bar_open_ts, :bar_series_id, 2, '2029-01-01T00:00:00Z',
                    '2031-01-01T00:00:00Z', :bar_close_ts, :trading_date,
                    :open_price, :high_price, :low_price, :close_price + 1,
                    :volume, TRUE, 0, :ingestion_batch_id, '2029-01-01T00:00:00Z'
                )
                """
            ),
            dict(source),
        )
    return (
        int(source["bar_series_id"]),
        source["bar_open_ts"],
        source["bar_close_ts"],
    )


def test_snapshot_build_verify_replay_modes_and_database_drift(
    db_engine: Engine, db_settings: Settings, tmp_path: Path
) -> None:
    series_id, event_from, event_to = _bootstrap_and_add_correction(db_engine, db_settings)
    public_code = unique_code("SNAP_PUBLIC", max_length=128)
    actual_code = unique_code("SNAP_ACTUAL", max_length=128)
    public_document = parse_snapshot_manifest_bytes(
        _manifest(public_code, series_id, event_from, event_to, "PUBLIC_REPLAY")
    )
    builder = SnapshotBuilder(db_engine)
    public = builder.build(public_document, output_dir=tmp_path)
    actual = builder.build(
        parse_snapshot_manifest_bytes(
            _manifest(actual_code, series_id, event_from, event_to, "ACTUAL_SYSTEM_REPLAY")
        ),
        output_dir=tmp_path,
    )

    assert public.components[0].row_count == 2
    assert actual.components[0].row_count == 1
    assert builder.build(public_document, output_dir=tmp_path).idempotent_replay
    assert SnapshotVerifier(db_engine).verify(public_code, against_db=True).verified

    with db_engine.begin() as connection:
        source = (
            connection.execute(
                text(
                    """
                SELECT * FROM market.bar_revision
                WHERE bar_series_id = :series_id AND revision_no = 2
                """
                ),
                {"series_id": series_id},
            )
            .mappings()
            .one()
        )
        connection.execute(
            text(
                """
                INSERT INTO market.bar_revision (
                    bar_open_ts, bar_series_id, revision_no, available_at,
                    system_available_at, bar_close_ts, trading_date, open_price,
                    high_price, low_price, close_price, volume, is_final,
                    quality_flags, ingestion_batch_id, recorded_at
                ) VALUES (
                    :bar_open_ts, :bar_series_id, 4, '2029-02-01T00:00:00Z',
                    '2029-02-01T00:00:00Z', :bar_close_ts, :trading_date,
                    :open_price, :high_price, :low_price, :close_price + 1,
                    :volume, TRUE, 0, :ingestion_batch_id, '2029-02-01T00:00:00Z'
                )
                """
            ),
            dict(source),
        )
    verified = SnapshotVerifier(db_engine).verify(public_code, against_db=True)
    assert verified.artifact_verified
    assert verified.database_drift

    component_path = tmp_path / actual_code / actual.components[0].relative_storage_path
    component_path.write_bytes(component_path.read_bytes() + b'{"tampered":true}\n')
    tampered = SnapshotVerifier(db_engine).verify(actual_code)
    assert not tampered.artifact_verified
    assert any(issue.code == "COMPONENT_HASH_MISMATCH" for issue in tampered.issues)
