"""Unit tests for conservative BrsApi source normalization."""

from datetime import date, time

import pytest

from bisfin.integrations.brsapi.normalization import (
    normalize_brsapi_symbol,
    normalize_digits,
    parse_jalali_date,
    parse_source_time,
)

pytestmark = pytest.mark.unit


def test_symbol_normalizes_arabic_yeh_kaf_nfkc_and_whitespace() -> None:
    assert normalize_brsapi_symbol("  كيميا\u00a0\t  ۰۰۷  ") == "کیمیا 007"


def test_symbol_normalization_preserves_meaningful_characters_and_leading_zeroes() -> None:
    value = "ضهرم-۰۰۰۳"

    normalized = normalize_brsapi_symbol(value)

    assert normalized == "ضهرم-0003"
    assert isinstance(normalized, str)


def test_digit_normalization_supports_both_provider_digit_sets() -> None:
    assert normalize_digits("۱۴۰۳/١٢/٠١") == "1403/12/01"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("1403-01-01", date(2024, 3, 20)),
        ("1403/01/01", date(2024, 3, 20)),
        ("۱۴۰۳/۰۱/۰۱", date(2024, 3, 20)),
        ("1403/06/31", date(2024, 9, 21)),
        ("1403/07/01", date(2024, 9, 22)),
        ("1399/12/30", date(2021, 3, 20)),
    ],
)
def test_jalali_conversion_is_deterministic(source: str, expected: date) -> None:
    assert parse_jalali_date(source) == expected


@pytest.mark.parametrize(
    "source",
    [
        "1400/12/30",
        "1403/13/01",
        "1403/00/01",
        "1403/01/32",
        "1403.01.01",
        "1403/1/01",
        "2024-03-20T00:00:00",
        "",
    ],
)
def test_invalid_jalali_dates_are_rejected(source: str) -> None:
    with pytest.raises(ValueError):
        parse_jalali_date(source)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("12:28", time(12, 28)),
        ("12:28:59", time(12, 28, 59)),
        ("۱۲:۲۸:۵۹", time(12, 28, 59)),
    ],
)
def test_source_time_accepts_only_documented_precisions(source: str, expected: time) -> None:
    assert parse_source_time(source) == expected


@pytest.mark.parametrize("source", ["24:00", "12:60", "1:02", "12:02:03.5", "", "noon"])
def test_invalid_source_times_are_rejected(source: str) -> None:
    with pytest.raises(ValueError):
        parse_source_time(source)
