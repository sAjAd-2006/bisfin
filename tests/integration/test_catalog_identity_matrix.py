"""PostgreSQL proofs for every PR-06 canonical instrument identity branch."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from bisfin.catalog import load_catalog_manifest
from bisfin.catalog.bootstrap import CatalogBootstrapService
from bisfin.catalog.errors import IdentifierRenameConflictError, InstrumentIdentityConflictError
from bisfin.catalog.manifest import CatalogManifestError
from bisfin.repositories import create_unit_of_work_factory

_BASE = Path("tests/fixtures/catalog/catalog_bootstrap_success.json")
_T1 = "2025-06-01T00:00:00Z"


def test_new_canonical_instrument_exact_rerun_and_existing_isin_same_symbol(
    db_engine: Engine, tmp_path: Path
) -> None:
    """Cases A-C: ISIN is canonical and identical declarations are no-ops."""

    manifest_path = _write_manifest(tmp_path / "canonical.json")
    document = load_catalog_manifest(manifest_path)
    instrument = document.manifest.instruments[0]
    service = _service(db_engine)
    service.bootstrap(document, request_id=f"identity-new-{uuid4()}")
    counts = _canonical_counts(db_engine, instrument.isin, instrument.provider_symbol)
    assert counts == (1, 1, 1, 1)

    provider_id, instrument_id = _provider_and_instrument(
        db_engine, instrument.provider_code, instrument.isin
    )
    factory = create_unit_of_work_factory(db_engine)
    with factory() as unit_of_work:
        isin = unit_of_work.instruments.find_by_identifier(
            provider_id, "ISIN", instrument.isin, instrument.identifier_valid_from
        )
        symbol = unit_of_work.instruments.find_by_identifier(
            provider_id,
            "BRSAPI_L18",
            instrument.provider_symbol,
            instrument.identifier_valid_from,
        )
    assert isin is not None and symbol is not None
    assert isin.instrument.instrument_id == symbol.instrument.instrument_id == instrument_id

    # Case B: an exact document rerun has a new audit request but no canonical delta.
    service.bootstrap(document, request_id=f"identity-exact-rerun-{uuid4()}")
    assert _canonical_counts(db_engine, instrument.isin, instrument.provider_symbol) == counts

    # Case C: a fresh manifest id carrying the same ISIN/symbol reuses the instrument.
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["manifest_id"] = f"identity-same-{uuid4()}"
    service.bootstrap(
        load_catalog_manifest(_write_payload(tmp_path / "same.json", payload)),
        request_id=f"identity-same-{uuid4()}",
    )
    assert _canonical_counts(db_engine, instrument.isin, instrument.provider_symbol) == counts


def test_existing_isin_with_unannounced_symbol_is_rejected(
    db_engine: Engine, tmp_path: Path
) -> None:
    """Case D: a symbol change needs the explicit temporal rename declaration."""

    initial_path = _write_manifest(tmp_path / "initial.json")
    initial = load_catalog_manifest(initial_path)
    service = _service(db_engine)
    service.bootstrap(initial, request_id=f"identity-initial-{uuid4()}")
    before = _canonical_counts(
        db_engine,
        initial.manifest.instruments[0].isin,
        initial.manifest.instruments[0].provider_symbol,
    )
    payload = json.loads(initial_path.read_text(encoding="utf-8"))
    unexpected = f"NEW{uuid4().hex[:10].upper()}"
    payload["manifest_id"] = f"identity-unannounced-{uuid4()}"
    payload["instruments"][0]["provider_symbol"] = unexpected
    with pytest.raises(IdentifierRenameConflictError):
        service.bootstrap(
            load_catalog_manifest(_write_payload(tmp_path / "unannounced.json", payload)),
            request_id=f"identity-unannounced-{uuid4()}",
        )
    assert (
        _canonical_counts(
            db_engine,
            initial.manifest.instruments[0].isin,
            initial.manifest.instruments[0].provider_symbol,
        )
        == before
    )
    assert _identifier_count(db_engine, unexpected) == 0


def test_existing_symbol_with_different_isin_and_split_identity_are_rejected(
    db_engine: Engine, tmp_path: Path
) -> None:
    """Cases E-F: neither repointing nor automated canonical merges are allowed."""

    first_path = _write_manifest(tmp_path / "first.json")
    first = load_catalog_manifest(first_path)
    service = _service(db_engine)
    service.bootstrap(first, request_id=f"identity-first-{uuid4()}")
    first_definition = first.manifest.instruments[0]
    before_instruments = _instrument_total(db_engine)

    mismatched = json.loads(first_path.read_text(encoding="utf-8"))
    mismatched["manifest_id"] = f"identity-mismatch-{uuid4()}"
    mismatched["instruments"][0]["isin"] = _new_isin()
    with pytest.raises(InstrumentIdentityConflictError):
        service.bootstrap(
            load_catalog_manifest(_write_payload(tmp_path / "mismatched.json", mismatched)),
            request_id=f"identity-mismatch-{uuid4()}",
        )
    assert _instrument_total(db_engine) == before_instruments
    assert _canonical_counts(
        db_engine, first_definition.isin, first_definition.provider_symbol
    ) == (1, 1, 1, 1)
    assert _identifier_count(db_engine, mismatched["instruments"][0]["isin"]) == 0

    second_path = _write_manifest(tmp_path / "second.json")
    second = load_catalog_manifest(second_path)
    service.bootstrap(second, request_id=f"identity-second-{uuid4()}")
    second_definition = second.manifest.instruments[0]
    split = json.loads(first_path.read_text(encoding="utf-8"))
    split["manifest_id"] = f"identity-split-{uuid4()}"
    split["instruments"][0]["provider_symbol"] = second_definition.provider_symbol
    with pytest.raises(InstrumentIdentityConflictError):
        service.bootstrap(
            load_catalog_manifest(_write_payload(tmp_path / "split.json", split)),
            request_id=f"identity-split-{uuid4()}",
        )
    assert _instrument_total(db_engine) == before_instruments + 1
    assert _canonical_counts(
        db_engine, first_definition.isin, first_definition.provider_symbol
    ) == (1, 1, 1, 1)
    assert _canonical_counts(
        db_engine, second_definition.isin, second_definition.provider_symbol
    ) == (1, 1, 1, 1)


def test_missing_isin_is_rejected_before_audit_or_canonical_write(
    db_engine: Engine, tmp_path: Path
) -> None:
    """Case G: equity-like PR-06 instruments require an ISIN in the manifest."""

    path = _write_manifest(tmp_path / "missing-isin-base.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    symbol = payload["instruments"][0]["provider_symbol"]
    before_instruments = _instrument_total(db_engine)
    before_identifiers = _identifier_count(db_engine, symbol)
    del payload["instruments"][0]["isin"]
    with pytest.raises(CatalogManifestError):
        load_catalog_manifest(_write_payload(tmp_path / "missing-isin.json", payload))
    assert _instrument_total(db_engine) == before_instruments
    assert _identifier_count(db_engine, symbol) == before_identifiers


def test_unicode_equivalent_symbol_and_leading_zero_symbol_are_textual_identities(
    db_engine: Engine, tmp_path: Path
) -> None:
    """Cases H-I: manifest normalization preserves one canonical text identifier."""

    unicode_token = uuid4().hex[:8].upper()
    unicode_path = _write_manifest(tmp_path / "unicode.json", provider_symbol=f"يك{unicode_token}")
    unicode_document = load_catalog_manifest(unicode_path)
    service = _service(db_engine)
    service.bootstrap(unicode_document, request_id=f"identity-unicode-{uuid4()}")
    normalized_symbol = unicode_document.manifest.instruments[0].provider_symbol
    assert normalized_symbol == f"یک{unicode_token}"

    unicode_payload = json.loads(unicode_path.read_text(encoding="utf-8"))
    unicode_payload["manifest_id"] = f"identity-unicode-rerun-{uuid4()}"
    unicode_payload["instruments"][0]["provider_symbol"] = f"یک{unicode_token}"
    service.bootstrap(
        load_catalog_manifest(_write_payload(tmp_path / "unicode-rerun.json", unicode_payload)),
        request_id=f"identity-unicode-rerun-{uuid4()}",
    )
    assert _identifier_count(db_engine, normalized_symbol) == 1

    leading_zero = f"000{uuid4().hex[:9].upper()}"
    zero_path = _write_manifest(tmp_path / "leading-zero.json", provider_symbol=leading_zero)
    zero_document = load_catalog_manifest(zero_path)
    service.bootstrap(zero_document, request_id=f"identity-leading-zero-{uuid4()}")
    with db_engine.connect() as connection:
        stored = connection.execute(
            text(
                "SELECT identifier_value FROM catalog.instrument_identifier "
                "WHERE identifier_type = 'BRSAPI_L18' AND identifier_value = :symbol"
            ),
            {"symbol": leading_zero},
        ).scalar_one()
    assert stored == leading_zero


def test_rename_to_symbol_owned_by_another_instrument_is_rejected(
    db_engine: Engine, tmp_path: Path
) -> None:
    """Case 15: an explicit rename never repoints a symbol owned by instrument B."""

    first_path = _write_manifest(tmp_path / "rename-a.json")
    second_path = _write_manifest(tmp_path / "rename-b.json")
    first = load_catalog_manifest(first_path)
    second = load_catalog_manifest(second_path)
    service = _service(db_engine)
    service.bootstrap(first, request_id=f"identity-rename-a-{uuid4()}")
    service.bootstrap(second, request_id=f"identity-rename-b-{uuid4()}")
    first_definition = first.manifest.instruments[0]
    second_definition = second.manifest.instruments[0]

    payload = json.loads(first_path.read_text(encoding="utf-8"))
    payload["manifest_id"] = f"identity-owned-symbol-{uuid4()}"
    payload["instruments"][0].update(
        {
            "previous_symbol": first_definition.provider_symbol,
            "provider_symbol": second_definition.provider_symbol,
            "rename_effective_from": _T1,
        }
    )
    with pytest.raises(InstrumentIdentityConflictError):
        service.bootstrap(
            load_catalog_manifest(_write_payload(tmp_path / "owned-symbol.json", payload)),
            request_id=f"identity-owned-symbol-{uuid4()}",
        )
    assert _canonical_counts(
        db_engine, first_definition.isin, first_definition.provider_symbol
    ) == (1, 1, 1, 1)
    assert _canonical_counts(
        db_engine, second_definition.isin, second_definition.provider_symbol
    ) == (1, 1, 1, 1)


def _service(engine: Engine) -> CatalogBootstrapService:
    return CatalogBootstrapService(
        unit_of_work_factory=create_unit_of_work_factory(engine).create_temporal_write
    )


def _write_manifest(path: Path, *, provider_symbol: str | None = None) -> Path:
    payload = json.loads(_BASE.read_text(encoding="utf-8"))
    token = uuid4().hex.upper()
    payload["manifest_id"] = f"identity-{token}"
    payload["instruments"][0].update(
        {
            "stable_key": f"identity-{token}",
            "provider_symbol": provider_symbol or f"SYM{token[:10]}",
            "isin": f"IR{token[:9]}1",
            "name_fa": f"Identity {token[:8]}",
            "name_en": f"Identity {token[:8]}",
        }
    )
    return _write_payload(path, payload)


def _write_payload(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _canonical_counts(engine: Engine, isin: str, symbol: str) -> tuple[int, int, int, int]:
    with engine.connect() as connection:
        instrument_id = connection.execute(
            text(
                "SELECT instrument_id FROM catalog.instrument_identifier "
                "WHERE identifier_type = 'ISIN' AND identifier_value = :isin"
            ),
            {"isin": isin},
        ).scalar_one_or_none()
        if instrument_id is None:
            return (0, 0, _identifier_count(engine, symbol), 0)
        return (
            1,
            connection.execute(
                text(
                    "SELECT count(*) FROM catalog.instrument_identifier "
                    "WHERE instrument_id = :id AND identifier_type = 'ISIN' AND valid_to IS NULL"
                ),
                {"id": instrument_id},
            ).scalar_one(),
            connection.execute(
                text(
                    "SELECT count(*) FROM catalog.instrument_identifier "
                    "WHERE instrument_id = :id AND identifier_type = 'BRSAPI_L18' "
                    "AND valid_to IS NULL"
                ),
                {"id": instrument_id},
            ).scalar_one(),
            connection.execute(
                text(
                    "SELECT count(*) FROM catalog.instrument_spec_version WHERE instrument_id = :id"
                ),
                {"id": instrument_id},
            ).scalar_one(),
        )


def _provider_and_instrument(engine: Engine, provider_code: str, isin: str) -> tuple[int, int]:
    with engine.connect() as connection:
        return (
            connection.execute(
                text("SELECT provider_id FROM catalog.data_provider WHERE provider_code = :code"),
                {"code": provider_code},
            ).scalar_one(),
            connection.execute(
                text(
                    "SELECT instrument_id FROM catalog.instrument_identifier "
                    "WHERE identifier_type = 'ISIN' AND identifier_value = :isin"
                ),
                {"isin": isin},
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


def _instrument_total(engine: Engine) -> int:
    with engine.connect() as connection:
        return cast(
            int,
            connection.execute(text("SELECT count(*) FROM catalog.instrument")).scalar_one(),
        )


def _new_isin() -> str:
    return f"IR{uuid4().hex[:9].upper()}1"
