"""Two-connection PostgreSQL proofs for catalog and calendar convergence."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event
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


def test_concurrent_conflicting_symbol_renames_create_one_canonical_history(
    db_engine: Engine, tmp_path: Path
) -> None:
    """Two different targets for one old symbol serialize on the shared ISIN key."""

    initial_path = _write_catalog(tmp_path / "rename-base.json")
    initial = load_catalog_manifest(initial_path)
    service = CatalogBootstrapService(
        unit_of_work_factory=create_unit_of_work_factory(db_engine).create_temporal_write
    )
    service.bootstrap(initial, request_id=f"rename-base-{uuid4()}")
    old_symbol = initial.manifest.instruments[0].provider_symbol
    payload = json.loads(initial_path.read_text(encoding="utf-8"))
    first_target = f"REN{uuid4().hex[:10].upper()}"
    second_target = f"REN{uuid4().hex[:10].upper()}"
    first = load_catalog_manifest(
        _write_rename(tmp_path / "rename-first.json", payload, first_target)
    )
    second = load_catalog_manifest(
        _write_rename(tmp_path / "rename-second.json", payload, second_target)
    )
    barrier = Barrier(2)

    def apply(document: CatalogManifestDocument) -> object:
        barrier.wait(timeout=10)
        try:
            return (
                CatalogBootstrapService(
                    unit_of_work_factory=create_unit_of_work_factory(
                        db_engine
                    ).create_temporal_write
                )
                .bootstrap(document, request_id=f"rename-conflict-{uuid4()}")
                .status
            )
        except CatalogConflictError:
            return CatalogConflictError

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(apply, (first, second), timeout=20))
    assert outcomes.count(IngestionBatchStatus.SUCCEEDED) == 1
    assert outcomes.count(CatalogConflictError) == 1

    with db_engine.connect() as connection:
        instrument_id = connection.execute(
            text(
                "SELECT instrument_id FROM catalog.instrument_identifier "
                "WHERE identifier_type = 'ISIN' AND identifier_value = :isin"
            ),
            {"isin": initial.manifest.instruments[0].isin},
        ).scalar_one()
        rows = (
            connection.execute(
                text(
                    "SELECT identifier_value, valid_from, valid_to "
                    "FROM catalog.instrument_identifier "
                    "WHERE instrument_id = :id "
                    "AND identifier_type = 'BRSAPI_L18' "
                    "ORDER BY valid_from"
                ),
                {"id": instrument_id},
            )
            .mappings()
            .all()
        )
    assert len(rows) == 2
    assert rows[0]["identifier_value"] == old_symbol
    assert rows[0]["valid_to"].isoformat() == "2025-06-01T00:00:00+00:00"
    assert rows[1]["identifier_value"] in {first_target, second_target}
    assert rows[1]["valid_from"].isoformat() == "2025-06-01T00:00:00+00:00"
    assert rows[1]["valid_to"] is None


def test_independent_instrument_specification_writes_are_not_globally_serialized(
    db_engine: Engine, tmp_path: Path
) -> None:
    """A holds A's identifier/spec advisory locks while B completes before A commits.

    The logical keys differ because both the provider/identifier composite and
    the instrument specification key are unique per generated instrument.
    """

    first_path = _write_catalog(tmp_path / "independent-a.json")
    second_path = _write_catalog(tmp_path / "independent-b.json")
    first = load_catalog_manifest(first_path)
    second = load_catalog_manifest(second_path)
    service = CatalogBootstrapService(
        unit_of_work_factory=create_unit_of_work_factory(db_engine).create_temporal_write
    )
    service.bootstrap(first, request_id=f"independent-a-base-{uuid4()}")
    service.bootstrap(second, request_id=f"independent-b-base-{uuid4()}")
    first_changed = load_catalog_manifest(
        _write_spec_change(tmp_path / "independent-a-change.json", first_path, "2")
    )
    second_changed = load_catalog_manifest(
        _write_spec_change(tmp_path / "independent-b-change.json", second_path, "3")
    )
    first_definition = first_changed.manifest.instruments[0]
    holder_ready = Event()
    completed_before_release = Event()

    def apply_second() -> IngestionBatchStatus:
        if not holder_ready.wait(timeout=5):
            raise AssertionError("first transaction never reached its advisory-lock checkpoint")
        result = CatalogBootstrapService(
            unit_of_work_factory=create_unit_of_work_factory(db_engine).create_temporal_write
        ).bootstrap(second_changed, request_id=f"independent-b-change-{uuid4()}")
        completed_before_release.set()
        return result.status

    with db_engine.connect() as connection:
        provider_id = connection.execute(
            text("SELECT provider_id FROM catalog.data_provider WHERE provider_code = :code"),
            {"code": first_definition.provider_code},
        ).scalar_one()
        venue_id = connection.execute(
            text("SELECT venue_id FROM catalog.venue WHERE venue_code = :code"),
            {"code": first_definition.venue_code},
        ).scalar_one()

    factory = create_unit_of_work_factory(db_engine)
    with factory.create_temporal_write() as holder:
        holder.catalog_writer.apply_instrument(
            first_definition, provider_id=provider_id, venue_id=venue_id
        )
        held_advisory_locks = holder.connection.execute(
            text(
                "SELECT count(*) FROM pg_locks "
                "WHERE pid = pg_backend_pid() AND locktype = 'advisory' AND granted"
            )
        ).scalar_one()
        assert held_advisory_locks > 0
        holder_ready.set()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(apply_second)
            assert future.result(timeout=10) is IngestionBatchStatus.SUCCEEDED
        assert completed_before_release.is_set()
        assert holder.connection.in_transaction()
        holder.commit()

    for document, expected_tick in ((first_changed, 2), (second_changed, 3)):
        with db_engine.connect() as connection:
            instrument_id = connection.execute(
                text(
                    "SELECT instrument_id FROM catalog.instrument_identifier "
                    "WHERE identifier_type = 'ISIN' AND identifier_value = :isin"
                ),
                {"isin": document.manifest.instruments[0].isin},
            ).scalar_one()
        with factory() as unit_of_work:
            specification = unit_of_work.instruments.get_active_spec(
                instrument_id, document.manifest.instruments[0].spec_effective_from
            )
        assert specification is not None and specification.price_tick == expected_tick


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


def _write_rename(path: Path, payload: dict[str, object], target: str) -> Path:
    renamed = json.loads(json.dumps(payload))
    renamed["manifest_id"] = f"rename-{uuid4()}"
    instrument = cast(dict[str, object], renamed["instruments"][0])
    instrument.update(
        {
            "previous_symbol": instrument["provider_symbol"],
            "provider_symbol": target,
            "rename_effective_from": "2025-06-01T00:00:00Z",
        }
    )
    path.write_text(json.dumps(renamed), encoding="utf-8")
    return path


def _write_spec_change(path: Path, source: Path, price_tick: str) -> Path:
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["manifest_id"] = f"spec-change-{uuid4()}"
    payload["instruments"][0].update(
        {"price_tick": price_tick, "spec_effective_from": "2025-06-01T00:00:00Z"}
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
