"""Cross-entry validation and normalization tests for catalog manifests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bisfin.catalog import CatalogManifestError, load_catalog_manifest, normalize_isin

pytestmark = pytest.mark.unit

_FIXTURES = Path("tests/fixtures/catalog")
_REQUIRED_FIXTURES = {
    "catalog_bootstrap_conflicting_isin.json",
    "catalog_bootstrap_duplicate_key.json",
    "catalog_bootstrap_invalid_reference.json",
    "catalog_bootstrap_missing_isin.json",
    "catalog_bootstrap_rerun.json",
    "catalog_bootstrap_success.json",
    "catalog_bootstrap_symbol_rename.json",
    "catalog_bootstrap_unicode_symbols.json",
}


def test_required_fixture_set_is_complete_utf8_json_and_secret_free() -> None:
    paths = {path.name: path for path in _FIXTURES.iterdir() if path.is_file()}

    assert set(paths) == _REQUIRED_FIXTURES
    for path in paths.values():
        text = path.read_bytes().decode("utf-8")
        json.loads(text)
        lowered = text.lower()
        assert "api_key" not in lowered
        assert '"key"' not in lowered
        assert "authorization" not in lowered


@pytest.mark.parametrize(
    ("fixture_name", "message"),
    [
        ("catalog_bootstrap_duplicate_key.json", "duplicate instrument stable_key"),
        ("catalog_bootstrap_missing_isin.json", "isin"),
        ("catalog_bootstrap_invalid_reference.json", "unknown venue_code"),
    ],
)
def test_invalid_fixture_fails_completely_before_any_database_boundary(
    fixture_name: str,
    message: str,
) -> None:
    with pytest.raises(CatalogManifestError, match=message):
        load_catalog_manifest(_FIXTURES / fixture_name)


def test_conflicting_isin_fixture_is_structurally_valid_for_database_conflict_testing() -> None:
    manifest = load_catalog_manifest(_FIXTURES / "catalog_bootstrap_conflicting_isin.json").manifest

    assert manifest.instruments[0].provider_symbol == "فملی"
    assert manifest.instruments[0].isin == "IRO1MSMI9999"


def test_rename_fixture_has_an_explicit_adjacent_interval_boundary() -> None:
    instrument = load_catalog_manifest(
        _FIXTURES / "catalog_bootstrap_symbol_rename.json"
    ).manifest.instruments[0]

    assert instrument.previous_symbol == "فملی"
    assert instrument.provider_symbol == "فملی‌نو"
    assert instrument.rename_effective_from is not None
    assert instrument.rename_effective_from > instrument.identifier_valid_from


def test_unicode_symbol_normalization_preserves_persian_text_and_leading_zeroes() -> None:
    instrument = load_catalog_manifest(
        _FIXTURES / "catalog_bootstrap_unicode_symbols.json"
    ).manifest.instruments[0]

    assert instrument.provider_symbol == "کیمیا 007"
    assert instrument.name_fa == "شرکت کیمیا"
    assert instrument.isin == "IRO1KIMI0007"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (" iro1msmi0001 ", "IRO1MSMI0001"),
        ("IRO1KIMI0007", "IRO1KIMI0007"),
    ],
)
def test_isin_normalization_is_trimmed_uppercase_and_preserves_zeroes(
    source: str,
    expected: str,
) -> None:
    assert normalize_isin(source) == expected


@pytest.mark.parametrize(
    "source",
    ["", "   ", "IR", "IRO1MSMI000!", "۱۲۳۴۵۶۷۸۹۰۱۲", True],
)
def test_isin_format_check_is_conservative_and_does_not_coerce(source: object) -> None:
    with pytest.raises(ValueError, match="ISIN"):
        normalize_isin(source)


def test_duplicate_provider_market_mapping_is_rejected_after_unicode_normalization() -> None:
    payload = json.loads((_FIXTURES / "catalog_bootstrap_success.json").read_bytes())
    payload["provider_market_mappings"].append(
        {"provider_code": "BRSAPI", "provider_market": "  بورس  ", "venue_code": "TSE"}
    )
    temporary = json.dumps(payload, ensure_ascii=False).encode()

    from bisfin.catalog import parse_catalog_manifest_bytes

    with pytest.raises(CatalogManifestError, match="duplicate provider-market mapping"):
        parse_catalog_manifest_bytes(temporary)
