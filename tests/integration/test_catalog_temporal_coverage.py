"""Focused PostgreSQL proofs for PR-06 catalog reference and temporal rules."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy import insert, text
from sqlalchemy.engine import Engine

from bisfin.catalog import load_catalog_manifest
from bisfin.catalog.bootstrap import CatalogBootstrapService
from bisfin.catalog.errors import CatalogConflictError
from bisfin.catalog.manifest import CatalogManifestError
from bisfin.db.errors import TemporalOverlapError, translate_database_errors
from bisfin.db.tables import instrument_spec_version
from bisfin.domain.ingestion import IngestionBatchStatus
from bisfin.repositories import create_unit_of_work_factory

_BASE = Path("tests/fixtures/catalog/catalog_bootstrap_success.json")
_T0 = "2025-01-01T00:00:00Z"
_T1 = "2025-06-01T00:00:00Z"


def test_reference_bootstrap_rerun_and_immutable_conflicts(
    db_engine: Engine, tmp_path: Path
) -> None:
    path = _write_manifest(tmp_path / "reference.json", include_unique_references=True)
    service = _service(db_engine)
    first = service.bootstrap(load_catalog_manifest(path), request_id=f"reference-{uuid4()}")
    second = service.bootstrap(load_catalog_manifest(path), request_id=f"reference-rerun-{uuid4()}")
    assert first.status is second.status is IngestionBatchStatus.SUCCEEDED

    payload = json.loads(path.read_text(encoding="utf-8"))
    instrument = payload["instruments"][0]
    provider = payload["providers"][-1]
    provider_display_name = provider["display_name"]
    feed = payload["feeds"][-1]
    feed_display_name = feed["display_name"]
    currency = payload["currencies"][-1]
    currency_minor_unit = currency["minor_unit"]
    asset_type = payload["asset_types"][-1]
    asset_type_display_name = asset_type["display_name"]
    venue = payload["venues"][-1]
    timeframe = payload["timeframes"][-1]
    with db_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM catalog.data_provider WHERE provider_code = :code"),
                {"code": provider["provider_code"]},
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(
                text("SELECT provider_id FROM catalog.data_feed WHERE feed_code = :code"),
                {"code": payload["feeds"][-1]["feed_code"]},
            ).scalar_one()
            == connection.execute(
                text("SELECT provider_id FROM catalog.data_provider WHERE provider_code = :code"),
                {"code": provider["provider_code"]},
            ).scalar_one()
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM catalog.instrument_identifier "
                    "WHERE identifier_value = :isin"
                ),
                {"isin": instrument["isin"]},
            ).scalar_one()
            == 1
        )

    payload["manifest_id"] = f"provider-conflict-{uuid4()}"
    payload["providers"][-1]["display_name"] = "Conflicting Provider"
    conflict = _write_payload(tmp_path / "provider-conflict.json", payload)
    with pytest.raises(CatalogConflictError):
        service.bootstrap(
            load_catalog_manifest(conflict), request_id=f"provider-conflict-{uuid4()}"
        )
    with db_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT display_name FROM catalog.data_provider WHERE provider_code = :code"),
                {"code": provider["provider_code"]},
            ).scalar_one()
            == provider_display_name
        )

    payload["manifest_id"] = f"venue-conflict-{uuid4()}"
    payload["providers"][-1]["display_name"] = provider["display_name"]
    payload["venues"][-1]["timezone_name"] = "UTC"
    with pytest.raises(CatalogConflictError):
        service.bootstrap(
            load_catalog_manifest(_write_payload(tmp_path / "venue-conflict.json", payload)),
            request_id=f"venue-conflict-{uuid4()}",
        )
    payload["venues"][-1]["timezone_name"] = venue["timezone_name"]
    payload["manifest_id"] = f"timeframe-conflict-{uuid4()}"
    payload["timeframes"][-1]["session_aligned"] = False
    with pytest.raises(CatalogConflictError):
        service.bootstrap(
            load_catalog_manifest(_write_payload(tmp_path / "timeframe-conflict.json", payload)),
            request_id=f"timeframe-conflict-{uuid4()}",
        )
    with db_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT session_aligned FROM catalog.timeframe WHERE timeframe_code = :code"),
                {"code": timeframe["timeframe_code"]},
            ).scalar_one()
            is True
        )

    payload["timeframes"][-1]["session_aligned"] = timeframe["session_aligned"]
    payload["manifest_id"] = f"feed-conflict-{uuid4()}"
    payload["feeds"][-1]["display_name"] = "Conflicting Feed"
    with pytest.raises(CatalogConflictError):
        service.bootstrap(
            load_catalog_manifest(_write_payload(tmp_path / "feed-conflict.json", payload)),
            request_id=f"feed-conflict-{uuid4()}",
        )
    with db_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT display_name FROM catalog.data_feed WHERE feed_code = :code"),
                {"code": feed["feed_code"]},
            ).scalar_one()
            == feed_display_name
        )

    payload["feeds"][-1]["display_name"] = feed_display_name
    payload["manifest_id"] = f"currency-conflict-{uuid4()}"
    payload["currencies"][-1]["minor_unit"] = currency_minor_unit + 1
    with pytest.raises(CatalogConflictError):
        service.bootstrap(
            load_catalog_manifest(_write_payload(tmp_path / "currency-conflict.json", payload)),
            request_id=f"currency-conflict-{uuid4()}",
        )
    with db_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT minor_unit FROM catalog.currency WHERE currency_code = :code"),
                {"code": currency["currency_code"]},
            ).scalar_one()
            == currency_minor_unit
        )

    payload["currencies"][-1]["minor_unit"] = currency_minor_unit
    payload["manifest_id"] = f"asset-type-conflict-{uuid4()}"
    payload["asset_types"][-1]["display_name"] = "Conflicting Asset Type"
    with pytest.raises(CatalogConflictError):
        service.bootstrap(
            load_catalog_manifest(_write_payload(tmp_path / "asset-type-conflict.json", payload)),
            request_id=f"asset-type-conflict-{uuid4()}",
        )
    with db_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT display_name FROM catalog.asset_type WHERE asset_type_code = :code"),
                {"code": asset_type["asset_type_code"]},
            ).scalar_one()
            == asset_type_display_name
        )


def test_symbol_rename_has_adjacent_intervals_and_exact_pit_boundary(
    db_engine: Engine, tmp_path: Path
) -> None:
    initial = _write_manifest(tmp_path / "initial.json")
    payload = json.loads(initial.read_text(encoding="utf-8"))
    old_symbol = payload["instruments"][0]["provider_symbol"]
    service = _service(db_engine)
    service.bootstrap(load_catalog_manifest(initial), request_id=f"rename-initial-{uuid4()}")
    new_symbol = f"NEW{uuid4().hex[:10].upper()}"
    rename = _renamed_payload(payload, new_symbol)
    service.bootstrap(
        load_catalog_manifest(_write_payload(tmp_path / "rename.json", rename)),
        request_id=f"rename-{uuid4()}",
    )
    provider_id, instrument_id = _provider_and_instrument(db_engine, rename)
    with db_engine.connect() as connection:
        intervals = (
            connection.execute(
                text(
                    "SELECT identifier_value, valid_from, valid_to "
                    "FROM catalog.instrument_identifier "
                    "WHERE instrument_id = :instrument_id AND identifier_type = 'BRSAPI_L18' "
                    "ORDER BY valid_from"
                ),
                {"instrument_id": instrument_id},
            )
            .mappings()
            .all()
        )
    assert [
        (
            row["identifier_value"],
            row["valid_from"].isoformat(),
            row["valid_to"].isoformat() if row["valid_to"] else None,
        )
        for row in intervals
    ] == [
        (old_symbol, _T0.replace("Z", "+00:00"), _T1.replace("Z", "+00:00")),
        (new_symbol, _T1.replace("Z", "+00:00"), None),
    ]
    factory = create_unit_of_work_factory(db_engine)
    with factory() as unit_of_work:
        before = datetime(2025, 5, 31, 23, 59, tzinfo=UTC)
        boundary = datetime(2025, 6, 1, tzinfo=UTC)
        assert unit_of_work.instruments.find_by_identifier(
            provider_id, "BRSAPI_L18", old_symbol, before
        )
        assert (
            unit_of_work.instruments.find_by_identifier(
                provider_id, "BRSAPI_L18", new_symbol, before
            )
            is None
        )
        assert (
            unit_of_work.instruments.find_by_identifier(
                provider_id, "BRSAPI_L18", old_symbol, boundary
            )
            is None
        )
        assert unit_of_work.instruments.find_by_identifier(
            provider_id, "BRSAPI_L18", new_symbol, boundary
        )
    before_count = len(intervals)
    service.bootstrap(
        load_catalog_manifest(_write_payload(tmp_path / "rename-rerun.json", rename)),
        request_id=f"rename-rerun-{uuid4()}",
    )
    with db_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM catalog.instrument_identifier WHERE instrument_id = :id"
                ),
                {"id": instrument_id},
            ).scalar_one()
            == before_count + 1
        )  # ISIN plus the two symbol intervals


def test_invalid_rename_and_changed_spec_leave_history_consistent(
    db_engine: Engine, tmp_path: Path
) -> None:
    initial = _write_manifest(tmp_path / "spec-initial.json")
    payload = json.loads(initial.read_text(encoding="utf-8"))
    service = _service(db_engine)
    service.bootstrap(load_catalog_manifest(initial), request_id=f"spec-initial-{uuid4()}")
    provider_id, instrument_id = _provider_and_instrument(db_engine, payload)
    with db_engine.connect() as connection:
        before_invalid_history = (
            connection.execute(
                text(
                    "SELECT count(*) FROM catalog.instrument_identifier WHERE instrument_id = :id"
                ),
                {"id": instrument_id},
            ).scalar_one(),
            connection.execute(text("SELECT count(*) FROM ingest.ingestion_batch")).scalar_one(),
            connection.execute(text("SELECT count(*) FROM ingest.raw_event")).scalar_one(),
        )

    invalid = _renamed_payload(payload, f"BAD{uuid4().hex[:10].upper()}")
    invalid["instruments"][0]["rename_effective_from"] = "2024-12-31T00:00:00Z"
    with pytest.raises(CatalogManifestError):
        load_catalog_manifest(_write_payload(tmp_path / "invalid-rename.json", invalid))
    with db_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM catalog.instrument_identifier WHERE instrument_id = :id"
                ),
                {"id": instrument_id},
            ).scalar_one(),
            connection.execute(text("SELECT count(*) FROM ingest.ingestion_batch")).scalar_one(),
            connection.execute(text("SELECT count(*) FROM ingest.raw_event")).scalar_one(),
        ) == before_invalid_history

    changed = json.loads(json.dumps(payload))
    changed["manifest_id"] = f"spec-change-{uuid4()}"
    changed["instruments"][0]["price_tick"] = "10.0"
    changed["instruments"][0]["spec_effective_from"] = _T1
    service.bootstrap(
        load_catalog_manifest(_write_payload(tmp_path / "spec-change.json", changed)),
        request_id=f"spec-change-{uuid4()}",
    )
    factory = create_unit_of_work_factory(db_engine)
    with factory() as unit_of_work:
        old = unit_of_work.instruments.get_active_spec(
            instrument_id, datetime(2025, 5, 31, 23, 59, tzinfo=UTC)
        )
        new = unit_of_work.instruments.get_active_spec(
            instrument_id, datetime(2025, 6, 1, tzinfo=UTC)
        )
        after = unit_of_work.instruments.get_active_spec(
            instrument_id, datetime(2025, 6, 1, 0, 1, tzinfo=UTC)
        )
    assert old and old.price_tick == 1
    assert new and new.price_tick == 10
    assert after and after.price_tick == 10
    with db_engine.connect() as connection:
        versions = (
            connection.execute(
                text(
                    "SELECT effective_from, effective_to FROM catalog.instrument_spec_version "
                    "WHERE instrument_id = :id ORDER BY effective_from"
                ),
                {"id": instrument_id},
            )
            .mappings()
            .all()
        )
    assert len(versions) == 2
    assert versions[0]["effective_from"].isoformat() == _T0.replace("Z", "+00:00")
    assert versions[0]["effective_to"] == versions[1]["effective_from"]
    assert versions[1]["effective_from"].isoformat() == _T1.replace("Z", "+00:00")
    assert versions[1]["effective_to"] is None
    assert provider_id > 0


def test_specification_exact_and_decimal_equivalent_reruns_are_noops(
    db_engine: Engine, tmp_path: Path
) -> None:
    initial = _write_manifest(tmp_path / "spec-noop-initial.json")
    payload = json.loads(initial.read_text(encoding="utf-8"))
    service = _service(db_engine)
    service.bootstrap(load_catalog_manifest(initial), request_id=f"spec-noop-initial-{uuid4()}")
    _, instrument_id = _provider_and_instrument(db_engine, payload)
    with db_engine.connect() as connection:
        before = (
            connection.execute(
                text(
                    "SELECT effective_from, effective_to FROM catalog.instrument_spec_version "
                    "WHERE instrument_id = :id ORDER BY effective_from"
                ),
                {"id": instrument_id},
            )
            .mappings()
            .all()
        )

    service.bootstrap(load_catalog_manifest(initial), request_id=f"spec-noop-rerun-{uuid4()}")
    equivalent = json.loads(json.dumps(payload))
    equivalent["manifest_id"] = f"spec-equivalent-{uuid4()}"
    equivalent["instruments"][0]["price_tick"] = "1.0"
    service.bootstrap(
        load_catalog_manifest(_write_payload(tmp_path / "spec-equivalent.json", equivalent)),
        request_id=f"spec-equivalent-{uuid4()}",
    )
    with db_engine.connect() as connection:
        after = (
            connection.execute(
                text(
                    "SELECT effective_from, effective_to FROM catalog.instrument_spec_version "
                    "WHERE instrument_id = :id ORDER BY effective_from"
                ),
                {"id": instrument_id},
            )
            .mappings()
            .all()
        )
    assert after == before


def test_specification_overlap_is_rejected_by_postgresql_and_rolls_back(
    db_engine: Engine, tmp_path: Path
) -> None:
    initial = _write_manifest(tmp_path / "spec-overlap-initial.json")
    payload = json.loads(initial.read_text(encoding="utf-8"))
    _service(db_engine).bootstrap(
        load_catalog_manifest(initial), request_id=f"spec-overlap-initial-{uuid4()}"
    )
    _, instrument_id = _provider_and_instrument(db_engine, payload)
    factory = create_unit_of_work_factory(db_engine)
    with pytest.raises(TemporalOverlapError) as error:
        with factory.create_temporal_write() as unit_of_work:
            with translate_database_errors(operation="test instrument specification overlap"):
                unit_of_work.connection.execute(
                    insert(instrument_spec_version).values(
                        instrument_id=instrument_id,
                        effective_from=datetime(2025, 2, 1, tzinfo=UTC),
                        effective_to=datetime(2025, 3, 1, tzinfo=UTC),
                        price_tick=1,
                        quantity_step=1,
                        lot_size=1,
                        contract_multiplier=1,
                        metadata={},
                    )
                )
    assert error.value.sqlstate == "23P01"
    with db_engine.connect() as connection:
        versions = (
            connection.execute(
                text(
                    "SELECT effective_from, effective_to FROM catalog.instrument_spec_version "
                    "WHERE instrument_id = :id ORDER BY effective_from"
                ),
                {"id": instrument_id},
            )
            .mappings()
            .all()
        )
    assert len(versions) == 1
    assert versions[0]["effective_from"].isoformat() == _T0.replace("Z", "+00:00")
    assert versions[0]["effective_to"] is None


def test_specification_conflict_rolls_back_prior_canonical_write_but_keeps_audit(
    db_engine: Engine, tmp_path: Path
) -> None:
    initial = _write_manifest(tmp_path / "spec-rollback-initial.json")
    payload = json.loads(initial.read_text(encoding="utf-8"))
    service = _service(db_engine)
    service.bootstrap(load_catalog_manifest(initial), request_id=f"spec-rollback-initial-{uuid4()}")
    original = cast(dict[str, Any], payload["instruments"][0])
    earlier_write = json.loads(json.dumps(original))
    token = uuid4().hex.upper()
    earlier_write.update(
        {
            "stable_key": f"a-spec-rollback-{token}",
            "provider_symbol": f"ROLL{token[:9]}",
            "isin": f"IR{token[:9]}1",
            "name_fa": f"Rollback {token[:8]}",
            "name_en": f"Rollback {token[:8]}",
        }
    )
    conflicting = json.loads(json.dumps(original))
    conflicting["price_tick"] = "2"
    payload["manifest_id"] = f"spec-rollback-{uuid4()}"
    payload["instruments"] = [earlier_write, conflicting]
    request_id = f"spec-rollback-{uuid4()}"
    with pytest.raises(CatalogConflictError):
        service.bootstrap(
            load_catalog_manifest(_write_payload(tmp_path / "spec-rollback.json", payload)),
            request_id=request_id,
        )
    with db_engine.connect() as connection:
        batch_status = connection.execute(
            text("SELECT status FROM ingest.ingestion_batch WHERE request_id = :id"),
            {"id": request_id},
        ).scalar_one()
        raw_count = connection.execute(
            text(
                "SELECT count(*) FROM ingest.raw_event WHERE ingestion_batch_id = "
                "(SELECT ingestion_batch_id FROM ingest.ingestion_batch WHERE request_id = :id)"
            ),
            {"id": request_id},
        ).scalar_one()
    assert batch_status == "FAILED"
    assert raw_count == 2
    assert _identifier_count(db_engine, earlier_write["isin"]) == 0


def test_concurrent_identical_rename_converges(db_engine: Engine, tmp_path: Path) -> None:
    initial = _write_manifest(tmp_path / "concurrent-initial.json")
    payload = json.loads(initial.read_text(encoding="utf-8"))
    _service(db_engine).bootstrap(
        load_catalog_manifest(initial), request_id=f"rename-base-{uuid4()}"
    )
    renamed = load_catalog_manifest(
        _write_payload(
            tmp_path / "concurrent-rename.json",
            _renamed_payload(payload, f"NEW{uuid4().hex[:10].upper()}"),
        )
    )
    barrier = Barrier(2)

    def apply() -> IngestionBatchStatus:
        barrier.wait(timeout=10)
        return (
            _service(db_engine).bootstrap(renamed, request_id=f"rename-concurrent-{uuid4()}").status
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(lambda _: apply(), range(2), timeout=20))
    assert statuses == [IngestionBatchStatus.SUCCEEDED, IngestionBatchStatus.SUCCEEDED]
    _, instrument_id = _provider_and_instrument(db_engine, payload)
    old_symbol = payload["instruments"][0]["provider_symbol"]
    new_symbol = renamed.manifest.instruments[0].provider_symbol
    with db_engine.connect() as connection:
        rows = (
            connection.execute(
                text(
                    "SELECT identifier_value, valid_from, valid_to "
                    "FROM catalog.instrument_identifier WHERE instrument_id = :id "
                    "AND identifier_type = 'BRSAPI_L18' ORDER BY valid_from"
                ),
                {"id": instrument_id},
            )
            .mappings()
            .all()
        )
    assert len(rows) == 2
    assert rows[0]["identifier_value"] == old_symbol
    assert rows[0]["valid_to"].isoformat() == _T1.replace("Z", "+00:00")
    assert rows[1]["identifier_value"] == new_symbol
    assert rows[1]["valid_from"].isoformat() == _T1.replace("Z", "+00:00")
    assert rows[1]["valid_to"] is None


def _service(engine: Engine) -> CatalogBootstrapService:
    return CatalogBootstrapService(
        unit_of_work_factory=create_unit_of_work_factory(engine).create_temporal_write
    )


def _write_manifest(path: Path, *, include_unique_references: bool = False) -> Path:
    payload = json.loads(_BASE.read_text(encoding="utf-8"))
    token = uuid4().hex.upper()
    instrument = payload["instruments"][0]
    instrument.update(
        {
            "stable_key": f"temporal-{token}",
            "provider_symbol": f"OLD{token[:10]}",
            "isin": f"IR{token[:9]}1",
            "name_fa": f"Temporal {token[:8]}",
            "name_en": f"Temporal {token[:8]}",
        }
    )
    payload["manifest_id"] = f"temporal-{token}"
    if include_unique_references:
        code = f"REF{token[:10]}"
        currency = f"X{token[:2]}"
        asset = f"AS{token[:8]}"
        venue = f"V{token[:10]}"
        timeframe = f"TF{token[:8]}"
        payload["providers"].append(
            {
                "provider_code": code,
                "display_name": code,
                "provider_kind": "VENDOR",
                "base_url": None,
                "default_timezone": "UTC",
                "metadata": {},
            }
        )
        payload["feeds"].append(
            {
                "provider_code": code,
                "feed_code": f"FEED{token[:10]}",
                "display_name": code,
                "data_kind": "INSTRUMENT",
                "native_timezone": "UTC",
                "parser_version": "v1",
                "active_from": None,
                "active_to": None,
                "metadata": {},
            }
        )
        payload["currencies"].append(
            {
                "currency_code": currency,
                "display_name": currency,
                "minor_unit": 2,
                "is_fiat": False,
                "metadata": {},
            }
        )
        payload["asset_types"].append(
            {"asset_type_code": asset, "display_name": asset, "description": None}
        )
        payload["venues"].append(
            {
                "venue_code": venue,
                "display_name": venue,
                "mic_code": None,
                "country_code": "ZZ",
                "timezone_name": "UTC",
                "base_currency_code": currency,
                "metadata": {},
            }
        )
        payload["timeframes"].append(
            {
                "timeframe_code": timeframe,
                "display_name": timeframe,
                "duration_seconds": 60,
                "calendar_unit": "FIXED",
                "session_aligned": True,
            }
        )
        payload["provider_market_mappings"].append(
            {"provider_code": code, "provider_market": "TEST", "venue_code": venue}
        )
        instrument.update(
            {
                "provider_code": code,
                "venue_code": venue,
                "currency_code": currency,
                "asset_type_code": asset,
            }
        )
    return _write_payload(path, payload)


def _renamed_payload(payload: dict[str, Any], new_symbol: str) -> dict[str, Any]:
    renamed = json.loads(json.dumps(payload))
    renamed["manifest_id"] = f"rename-{uuid4()}"
    renamed["instruments"][0].update(
        {
            "previous_symbol": renamed["instruments"][0]["provider_symbol"],
            "provider_symbol": new_symbol,
            "rename_effective_from": _T1,
        }
    )
    return cast(dict[str, Any], renamed)


def _write_payload(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _provider_and_instrument(engine: Engine, payload: dict[str, Any]) -> tuple[int, int]:
    instrument = cast(dict[str, Any], payload["instruments"][0])
    with engine.connect() as connection:
        return (
            connection.execute(
                text("SELECT provider_id FROM catalog.data_provider WHERE provider_code = :code"),
                {"code": instrument["provider_code"]},
            ).scalar_one(),
            connection.execute(
                text(
                    "SELECT instrument_id FROM catalog.instrument_identifier "
                    "WHERE identifier_type = 'ISIN' AND identifier_value = :isin"
                ),
                {"isin": instrument["isin"]},
            ).scalar_one(),
        )


def _identifier_count(engine: Engine, identifier_value: str) -> int:
    with engine.connect() as connection:
        return cast(
            int,
            connection.execute(
                text(
                    "SELECT count(*) FROM catalog.instrument_identifier "
                    "WHERE identifier_value = :value"
                ),
                {"value": identifier_value},
            ).scalar_one(),
        )
