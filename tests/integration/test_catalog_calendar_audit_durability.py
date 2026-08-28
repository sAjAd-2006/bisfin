"""Real PostgreSQL proofs that audit commits outlive canonical failures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from bisfin.calendar import load_calendar_manifest, validate_calendar_manifest
from bisfin.calendar.errors import CalendarConflictError
from bisfin.calendar.importer import TradingCalendarImportService
from bisfin.catalog import load_catalog_manifest
from bisfin.catalog.bootstrap import (
    CatalogBootstrapService,
    CatalogValidationMode,
    SymbolProviderMismatchError,
    SymbolValidationError,
)
from bisfin.catalog.errors import CatalogConflictError
from bisfin.catalog.manifest import CatalogManifestError
from bisfin.integrations.brsapi import FixtureBrsApiSymbolClient
from bisfin.repositories import create_unit_of_work_factory

_CATALOG = Path("tests/fixtures/catalog/catalog_bootstrap_success.json")
_MISMATCH_FIXTURE = Path("tests/fixtures/brsapi")
_CALENDAR = Path("tests/fixtures/calendar/tse_regular_success.json")


def test_provider_mismatch_keeps_batch_and_raw_evidence(db_engine: Engine, tmp_path: Path) -> None:
    request_id = f"durable-symbol-{uuid4()}"
    fixture_dir = _copy_index_with_mismatch(tmp_path)
    before_instruments = _instrument_count(db_engine)
    service = CatalogBootstrapService(
        unit_of_work_factory=create_unit_of_work_factory(db_engine).create_temporal_write
    )

    with pytest.raises(SymbolProviderMismatchError):
        service.bootstrap(
            load_catalog_manifest(_CATALOG),
            validation_mode=CatalogValidationMode.FIXTURE_VALIDATE,
            symbol_client=FixtureBrsApiSymbolClient(fixture_dir),
            request_id=request_id,
        )

    with db_engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT status, payload_sha256 FROM ingest.ingestion_batch "
                    "WHERE request_id = :id"
                ),
                {"id": request_id},
            )
            .mappings()
            .one()
        )
        raw = (
            connection.execute(
                text(
                    "SELECT raw_payload, payload_sha256 FROM ingest.raw_event "
                    "WHERE ingestion_batch_id = "
                    "(SELECT ingestion_batch_id FROM ingest.ingestion_batch "
                    "WHERE request_id = :id)"
                ),
                {"id": request_id},
            )
            .mappings()
            .all()
        )
    assert row["status"] == "QUARANTINED"
    assert row["payload_sha256"] is None
    assert len(raw) == 2
    assert all(item["raw_payload"] for item in raw)
    assert all(item["payload_sha256"] for item in raw)
    assert _instrument_count(db_engine) == before_instruments


def test_malformed_provider_response_keeps_exact_raw_evidence(
    db_engine: Engine, tmp_path: Path
) -> None:
    request_id = f"durable-malformed-{uuid4()}"
    service = CatalogBootstrapService(
        unit_of_work_factory=create_unit_of_work_factory(db_engine).create_temporal_write
    )
    with pytest.raises(SymbolValidationError):
        service.bootstrap(
            load_catalog_manifest(_CATALOG),
            validation_mode=CatalogValidationMode.FIXTURE_VALIDATE,
            symbol_client=FixtureBrsApiSymbolClient(
                _copy_index_with_response(tmp_path, "symbol_malformed_json.txt")
            ),
            request_id=request_id,
        )
    with db_engine.connect() as connection:
        batch = connection.execute(
            text("SELECT status FROM ingest.ingestion_batch WHERE request_id = :id"),
            {"id": request_id},
        ).scalar_one()
        raw = (
            connection.execute(
                text(
                    "SELECT raw_payload FROM ingest.raw_event WHERE ingestion_batch_id = "
                    "(SELECT ingestion_batch_id FROM ingest.ingestion_batch "
                    "WHERE request_id = :id) AND validation_status = 'REJECTED'"
                ),
                {"id": request_id},
            )
            .mappings()
            .one()["raw_payload"]
        )
    assert batch == "QUARANTINED"
    assert raw["response_bytes_hex"]
    assert raw["response_sha256"]


def test_canonical_conflict_keeps_catalog_raw_audit_and_existing_rows(
    db_engine: Engine, tmp_path: Path
) -> None:
    factory = create_unit_of_work_factory(db_engine)
    service = CatalogBootstrapService(unit_of_work_factory=factory.create_temporal_write)
    service.bootstrap(load_catalog_manifest(_CATALOG), request_id=f"catalog-prereq-{uuid4()}")
    with db_engine.connect() as connection:
        before = connection.execute(
            text(
                "SELECT instrument.active_from FROM catalog.instrument AS instrument "
                "JOIN catalog.instrument_identifier AS identifier "
                "ON identifier.instrument_id = instrument.instrument_id "
                "WHERE identifier.identifier_type = 'ISIN' "
                "AND identifier.identifier_value = 'IRO1MSMI0001'"
            )
        ).scalar_one()
        canonical_counts = _catalog_counts(connection)
    conflict_path = tmp_path / "canonical-conflict.json"
    conflict = json.loads(_CATALOG.read_text(encoding="utf-8"))
    conflict["manifest_id"] = f"canonical-conflict-{uuid4()}"
    conflict["instruments"][0]["active_from"] = "2025-02-01T00:00:00Z"
    conflict_path.write_text(json.dumps(conflict), encoding="utf-8")
    request_id = f"canonical-conflict-{uuid4()}"

    with pytest.raises(CatalogConflictError):
        service.bootstrap(load_catalog_manifest(conflict_path), request_id=request_id)

    with db_engine.connect() as connection:
        batch = connection.execute(
            text("SELECT status FROM ingest.ingestion_batch WHERE request_id = :id"),
            {"id": request_id},
        ).scalar_one()
        raw_count = connection.execute(
            text(
                "SELECT count(*) FROM ingest.raw_event WHERE ingestion_batch_id = "
                "(SELECT ingestion_batch_id FROM ingest.ingestion_batch "
                "WHERE request_id = :id)"
            ),
            {"id": request_id},
        ).scalar_one()
        after = connection.execute(
            text(
                "SELECT instrument.active_from FROM catalog.instrument AS instrument "
                "JOIN catalog.instrument_identifier AS identifier "
                "ON identifier.instrument_id = instrument.instrument_id "
                "WHERE identifier.identifier_type = 'ISIN' "
                "AND identifier.identifier_value = 'IRO1MSMI0001'"
            )
        ).scalar_one()
        assert _catalog_counts(connection) == canonical_counts
    assert batch == "FAILED"
    assert raw_count == 1
    assert after == before


def test_invalid_manifest_creates_no_audit_or_canonical_rows(
    db_engine: Engine, tmp_path: Path
) -> None:
    with db_engine.connect() as connection:
        before_batches = connection.execute(
            text("SELECT count(*) FROM ingest.ingestion_batch")
        ).scalar_one()
        before_raw = connection.execute(text("SELECT count(*) FROM ingest.raw_event")).scalar_one()
        before_instruments = _instrument_count(db_engine)
    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text('{"schema_version": 2}', encoding="utf-8")
    with pytest.raises(CatalogManifestError):
        load_catalog_manifest(invalid_path)
    with db_engine.connect() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM ingest.ingestion_batch")).scalar_one()
            == before_batches
        )
        assert (
            connection.execute(text("SELECT count(*) FROM ingest.raw_event")).scalar_one()
            == before_raw
        )
    assert _instrument_count(db_engine) == before_instruments


def test_calendar_conflict_keeps_raw_rows_and_rolls_back_new_sessions(
    db_engine: Engine, tmp_path: Path
) -> None:
    factory = create_unit_of_work_factory(db_engine)
    # Ensure canonical prerequisites; this is bootstrap, not manual SQL seed.
    CatalogBootstrapService(unit_of_work_factory=factory.create_temporal_write).bootstrap(
        load_catalog_manifest(_CATALOG), request_id=f"calendar-prereq-{uuid4()}"
    )
    service = TradingCalendarImportService(unit_of_work_factory=factory)
    conflict_date = "2025-04-12"
    service.import_calendar(
        validate_calendar_manifest(
            load_calendar_manifest(
                _write_calendar(
                    tmp_path / "seed.json",
                    [_session(conflict_date, "09:00:00", "12:30:00", "DECLARED_OPEN")],
                )
            )
        ),
        request_id=f"calendar-prereq-{uuid4()}",
    )
    request_id = f"durable-calendar-{uuid4()}"
    with pytest.raises(CalendarConflictError):
        service.import_calendar(
            validate_calendar_manifest(
                load_calendar_manifest(
                    _write_calendar(
                        tmp_path / "conflict.json",
                        [
                            _session("2025-04-10", "09:00:00", "12:30:00", "DECLARED_OPEN"),
                            _session("2025-04-11", "09:00:00", "12:30:00", "DECLARED_OPEN"),
                            _session(
                                conflict_date, "10:00:00", "12:00:00", "DECLARED_CONFLICT_FIXTURE"
                            ),
                        ],
                    )
                )
            ),
            request_id=request_id,
        )
    with db_engine.connect() as connection:
        batch = connection.execute(
            text("SELECT status FROM ingest.ingestion_batch WHERE request_id = :id"),
            {"id": request_id},
        ).scalar_one()
        raw_count = connection.execute(
            text(
                "SELECT count(*) FROM ingest.raw_event WHERE ingestion_batch_id = "
                "(SELECT ingestion_batch_id FROM ingest.ingestion_batch "
                "WHERE request_id = :id)"
            ),
            {"id": request_id},
        ).scalar_one()
        rolled_back = connection.execute(
            text(
                "SELECT count(*) FROM catalog.trading_session "
                "WHERE trading_date IN ('2025-04-10', '2025-04-11')"
            )
        ).scalar_one()
    assert batch == "FAILED"
    assert raw_count == 3
    assert rolled_back == 0


def _copy_index_with_mismatch(tmp_path: Path) -> Path:
    """Use the indexed fixture client with a deterministic mismatch response."""

    return _copy_index_with_response(tmp_path, "symbol_isin_mismatch.json")


def _copy_index_with_response(tmp_path: Path, fixture_name: str) -> Path:
    (tmp_path / "femeli.json").write_bytes((_MISMATCH_FIXTURE / fixture_name).read_bytes())
    (tmp_path / "index.json").write_text(
        '{"schema_version":1,"symbols":{"فملی":"femeli.json"}}',
        encoding="utf-8",
    )
    return tmp_path


def _write_calendar(path: Path, sessions: list[dict[str, object]]) -> Path:
    dates = [str(session["trading_date"]) for session in sessions]
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "calendar_id": f"durability-{uuid4()}",
                "venue_code": "TSE",
                "timezone": "Asia/Tehran",
                "date_from": min(dates),
                "date_to": max(dates),
                "sessions": sessions,
            }
        ),
        encoding="utf-8",
    )
    return path


def _session(
    trading_date: str, open_time: str, close_time: str, source_status: str
) -> dict[str, object]:
    return {
        "trading_date": trading_date,
        "session_code": "REGULAR",
        "is_trading_day": True,
        "open_local_time": open_time,
        "close_local_time": close_time,
        "source_status": source_status,
    }


def _instrument_count(engine: Engine) -> int:
    with engine.connect() as connection:
        return cast(
            int, connection.execute(text("SELECT count(*) FROM catalog.instrument")).scalar_one()
        )


def _catalog_counts(connection: Connection) -> tuple[int, ...]:
    return tuple(
        connection.execute(text(query)).scalar_one()
        for query in (
            "SELECT count(*) FROM catalog.data_provider",
            "SELECT count(*) FROM catalog.data_feed",
            "SELECT count(*) FROM catalog.currency",
            "SELECT count(*) FROM catalog.asset_type",
            "SELECT count(*) FROM catalog.venue",
            "SELECT count(*) FROM catalog.timeframe",
            "SELECT count(*) FROM catalog.instrument",
            "SELECT count(*) FROM catalog.instrument_identifier",
            "SELECT count(*) FROM catalog.instrument_spec_version",
        )
    )
