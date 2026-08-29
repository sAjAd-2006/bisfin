"""Fixture-backed snapshot E2E coverage with no manual catalog prerequisites."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy.engine import Engine
from tests.fixtures import unique_code
from tests.integration.snapshot_support import SnapshotSeries, insert_revision, seed_snapshot_series

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
) -> tuple[SnapshotSeries, datetime, datetime]:
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

    # The bootstrap/ingestion flow proves prerequisites. Snapshot rows themselves
    # are then test-owned so earlier integration tests cannot influence selection.
    series = seed_snapshot_series(engine)
    event_from = datetime(2029, 1, 1, tzinfo=UTC)
    event_to = datetime(2029, 1, 2, tzinfo=UTC)
    insert_revision(
        engine,
        series,
        bar_open_ts=event_from,
        revision_no=1,
        available_at=datetime(2029, 1, 2, tzinfo=UTC),
    )
    insert_revision(
        engine,
        series,
        bar_open_ts=event_from,
        revision_no=2,
        available_at=datetime(2029, 1, 4, tzinfo=UTC),
        system_available_at=datetime(2031, 1, 1, tzinfo=UTC),
        close_price=11,
    )
    return series, event_from, event_to


def test_snapshot_build_verify_replay_modes_and_database_drift(
    db_engine: Engine, db_settings: Settings, snapshot_artifact_dir: Path
) -> None:
    series, event_from, event_to = _bootstrap_and_add_correction(db_engine, db_settings)
    public_code = unique_code("SNAP_PUBLIC")
    actual_code = unique_code("SNAP_ACTUAL")
    public_document = parse_snapshot_manifest_bytes(
        _manifest(public_code, series.bar_series_id, event_from, event_to, "PUBLIC_REPLAY")
    )
    builder = SnapshotBuilder(db_engine)
    public = builder.build(public_document, output_dir=snapshot_artifact_dir)
    actual = builder.build(
        parse_snapshot_manifest_bytes(
            _manifest(
                actual_code,
                series.bar_series_id,
                event_from,
                event_to,
                "ACTUAL_SYSTEM_REPLAY",
            )
        ),
        output_dir=snapshot_artifact_dir,
    )

    assert public.components[0].row_count == 2
    assert actual.components[0].row_count == 1
    assert builder.build(public_document, output_dir=snapshot_artifact_dir).idempotent_replay
    assert SnapshotVerifier(db_engine).verify(public_code, against_db=True).verified

    # Add a previously eligible revision to the exact E2E series; no global SQL lookup.
    insert_revision(
        db_engine,
        series,
        bar_open_ts=event_from,
        revision_no=3,
        available_at=datetime(2029, 2, 1, tzinfo=UTC),
        close_price=12,
    )
    verified = SnapshotVerifier(db_engine).verify(public_code, against_db=True)
    assert verified.artifact_verified
    assert verified.database_drift

    component_path = (
        snapshot_artifact_dir / actual_code / actual.components[0].relative_storage_path
    )
    component_path.write_bytes(component_path.read_bytes() + b'{"tampered":true}\n')
    tampered = SnapshotVerifier(db_engine).verify(actual_code)
    assert not tampered.artifact_verified
    assert any(issue.code == "COMPONENT_HASH_MISMATCH" for issue in tampered.issues)
