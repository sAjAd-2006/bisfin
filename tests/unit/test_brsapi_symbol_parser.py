"""Deterministic parsing tests for catalog-relevant BrsApi Symbol metadata."""

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bisfin.integrations.brsapi.contracts import (
    BrsApiContractError,
    BrsApiMalformedResponseError,
    BrsApiProviderError,
    BrsApiRawResponse,
)
from bisfin.integrations.brsapi.symbol import parse_symbol_metadata

pytestmark = pytest.mark.unit

_FIXTURES = Path("tests/fixtures/brsapi")


def _response(body: bytes) -> BrsApiRawResponse:
    now = datetime(2026, 8, 28, tzinfo=UTC)
    return BrsApiRawResponse(200, (), body, now, now, timedelta(0))


def _fixture(name: str) -> BrsApiRawResponse:
    return _response((_FIXTURES / name).read_bytes())


def test_symbol_success_parses_only_catalog_fields_but_preserves_complete_raw_json() -> None:
    raw = _fixture("symbol_success.json")

    parsed = parse_symbol_metadata(raw)

    assert parsed.original_symbol == "فملی"
    assert parsed.normalized_symbol == "فملی"
    assert parsed.isin == "IRO1MSMI0001"
    assert parsed.name_fa == "ملی صنایع مس ایران"
    assert parsed.name_en == "National Iranian Copper Industries Co."
    assert parsed.market == "بورس"
    assert parsed.market_board == "بازار اول"
    assert parsed.industry == "فلزات اساسی"
    assert parsed.date_update == "1404-05-30"
    assert parsed.source_time == "12:30:00"
    assert parsed.state == "مجاز"
    assert parsed.response_sha256 == hashlib.sha256(raw.body_bytes).hexdigest()
    assert parsed.raw_payload["pc"] == 12345
    assert str(parsed.raw_payload["pe"]) == "7.25"
    assert parsed.raw_payload["assembly"] == [{"title": "اطلاعیه آزمایشی", "published": False}]
    assert parsed.raw_payload["depth"] == {"bid": {"price": 12340, "count": 2}}


def test_symbol_normalizes_isin_and_symbol_without_altering_names() -> None:
    parsed = parse_symbol_metadata(
        _response(
            """{
              "l18":"  فملي  ۰۰۷ ",
              "isin":"  ir01abcd0007  ",
              "m":" بورس ",
              "l30":"  نام فارسی  ",
              "l30_en":"  English Name  "
            }""".encode()
        )
    )

    assert parsed.original_symbol == "  فملي  ۰۰۷ "
    assert parsed.normalized_symbol == "فملی 007"
    assert parsed.isin == "IR01ABCD0007"
    assert parsed.market == "بورس"
    assert parsed.name_fa == "  نام فارسی  "
    assert parsed.name_en == "  English Name  "


@pytest.mark.parametrize(
    "fixture, expected_symbol, expected_isin, expected_market",
    [
        ("symbol_name_changed.json", "ملی مس", "IRO1MSMI0001", "بورس"),
        ("symbol_isin_mismatch.json", "فملی", "IRO1MSMI9999", "بورس"),
        ("symbol_market_mismatch.json", "فملی", "IRO1MSMI0001", "فرابورس"),
    ],
)
def test_symbol_scenario_fixtures_remain_structurally_parseable(
    fixture: str,
    expected_symbol: str,
    expected_isin: str,
    expected_market: str,
) -> None:
    parsed = parse_symbol_metadata(_fixture(fixture))

    assert parsed.normalized_symbol == expected_symbol
    assert parsed.isin == expected_isin
    assert parsed.market == expected_market


def test_symbol_provider_error_is_typed_and_secret_safe() -> None:
    secret = "provider-error-secret"
    body = (
        '{"code_http":401,"successful":false,"status":"error",'
        f'"message_error":"bad https://Api.BrsApi.ir/Tsetmc/Symbol.php?key={secret}&l18=x"'
        "}"
    ).encode()

    with pytest.raises(BrsApiProviderError) as captured:
        parse_symbol_metadata(_response(body))

    error = captured.value
    assert error.raw_payload["code_http"] == 401
    assert secret not in f"{error!r} {error} {error.message_error}"
    assert "key=***" in str(error)


def test_committed_symbol_provider_error_fixture_is_rejected() -> None:
    with pytest.raises(BrsApiProviderError):
        parse_symbol_metadata(_fixture("symbol_provider_error.json"))


@pytest.mark.parametrize(
    "body",
    [
        b"[]",
        b"[{}]",
        b'"object required"',
        b"null",
        b'{"l18":"x","isin":"IR01ABCD0007","m":"x","unexpected":NaN}',
        b'{"l18":"x","isin":"IR01ABCD0007","m":"x","unexpected":Infinity}',
        b'{"l18":"x","l18":"y","isin":"IR01ABCD0007","m":"x"}',
        b'{"l18":"x","isin":"IR01ABCD0007","m":"x","nested":{"a":1,"a":2}}',
    ],
)
def test_symbol_parser_rejects_ambiguous_envelopes_duplicate_keys_and_nonfinite_numbers(
    body: bytes,
) -> None:
    with pytest.raises((BrsApiContractError, BrsApiMalformedResponseError)):
        parse_symbol_metadata(_response(body))


@pytest.mark.parametrize(
    "body",
    [
        b'{"isin":"IR01ABCD0007","m":"x"}',
        b'{"l18":"x","m":"x"}',
        b'{"l18":"x","isin":"IR01ABCD0007"}',
        b'{"l18":true,"isin":"IR01ABCD0007","m":"x"}',
        b'{"l18":"x","isin":true,"m":"x"}',
        b'{"l18":"x","isin":"IR01ABCD0007","m":false}',
        b'{"l18":"x","isin":"IR01ABCD0007","m":"x","l30":true}',
        b'{"l18":" ","isin":"IR01ABCD0007","m":"x"}',
        b'{"l18":"x","isin":" ","m":"x"}',
        b'{"l18":"x","isin":"NOT-AN-ISIN","m":"x"}',
        b'{"l18":"x","isin":"IR01ABCD0007","m":" "}',
    ],
)
def test_symbol_parser_requires_valid_nonempty_string_identity_fields(body: bytes) -> None:
    with pytest.raises(BrsApiContractError):
        parse_symbol_metadata(_response(body))


def test_committed_malformed_symbol_fixture_fails_closed() -> None:
    with pytest.raises(BrsApiMalformedResponseError):
        parse_symbol_metadata(_fixture("symbol_malformed_json.txt"))
