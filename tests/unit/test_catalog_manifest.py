"""Unit tests for the strict, versioned catalog-manifest boundary."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest

from bisfin.catalog import (
    CatalogManifestError,
    CatalogManifestErrorCode,
    CatalogManifestV1,
    catalog_manifest_json_schema,
    load_catalog_manifest,
    parse_catalog_manifest_bytes,
)

pytestmark = pytest.mark.unit

_FIXTURES = Path("tests/fixtures/catalog")


def test_success_manifest_is_strict_typed_ordered_and_hashed_from_exact_bytes() -> None:
    path = _FIXTURES / "catalog_bootstrap_success.json"
    payload = path.read_bytes()

    document = load_catalog_manifest(path)

    assert document.payload_bytes == payload
    assert document.payload_sha256 == hashlib.sha256(payload).hexdigest()
    assert document.manifest.schema_version == 1
    assert document.manifest.generated_at == datetime(2026, 7, 31, tzinfo=UTC)
    assert [provider.provider_code for provider in document.manifest.providers] == [
        "BISFIN",
        "BRSAPI",
    ]
    assert [feed.feed_code for feed in document.manifest.feeds] == [
        "BISFIN_CATALOG_MANIFEST",
        "BISFIN_TRADING_CALENDAR",
        "TSETMC_CANDLE_DAILY_RAW",
        "TSETMC_SYMBOL_METADATA",
    ]
    instrument = document.manifest.instruments[0]
    assert instrument.provider_symbol == "فملی"
    assert instrument.isin == "IRO1MSMI0001"
    assert instrument.contract_multiplier == Decimal("1")
    assert document.manifest.resolve_provider_market("BRSAPI", " بورس ") == "TSE"


def test_manifest_schema_is_machine_readable_and_fail_closed() -> None:
    schema = catalog_manifest_json_schema()

    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"]["const"] == 1
    assert "instruments" in schema["required"]


@pytest.mark.parametrize("version", [0, 2, "1", None])
def test_unknown_or_non_integer_schema_versions_fail_closed(version: object) -> None:
    payload = _valid_payload()
    payload["schema_version"] = version

    with pytest.raises(CatalogManifestError) as raised:
        parse_catalog_manifest_bytes(_json_bytes(payload))

    assert raised.value.code is CatalogManifestErrorCode.UNSUPPORTED_SCHEMA_VERSION


def test_unknown_fields_and_naive_timestamps_are_rejected() -> None:
    unknown = _valid_payload()
    unknown["unexpected"] = "must fail"

    with pytest.raises(CatalogManifestError) as raised_unknown:
        parse_catalog_manifest_bytes(_json_bytes(unknown))

    assert raised_unknown.value.code is CatalogManifestErrorCode.INVALID_MANIFEST
    assert any(item.path == "unexpected" for item in raised_unknown.value.diagnostics)

    naive = _valid_payload()
    naive["generated_at"] = "2026-07-31T00:00:00"

    with pytest.raises(CatalogManifestError, match="timezone-aware"):
        parse_catalog_manifest_bytes(_json_bytes(naive))


def test_decimal_fields_accept_strings_only_and_reject_non_finite_values() -> None:
    numeric = _valid_payload()
    numeric["instruments"][0]["price_tick"] = 10

    with pytest.raises(CatalogManifestError, match="Decimal string"):
        parse_catalog_manifest_bytes(_json_bytes(numeric))

    non_finite = _valid_payload()
    non_finite["instruments"][0]["price_tick"] = "NaN"

    with pytest.raises(CatalogManifestError, match="finite"):
        parse_catalog_manifest_bytes(_json_bytes(non_finite))


def test_duplicate_json_object_fields_are_rejected_instead_of_last_value_wins() -> None:
    payload = b'{"schema_version":1,"schema_version":2}'

    with pytest.raises(CatalogManifestError) as raised:
        parse_catalog_manifest_bytes(payload)

    assert raised.value.code is CatalogManifestErrorCode.DUPLICATE_JSON_FIELD


def test_invalid_utf8_and_malformed_json_have_stable_error_codes() -> None:
    with pytest.raises(CatalogManifestError) as invalid_utf8:
        parse_catalog_manifest_bytes(b"\xff")
    assert invalid_utf8.value.code is CatalogManifestErrorCode.INVALID_UTF8

    with pytest.raises(CatalogManifestError) as malformed:
        parse_catalog_manifest_bytes(b"{")
    assert malformed.value.code is CatalogManifestErrorCode.MALFORMED_JSON


def test_model_cannot_be_used_as_a_permissive_persistence_record() -> None:
    manifest = load_catalog_manifest(_FIXTURES / "catalog_bootstrap_success.json").manifest

    with pytest.raises(Exception, match="frozen"):
        manifest.manifest_id = "mutated"

    with pytest.raises(Exception):
        CatalogManifestV1.model_validate({**manifest.model_dump(), "database_id": 1})


def _valid_payload() -> dict[str, Any]:
    return cast(
        "dict[str, Any]",
        json.loads((_FIXTURES / "catalog_bootstrap_success.json").read_bytes()),
    )


def _json_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
