"""Strict, complete, IANA-aware calendar manifest parsing and validation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

_DIGITS = str.maketrans(
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
_TIME = re.compile(r"^(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?$")


class CalendarManifestErrorCode(StrEnum):
    INVALID_UTF8 = "INVALID_UTF8"
    MALFORMED_JSON = "MALFORMED_JSON"
    DUPLICATE_JSON_FIELD = "DUPLICATE_JSON_FIELD"
    UNSUPPORTED_SCHEMA_VERSION = "UNSUPPORTED_SCHEMA_VERSION"
    INVALID_MANIFEST = "INVALID_MANIFEST"


class CalendarManifestError(ValueError):
    def __init__(self, code: CalendarManifestErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)


class _DuplicateKey(ValueError):
    pass


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _normalize_digits(value: object) -> object:
    return value.translate(_DIGITS) if isinstance(value, str) else value


def _parse_local_time(value: object) -> time | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("local time must be a string or null")
    normalized = value.translate(_DIGITS).strip()
    match = _TIME.fullmatch(normalized)
    if match is None:
        raise ValueError("local time must use HH:MM[:SS[.ffffff]]")
    try:
        return time(
            hour=int(match.group(1)),
            minute=int(match.group(2)),
            second=int(match.group(3) or "0"),
            microsecond=int((match.group(4) or "0").ljust(6, "0")),
        )
    except ValueError as error:
        raise ValueError("local time is invalid") from error


class CalendarSession(_StrictModel):
    trading_date: date
    session_code: Literal["REGULAR"]
    is_trading_day: bool
    open_local_time: time | None
    close_local_time: time | None
    source_status: str = Field(min_length=1, max_length=128)
    settlement_date: date | None = None
    open_fold: Literal[0, 1] | None = None
    close_fold: Literal[0, 1] | None = None
    metadata: dict[str, object] = Field(default_factory=dict)

    _date_digits = field_validator("trading_date", "settlement_date", mode="before")(
        _normalize_digits
    )
    _time_values = field_validator("open_local_time", "close_local_time", mode="before")(
        _parse_local_time
    )

    @model_validator(mode="after")
    def validate_open_closed_shape(self) -> Self:
        if self.is_trading_day:
            if self.open_local_time is None or self.close_local_time is None:
                raise ValueError("open trading days require both local times")
        elif self.open_local_time is not None or self.close_local_time is not None:
            raise ValueError("closed days require both local times to be null")
        return self


class CalendarManifest(_StrictModel):
    schema_version: Literal[1]
    calendar_id: str = Field(min_length=1, max_length=128)
    venue_code: str = Field(min_length=1, max_length=32)
    timezone: str = Field(min_length=1, max_length=64)
    date_from: date
    date_to: date
    sessions: tuple[CalendarSession, ...]

    _date_digits = field_validator("date_from", "date_to", mode="before")(_normalize_digits)

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        if self.date_to < self.date_from:
            raise ValueError("date_to must not precede date_from")
        if not self.sessions:
            raise ValueError("sessions must not be empty")
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as error:
            raise ValueError("timezone must be a valid IANA timezone") from error
        return self


@dataclass(frozen=True, slots=True)
class CalendarManifestDocument:
    payload_bytes: bytes
    payload_sha256: str
    raw_payload: dict[str, object]
    manifest: CalendarManifest


@dataclass(frozen=True, slots=True)
class ValidatedCalendarSession:
    trading_date: date
    session_code: str
    is_trading_day: bool
    open_local_time: time | None
    close_local_time: time | None
    session_open_ts: datetime | None
    session_close_ts: datetime | None
    settlement_date: date | None
    source_status: str
    metadata: dict[str, object]


@dataclass(frozen=True, slots=True)
class CalendarValidationResult:
    document: CalendarManifestDocument
    sessions: tuple[ValidatedCalendarSession, ...]


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(key)
        result[key] = value
    return result


def _forbid_constant(value: str) -> object:
    raise ValueError(value)


def load_calendar_manifest(path: str | Path) -> CalendarManifestDocument:
    try:
        payload_bytes = Path(path).read_bytes()
        decoded = payload_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise CalendarManifestError(
            CalendarManifestErrorCode.INVALID_UTF8,
            "calendar is not UTF-8",
        ) from error
    except OSError as error:
        raise CalendarManifestError(
            CalendarManifestErrorCode.INVALID_MANIFEST,
            "calendar cannot be read",
        ) from error
    try:
        raw = json.loads(
            decoded,
            parse_constant=_forbid_constant,
            object_pairs_hook=_pairs,
        )
    except _DuplicateKey as error:
        raise CalendarManifestError(
            CalendarManifestErrorCode.DUPLICATE_JSON_FIELD,
            "duplicate JSON field",
        ) from error
    except (json.JSONDecodeError, ValueError) as error:
        raise CalendarManifestError(
            CalendarManifestErrorCode.MALFORMED_JSON,
            "calendar JSON is malformed",
        ) from error
    if not isinstance(raw, dict):
        raise CalendarManifestError(
            CalendarManifestErrorCode.INVALID_MANIFEST,
            "calendar root must be an object",
        )
    if type(raw.get("schema_version")) is not int or raw.get("schema_version") != 1:
        raise CalendarManifestError(
            CalendarManifestErrorCode.UNSUPPORTED_SCHEMA_VERSION,
            "unsupported schema_version",
        )
    try:
        manifest = CalendarManifest.model_validate(raw)
    except ValidationError as error:
        message = "; ".join(str(item["msg"]) for item in error.errors()[:3])
        raise CalendarManifestError(
            CalendarManifestErrorCode.INVALID_MANIFEST,
            message or "calendar is invalid",
        ) from error
    return CalendarManifestDocument(
        payload_bytes=payload_bytes,
        payload_sha256=hashlib.sha256(payload_bytes).hexdigest(),
        raw_payload=raw,
        manifest=manifest,
    )


def _to_utc(
    *,
    trading_date: date,
    local_time: time,
    timezone: ZoneInfo,
    fold: int | None,
) -> datetime:
    naive = datetime.combine(trading_date, local_time)
    candidates: dict[datetime, tuple[int, datetime]] = {}
    for candidate_fold in (0, 1):
        candidate = naive.replace(tzinfo=timezone, fold=candidate_fold)
        utc_value = candidate.astimezone(UTC)
        round_trip = utc_value.astimezone(timezone)
        if round_trip.replace(tzinfo=None) == naive:
            candidates[utc_value] = (candidate_fold, round_trip)
    if not candidates:
        raise CalendarManifestError(
            CalendarManifestErrorCode.INVALID_MANIFEST,
            "local time is nonexistent",
        )
    if len(candidates) > 1 and fold is None:
        raise CalendarManifestError(
            CalendarManifestErrorCode.INVALID_MANIFEST,
            "local time is ambiguous",
        )
    if fold is not None:
        for utc_value, (candidate_fold, _) in candidates.items():
            if candidate_fold == fold:
                return utc_value
        raise CalendarManifestError(
            CalendarManifestErrorCode.INVALID_MANIFEST,
            "requested local-time fold is invalid",
        )
    return next(iter(candidates))


def validate_calendar_manifest(document: CalendarManifestDocument) -> CalendarValidationResult:
    """Validate explicit completeness and convert every valid local session to UTC."""

    manifest = document.manifest
    expected_dates: list[date] = []
    current = manifest.date_from
    while current <= manifest.date_to:
        expected_dates.append(current)
        current += timedelta(days=1)
    seen: set[date] = set()
    for item in manifest.sessions:
        if item.trading_date in seen:
            raise CalendarManifestError(
                CalendarManifestErrorCode.INVALID_MANIFEST,
                "duplicate trading_date",
            )
        if item.trading_date < manifest.date_from or item.trading_date > manifest.date_to:
            raise CalendarManifestError(
                CalendarManifestErrorCode.INVALID_MANIFEST,
                "trading_date outside declared range",
            )
        seen.add(item.trading_date)
    if seen != set(expected_dates):
        raise CalendarManifestError(
            CalendarManifestErrorCode.INVALID_MANIFEST,
            "calendar range must be complete",
        )
    timezone = ZoneInfo(manifest.timezone)
    validated: list[ValidatedCalendarSession] = []
    for item in sorted(manifest.sessions, key=lambda value: value.trading_date):
        if item.is_trading_day:
            assert item.open_local_time is not None and item.close_local_time is not None
            open_ts = _to_utc(
                trading_date=item.trading_date,
                local_time=item.open_local_time,
                timezone=timezone,
                fold=item.open_fold,
            )
            close_ts = _to_utc(
                trading_date=item.trading_date,
                local_time=item.close_local_time,
                timezone=timezone,
                fold=item.close_fold,
            )
            if close_ts <= open_ts:
                raise CalendarManifestError(
                    CalendarManifestErrorCode.INVALID_MANIFEST,
                    "close must be after open",
                )
        else:
            open_ts = None
            close_ts = None
        validated.append(
            ValidatedCalendarSession(
                trading_date=item.trading_date,
                session_code=item.session_code,
                is_trading_day=item.is_trading_day,
                open_local_time=item.open_local_time,
                close_local_time=item.close_local_time,
                session_open_ts=open_ts,
                session_close_ts=close_ts,
                settlement_date=item.settlement_date,
                source_status=item.source_status,
                metadata=dict(item.metadata),
            )
        )
    return CalendarValidationResult(document=document, sessions=tuple(validated))


def calendar_source_record_key(
    manifest: CalendarManifest,
    session: ValidatedCalendarSession,
) -> str:
    return (
        f"bisfin|calendar|{manifest.calendar_id}|{manifest.venue_code}|"
        f"{session.session_code}|{session.trading_date.isoformat()}"
    )


__all__ = [
    "CalendarManifest",
    "CalendarManifestDocument",
    "CalendarManifestError",
    "CalendarManifestErrorCode",
    "CalendarSession",
    "CalendarValidationResult",
    "ValidatedCalendarSession",
    "calendar_source_record_key",
    "load_calendar_manifest",
    "validate_calendar_manifest",
]
