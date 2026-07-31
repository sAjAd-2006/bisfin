"""Unit tests for fail-closed envelope and row parsing."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from bisfin.integrations.brsapi.contracts import (
    BrsApiContractError,
    BrsApiMalformedResponseError,
    BrsApiNoDataResponse,
    BrsApiProviderError,
    BrsApiRawResponse,
    RowValidationCode,
)
from bisfin.integrations.brsapi.parser import (
    parse_candlestick_envelope,
    parse_unadjusted_daily_candles,
    response_payload_sha256,
)

pytestmark = pytest.mark.unit

_FIXTURES = Path("tests/fixtures/brsapi")
_NOW = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)


def _raw(body: bytes) -> BrsApiRawResponse:
    return BrsApiRawResponse(
        status_code=200,
        headers=(("content-type", "application/json"),),
        body_bytes=body,
        request_started_at=_NOW,
        response_received_at=_NOW + timedelta(milliseconds=25),
        elapsed=timedelta(milliseconds=25),
    )


def _fixture(name: str) -> BrsApiRawResponse:
    return _raw((_FIXTURES / name).read_bytes())


def _codes(result_index: int, result: object) -> set[RowValidationCode]:
    del result_index
    errors = getattr(result, "errors")
    return {issue.code for issue in errors}


def test_success_fixture_uses_explicit_top_level_array_contract() -> None:
    parsed = parse_unadjusted_daily_candles(
        _fixture("candlestick_type2_success.json"),
        requested_symbol="فملی",
    )

    assert parsed.no_data is None
    assert len(parsed.rows) == 3
    assert parsed.accepted_count == 3
    assert parsed.rejected_count == 0
    assert len(parsed.canonical_candidates) == 3
    assert parsed.response_sha256 == response_payload_sha256(
        (_FIXTURES / "candlestick_type2_success.json").read_bytes()
    )


def test_success_fixture_preserves_unknown_fields_raw_values_and_original_symbol() -> None:
    parsed = parse_unadjusted_daily_candles(
        _fixture("candlestick_type2_success.json"),
        requested_symbol="فملی",
    )
    first, second, third = parsed.canonical_candidates

    assert first.raw_payload["provider_note"] == "فیلد ناشناخته باید حفظ شود"
    assert second.raw_payload["open"] == "7100.00"
    assert second.raw_payload["extra"] == {
        "fixture": True,
        "source": "پایه تعیینی",
    }
    assert second.original_symbol == "فملي"
    assert second.normalized_symbol == "فملی"
    assert third.original_symbol == "  فملی  "


def test_success_fixture_parses_decimal_strings_jalali_digits_and_optional_time() -> None:
    parsed = parse_unadjusted_daily_candles(
        _fixture("candlestick_type2_success.json"),
        requested_symbol="فملی",
    )
    _, second, third = parsed.canonical_candidates

    assert second.open == Decimal("7100.00")
    assert second.close == Decimal("7250.0")
    assert second.trading_date == date(2025, 2, 19)
    assert second.source_time is not None
    assert second.source_time.isoformat() == "12:30:00"
    assert third.source_time is None
    assert third.source_time_text is None


def test_response_type_is_informational_and_does_not_reject_type2_acquisition() -> None:
    parsed = parse_unadjusted_daily_candles(
        _fixture("candlestick_type2_success.json"),
        requested_symbol="فملی",
    )

    assert parsed.accepted_count == 3
    assert all(
        RowValidationCode.RESPONSE_TYPE_IGNORED in {warning.code for warning in result.warnings}
        for result in parsed.rows
    )


def test_no_data_is_a_valid_zero_row_result() -> None:
    raw = _fixture("candlestick_type2_no_data.json")

    envelope = parse_candlestick_envelope(raw)
    parsed = parse_unadjusted_daily_candles(raw, requested_symbol="فملی")

    assert isinstance(envelope, BrsApiNoDataResponse)
    assert envelope.raw_payload["status"] == "no_data"
    assert parsed.no_data == envelope
    assert parsed.rows == ()
    assert parsed.canonical_candidates == ()


def test_provider_error_fixture_maps_to_typed_secret_safe_error() -> None:
    with pytest.raises(BrsApiProviderError) as captured:
        parse_candlestick_envelope(_fixture("candlestick_type2_provider_error.json"))

    assert captured.value.code_http == 429
    assert captured.value.status == "error"
    assert "محدودیت" in str(captured.value)
    assert captured.value.raw_payload["diagnostic_code"] == "FIXTURE_RATE_LIMIT"


def test_provider_error_redacts_keyed_url_from_exception_message() -> None:
    secret = "secret-must-not-escape"
    body = (
        '{"successful":false,"code_http":401,"status":"error",'
        '"message_error":"https://Api.BrsApi.ir/x?key=' + secret + '&type=2"}'
    ).encode()

    with pytest.raises(BrsApiProviderError) as captured:
        parse_candlestick_envelope(_raw(body))

    assert secret not in str(captured.value)
    assert captured.value.message_error is not None
    assert "key=***" in captured.value.message_error


def test_partial_fixture_keeps_valid_row_and_returns_stable_error_codes() -> None:
    parsed = parse_unadjusted_daily_candles(
        _fixture("candlestick_type2_partial_invalid.json"),
        requested_symbol="فملی",
    )

    assert parsed.accepted_count == 1
    assert parsed.rejected_count == 3
    assert len(parsed.canonical_candidates) == 1
    assert RowValidationCode.NEGATIVE_VOLUME in _codes(1, parsed.rows[1])
    assert {
        RowValidationCode.INVALID_SOURCE_TIME,
        RowValidationCode.INVALID_OHLC,
    }.issubset(_codes(2, parsed.rows[2]))
    assert {
        RowValidationCode.SYMBOL_MISMATCH,
        RowValidationCode.INVALID_JALALI_DATE,
        RowValidationCode.INVALID_NUMERIC_VALUE,
    }.issubset(_codes(3, parsed.rows[3]))
    assert parsed.rows[3].raw_payload["unknown_rejected_field"] == ("همچنان در raw حفظ می‌شود")


def test_duplicate_policy_preserves_identical_raw_rows_and_rejects_conflicts() -> None:
    parsed = parse_unadjusted_daily_candles(
        _fixture("candlestick_type2_duplicate_date.json"),
        requested_symbol="فملی",
    )

    assert len(parsed.rows) == 4
    assert parsed.accepted_count == 2
    assert parsed.rejected_count == 2
    assert len(parsed.canonical_candidates) == 1
    assert parsed.rows[0].include_in_canonicalization is True
    assert parsed.rows[1].include_in_canonicalization is False
    assert all(
        RowValidationCode.DUPLICATE_IDENTICAL in {warning.code for warning in result.warnings}
        for result in parsed.rows[:2]
    )
    assert all(
        RowValidationCode.DUPLICATE_CONFLICT in _codes(index, result)
        for index, result in enumerate(parsed.rows[2:], start=2)
    )


def test_corrected_fixture_changes_only_the_expected_financial_candidate() -> None:
    original = parse_unadjusted_daily_candles(
        _fixture("candlestick_type2_success.json"),
        requested_symbol="فملی",
    )
    corrected = parse_unadjusted_daily_candles(
        _fixture("candlestick_type2_corrected.json"),
        requested_symbol="فملی",
    )

    original_by_date = {
        candidate.trading_date: candidate for candidate in original.canonical_candidates
    }
    corrected_by_date = {
        candidate.trading_date: candidate for candidate in corrected.canonical_candidates
    }
    changed_date = date(2025, 2, 19)
    assert original_by_date[changed_date].close == Decimal("7250.0")
    assert corrected_by_date[changed_date].close == Decimal("7280.0")
    assert original_by_date[changed_date].row_payload_sha256 != (
        corrected_by_date[changed_date].row_payload_sha256
    )


@pytest.mark.parametrize(
    "body",
    [
        b"[]",
        b"42",
        b'[{"l18":"x"},42]',
        b'{"successful":true,"data":[]}',
        b'{"status":"no_data","successful":true,"code_http":201,"message_error":null}',
        b'{"status":"no_data","successful":true,"code_http":200,"message_error":"x"}',
    ],
)
def test_undocumented_or_invalid_envelopes_fail_closed(body: bytes) -> None:
    with pytest.raises(BrsApiContractError):
        parse_candlestick_envelope(_raw(body))


@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        b'{"value":NaN}',
        b"\xff\xfe",
    ],
)
def test_malformed_non_strict_json_is_rejected(body: bytes) -> None:
    with pytest.raises(BrsApiMalformedResponseError):
        parse_candlestick_envelope(_raw(body))


def test_committed_malformed_fixture_is_rejected() -> None:
    with pytest.raises(BrsApiMalformedResponseError):
        parse_candlestick_envelope(_fixture("candlestick_malformed_json.txt"))


def test_missing_fields_boolean_numbers_and_count_mismatch_are_diagnostic() -> None:
    body = (
        '[{"l18":"فملی","type":2,"count":9,"date":"1403/01/01",'
        '"open":true,"high":2,"low":1,"volume":5}]'
    ).encode()

    parsed = parse_unadjusted_daily_candles(_raw(body), requested_symbol="فملی")

    codes = _codes(0, parsed.rows[0])
    warning_codes = {warning.code for warning in parsed.rows[0].warnings}
    assert RowValidationCode.MISSING_FIELD in codes
    assert RowValidationCode.INVALID_NUMERIC_VALUE in codes
    assert RowValidationCode.COUNT_MISMATCH in warning_codes


def test_empty_requested_symbol_is_rejected_before_provider_rows() -> None:
    with pytest.raises(ValueError, match="requested_symbol"):
        parse_unadjusted_daily_candles(
            _fixture("candlestick_type2_success.json"),
            requested_symbol=" \t ",
        )
