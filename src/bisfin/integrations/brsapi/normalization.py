"""Deterministic Unicode, Jalali-date, and source-time normalization."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, time

import jdatetime  # type: ignore[import-untyped]  # Upstream 6.x ships no type marker.

_DIGIT_TRANSLATION = str.maketrans(
    {
        "۰": "0",
        "۱": "1",
        "۲": "2",
        "۳": "3",
        "۴": "4",
        "۵": "5",
        "۶": "6",
        "۷": "7",
        "۸": "8",
        "۹": "9",
        "٠": "0",
        "١": "1",
        "٢": "2",
        "٣": "3",
        "٤": "4",
        "٥": "5",
        "٦": "6",
        "٧": "7",
        "٨": "8",
        "٩": "9",
    }
)
_PERSIAN_LETTER_TRANSLATION = str.maketrans(
    {
        "ي": "ی",  # Arabic Yeh -> Persian Yeh
        "ى": "ی",  # Alef Maksura in provider identifiers -> Persian Yeh
        "ك": "ک",  # Arabic Kaf -> Persian Kaf
    }
)
_WHITESPACE = re.compile(r"\s+")
_JALALI_DATE = re.compile(r"^(\d{4})([-/])(\d{2})\2(\d{2})$")
_SOURCE_TIME = re.compile(r"^(\d{2}):(\d{2})(?::(\d{2}))?$")


def normalize_digits(value: str) -> str:
    """Normalize Persian and Arabic-Indic digits without numeric coercion."""

    return value.translate(_DIGIT_TRANSLATION)


def normalize_brsapi_symbol(value: str) -> str:
    """Normalize an identifier conservatively while preserving its string form.

    NFKC is followed by Arabic/Persian letter normalization, digit-glyph
    normalization, trimming, and collapsing Unicode whitespace.  The function
    never transliterates, parses numbers, or strips leading zeroes.
    """

    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.translate(_PERSIAN_LETTER_TRANSLATION)
    normalized = normalize_digits(normalized)
    return _WHITESPACE.sub(" ", normalized).strip()


def parse_jalali_date(value: str) -> date:
    """Convert a documented BrsApi Jalali date using maintained ``jdatetime``."""

    normalized = normalize_digits(unicodedata.normalize("NFKC", value)).strip()
    match = _JALALI_DATE.fullmatch(normalized)
    if match is None:
        raise ValueError("Jalali date must use YYYY-MM-DD or YYYY/MM/DD")

    year, month, day = (int(match.group(index)) for index in (1, 3, 4))
    try:
        converted = jdatetime.date(year, month, day).togregorian()
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("Jalali date is not valid") from error
    if not isinstance(converted, date):
        raise TypeError("jdatetime returned an unexpected Gregorian date type")
    return converted


def parse_source_time(value: str) -> time:
    """Parse optional provider metadata in strict HH:MM or HH:MM:SS form."""

    normalized = normalize_digits(unicodedata.normalize("NFKC", value)).strip()
    match = _SOURCE_TIME.fullmatch(normalized)
    if match is None:
        raise ValueError("source time must use HH:MM or HH:MM:SS")
    hour = int(match.group(1))
    minute = int(match.group(2))
    second = int(match.group(3) or "0")
    try:
        return time(hour=hour, minute=minute, second=second)
    except ValueError as error:
        raise ValueError("source time is not valid") from error


__all__ = [
    "normalize_brsapi_symbol",
    "normalize_digits",
    "parse_jalali_date",
    "parse_source_time",
]
