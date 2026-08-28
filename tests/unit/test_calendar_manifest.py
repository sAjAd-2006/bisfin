"""Unit contracts for strict explicit trading-calendar manifests."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

import pytest

from bisfin.calendar import (
    CalendarManifestError,
    CalendarManifestErrorCode,
    calendar_source_record_key,
    load_calendar_manifest,
    validate_calendar_manifest,
)

pytestmark = pytest.mark.unit

_FIXTURES = Path("tests/fixtures/calendar")
_REQUIRED = {
    "tse_regular_conflicting_existing.json",
    "tse_regular_duplicate_date.json",
    "tse_regular_invalid_open_close.json",
    "tse_regular_missing_date.json",
    "tse_regular_success.json",
    "tse_regular_unicode_digits.json",
    "tse_regular_with_holidays.json",
}


def test_calendar_fixture_set_is_complete_utf8_and_secret_free() -> None:
    paths = {path.name: path for path in _FIXTURES.iterdir() if path.is_file()}

    assert set(paths) == _REQUIRED
    for path in paths.values():
        content = path.read_bytes().decode("utf-8")
        json.loads(content)
        assert "api_key" not in content.lower()
        assert '"key"' not in content.lower()


def test_success_calendar_is_complete_hashed_and_converted_with_iana_timezone() -> None:
    path = _FIXTURES / "tse_regular_success.json"
    document = load_calendar_manifest(path)
    validated = validate_calendar_manifest(document)

    assert document.payload_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert document.manifest.timezone == "Asia/Tehran"
    assert len(validated.sessions) == 12
    first = validated.sessions[0]
    assert first.trading_date == date(2025, 2, 18)
    assert first.session_open_ts == datetime(2025, 2, 18, 5, 30, tzinfo=UTC)
    assert first.session_close_ts == datetime(2025, 2, 18, 9, 0, tzinfo=UTC)
    closed = next(item for item in validated.sessions if not item.is_trading_day)
    assert closed.session_open_ts is None
    assert closed.session_close_ts is None
    assert calendar_source_record_key(document.manifest, first) == (
        "bisfin|calendar|tse-regular-pr06-fixture-v1|TSE|REGULAR|2025-02-18"
    )


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("tse_regular_missing_date.json", "complete"),
        ("tse_regular_duplicate_date.json", "duplicate"),
        ("tse_regular_invalid_open_close.json", "after"),
    ],
)
def test_calendar_fixture_validation_fails_closed(name: str, message: str) -> None:
    with pytest.raises(CalendarManifestError, match=message):
        validate_calendar_manifest(load_calendar_manifest(_FIXTURES / name))


def test_holidays_are_explicit_not_inferred() -> None:
    validated = validate_calendar_manifest(
        load_calendar_manifest(_FIXTURES / "tse_regular_with_holidays.json")
    )

    assert [item.is_trading_day for item in validated.sessions] == [True, False, False, False]


def test_unicode_digits_are_normalized_without_machine_timezone_dependency() -> None:
    validated = validate_calendar_manifest(
        load_calendar_manifest(_FIXTURES / "tse_regular_unicode_digits.json")
    )

    assert validated.sessions[0].trading_date == date(2025, 2, 18)
    assert validated.sessions[0].open_local_time == time(9, 0)


def test_ambiguous_and_nonexistent_local_times_require_safe_resolution(tmp_path: Path) -> None:
    base: dict[str, Any] = {
        "schema_version": 1,
        "calendar_id": "dst-test",
        "venue_code": "TEST",
        "timezone": "America/New_York",
        "date_from": "2026-11-01",
        "date_to": "2026-11-01",
        "sessions": [
            {
                "trading_date": "2026-11-01",
                "session_code": "REGULAR",
                "is_trading_day": True,
                "open_local_time": "01:30:00",
                "close_local_time": "03:00:00",
                "source_status": "TEST",
            }
        ],
    }
    ambiguous = tmp_path / "ambiguous.json"
    ambiguous.write_text(json.dumps(base), encoding="utf-8")
    with pytest.raises(CalendarManifestError, match="ambiguous"):
        validate_calendar_manifest(load_calendar_manifest(ambiguous))

    base["sessions"][0]["open_fold"] = 1
    resolved = tmp_path / "resolved.json"
    resolved.write_text(json.dumps(base), encoding="utf-8")
    assert (
        validate_calendar_manifest(load_calendar_manifest(resolved)).sessions[0].session_open_ts
        is not None
    )

    base["date_from"] = "2026-03-08"
    base["date_to"] = "2026-03-08"
    base["sessions"][0]["trading_date"] = "2026-03-08"
    base["sessions"][0]["open_local_time"] = "02:30:00"
    base["sessions"][0].pop("open_fold")
    nonexistent = tmp_path / "nonexistent.json"
    nonexistent.write_text(json.dumps(base), encoding="utf-8")
    with pytest.raises(CalendarManifestError, match="nonexistent"):
        validate_calendar_manifest(load_calendar_manifest(nonexistent))


def test_unknown_fields_and_unknown_versions_have_machine_readable_failure_codes(
    tmp_path: Path,
) -> None:
    payload = json.loads((_FIXTURES / "tse_regular_success.json").read_text(encoding="utf-8"))
    payload["schema_version"] = 2
    path = tmp_path / "unsupported.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CalendarManifestError) as version:
        load_calendar_manifest(path)
    assert version.value.code is CalendarManifestErrorCode.UNSUPPORTED_SCHEMA_VERSION

    payload["schema_version"] = 1
    payload["unexpected"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CalendarManifestError) as invalid:
        load_calendar_manifest(path)
    assert invalid.value.code is CalendarManifestErrorCode.INVALID_MANIFEST
