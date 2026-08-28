"""Two-connection PostgreSQL proofs for catalog and calendar convergence."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import cast
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Engine

from bisfin.calendar import load_calendar_manifest, validate_calendar_manifest
from bisfin.calendar.importer import TradingCalendarImportService
from bisfin.catalog import load_catalog_manifest
from bisfin.catalog.bootstrap import CatalogBootstrapService
from bisfin.catalog.errors import CatalogConflictError
from bisfin.catalog.manifest import CatalogManifestDocument
from bisfin.domain.ingestion import IngestionBatchStatus
from bisfin.repositories import create_unit_of_work_factory

_CATALOG = Path("tests/fixtures/catalog/catalog_bootstrap_success.json")


def test_concurrent_identical_catalog_bootstrap_creates_one_instrument(
    db_engine: Engine, tmp_path: Path
) -> None:
    document = load_catalog_manifest(_write_catalog(tmp_path / "catalog.json"))
    barrier = Barrier(2)

    def bootstrap() -> IngestionBatchStatus:
        barrier.wait(timeout=10)
        factory = create_unit_of_work_factory(db_engine)
        result = CatalogBootstrapService(
            unit_of_work_factory=factory.create_temporal_write
        ).bootstrap(document, request_id=f"concurrent-catalog-{uuid4()}")
        return result.status

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(lambda _: bootstrap(), range(2), timeout=20))

    assert statuses == [IngestionBatchStatus.SUCCEEDED, IngestionBatchStatus.SUCCEEDED]
    assert _instrument_count(db_engine, document.manifest.instruments[0].isin) == 1


def test_concurrent_conflicting_symbol_ownership_cannot_both_commit(
    db_engine: Engine, tmp_path: Path
) -> None:
    first = load_catalog_manifest(_write_catalog(tmp_path / "first.json"))
    second_payload = json.loads((tmp_path / "first.json").read_text(encoding="utf-8"))
    second_payload["manifest_id"] = f"conflicting-{uuid4()}"
    second_payload["instruments"][0]["provider_symbol"] = f"SYM{uuid4().hex[:12].upper()}"
    second_path = tmp_path / "second.json"
    second_path.write_text(json.dumps(second_payload), encoding="utf-8")
    second = load_catalog_manifest(second_path)
    barrier = Barrier(2)

    def bootstrap(document: CatalogManifestDocument) -> object:
        barrier.wait(timeout=10)
        factory = create_unit_of_work_factory(db_engine)
        try:
            return (
                CatalogBootstrapService(unit_of_work_factory=factory.create_temporal_write)
                .bootstrap(document, request_id=f"conflicting-catalog-{uuid4()}")
                .status
            )
        except CatalogConflictError:
            return CatalogConflictError

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(bootstrap, (first, second), timeout=20))

    assert outcomes.count(IngestionBatchStatus.SUCCEEDED) == 1
    assert outcomes.count(CatalogConflictError) == 1
    assert _instrument_count(db_engine, first.manifest.instruments[0].isin) == 1


def test_concurrent_identical_calendar_import_creates_no_duplicate_sessions(
    db_engine: Engine, tmp_path: Path
) -> None:
    factory = create_unit_of_work_factory(db_engine)
    CatalogBootstrapService(unit_of_work_factory=factory.create_temporal_write).bootstrap(
        load_catalog_manifest(_CATALOG), request_id=f"calendar-concurrency-prereq-{uuid4()}"
    )
    document = validate_calendar_manifest(load_calendar_manifest(_write_calendar(tmp_path)))
    barrier = Barrier(2)

    def import_calendar() -> IngestionBatchStatus:
        barrier.wait(timeout=10)
        result = TradingCalendarImportService(
            unit_of_work_factory=create_unit_of_work_factory(db_engine)
        ).import_calendar(document, request_id=f"concurrent-calendar-{uuid4()}")
        return result.status

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(lambda _: import_calendar(), range(2), timeout=20))

    assert statuses == [IngestionBatchStatus.SUCCEEDED, IngestionBatchStatus.SUCCEEDED]
    with db_engine.connect() as connection:
        count = connection.execute(
            text(
                "SELECT count(*) FROM catalog.trading_session "
                "WHERE trading_date IN (DATE '2025-05-10', DATE '2025-05-11')"
            )
        ).scalar_one()
    assert count == 2


def _write_catalog(path: Path) -> Path:
    payload = json.loads(_CATALOG.read_text(encoding="utf-8"))
    token = uuid4().hex.upper()
    payload["manifest_id"] = f"concurrency-{token}"
    payload["instruments"][0].update(
        {
            "stable_key": f"concurrency-{token}",
            "provider_symbol": f"SYM{token[:12]}",
            "isin": f"IR{token[:9]}1",
            "name_fa": f"Concurrency {token[:8]}",
            "name_en": f"Concurrency {token[:8]}",
        }
    )
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_calendar(directory: Path) -> Path:
    path = directory / "calendar.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "calendar_id": f"concurrency-{uuid4()}",
                "venue_code": "TSE",
                "timezone": "Asia/Tehran",
                "date_from": "2025-05-10",
                "date_to": "2025-05-11",
                "sessions": [
                    _calendar_session("2025-05-10"),
                    _calendar_session("2025-05-11"),
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _calendar_session(trading_date: str) -> dict[str, object]:
    return {
        "trading_date": trading_date,
        "session_code": "REGULAR",
        "is_trading_day": True,
        "open_local_time": "09:00:00",
        "close_local_time": "12:30:00",
        "source_status": "DECLARED_OPEN",
    }


def _instrument_count(engine: Engine, isin: str) -> int:
    with engine.connect() as connection:
        return cast(
            int,
            connection.execute(
                text(
                    "SELECT count(*) FROM catalog.instrument_identifier "
                    "WHERE identifier_type = 'ISIN' AND identifier_value = :isin"
                ),
                {"isin": isin},
            ).scalar_one(),
        )
