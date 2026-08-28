"""Typed provider contracts for the narrow BrsApi daily-candle integration.

These types deliberately do not model database rows.  The committed provider
documentation does not show a successful JSON envelope, so the parser treats
the deterministic fixture envelope as an explicit project contract and rejects
all other shapes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum

type JsonValue = None | bool | int | Decimal | str | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]


def _require_aware(value: datetime, *, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class BrsApiRawResponse:
    """Exact response bytes plus bounded, secret-free transport metadata."""

    status_code: int
    headers: tuple[tuple[str, str], ...]
    body_bytes: bytes
    request_started_at: datetime
    response_received_at: datetime
    elapsed: timedelta

    def __post_init__(self) -> None:
        _require_aware(self.request_started_at, field="request_started_at")
        _require_aware(self.response_received_at, field="response_received_at")
        if self.response_received_at < self.request_started_at:
            raise ValueError("response_received_at cannot precede request_started_at")
        if self.elapsed < timedelta(0):
            raise ValueError("elapsed cannot be negative")


@dataclass(frozen=True, slots=True)
class BrsApiCandlestickResponse:
    """Successful project-fixture envelope: a non-empty top-level row array."""

    rows: tuple[JsonObject, ...]

    def __post_init__(self) -> None:
        if not self.rows:
            raise ValueError("a successful candlestick response cannot be empty")


@dataclass(frozen=True, slots=True)
class BrsApiNoDataResponse:
    """The only no-data representation explicitly shown in provider docs."""

    code_http: int
    successful: bool
    status: str
    message_error: None
    raw_payload: JsonObject


@dataclass(frozen=True, slots=True)
class BrsApiCandlestickRow:
    """One structurally and financially valid provider candle row."""

    original_symbol: str
    response_type: int
    declared_count: int
    source_date_text: str
    source_time_text: str | None
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    raw_payload: JsonObject


@dataclass(frozen=True, slots=True)
class BrsApiSymbolMetadata:
    """Catalog-relevant fields from one strict Symbol.php object response."""

    original_symbol: str
    normalized_symbol: str
    isin: str
    market: str
    name_fa: str | None
    name_en: str | None
    market_board: str | None
    industry: str | None
    date_update: str | None
    source_time: str | None
    state: str | None
    response_sha256: str
    raw_payload: JsonObject


@dataclass(frozen=True, slots=True)
class ParsedDailyBarCandidate:
    """Side-effect-free normalized candidate awaiting catalog resolution."""

    source_sequence: int
    original_symbol: str
    normalized_symbol: str
    source_date_text: str
    source_time_text: str | None
    source_time: time | None
    trading_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    raw_payload: JsonObject
    row_payload_sha256: str


class RowValidationCode(StrEnum):
    """Stable codes suitable for raw-event validation JSON."""

    MISSING_FIELD = "MISSING_FIELD"
    INVALID_FIELD_TYPE = "INVALID_FIELD_TYPE"
    EMPTY_SYMBOL = "EMPTY_SYMBOL"
    SYMBOL_MISMATCH = "SYMBOL_MISMATCH"
    INVALID_JALALI_DATE = "INVALID_JALALI_DATE"
    INVALID_SOURCE_TIME = "INVALID_SOURCE_TIME"
    INVALID_NUMERIC_VALUE = "INVALID_NUMERIC_VALUE"
    NEGATIVE_VOLUME = "NEGATIVE_VOLUME"
    INVALID_OHLC = "INVALID_OHLC"
    DUPLICATE_CONFLICT = "DUPLICATE_CONFLICT"
    DUPLICATE_IDENTICAL = "DUPLICATE_IDENTICAL"
    COUNT_MISMATCH = "COUNT_MISMATCH"
    RESPONSE_TYPE_IGNORED = "RESPONSE_TYPE_IGNORED"


@dataclass(frozen=True, slots=True)
class RowValidationIssue:
    """A bounded diagnostic that never includes raw payloads or credentials."""

    code: RowValidationCode
    field: str | None
    message: str


@dataclass(frozen=True, slots=True)
class RowValidationResult:
    """Validation outcome for one raw row, including duplicate disposition."""

    source_sequence: int
    raw_payload: JsonObject
    candidate: ParsedDailyBarCandidate | None
    errors: tuple[RowValidationIssue, ...] = ()
    warnings: tuple[RowValidationIssue, ...] = ()
    include_in_canonicalization: bool = False

    @property
    def accepted(self) -> bool:
        return self.candidate is not None and not self.errors


@dataclass(frozen=True, slots=True)
class DailyBarParseResult:
    """Complete deterministic parse result for one response acquisition."""

    response_sha256: str
    no_data: BrsApiNoDataResponse | None
    rows: tuple[RowValidationResult, ...]

    @property
    def canonical_candidates(self) -> tuple[ParsedDailyBarCandidate, ...]:
        return tuple(
            result.candidate
            for result in self.rows
            if result.include_in_canonicalization and result.candidate is not None
        )

    @property
    def accepted_count(self) -> int:
        return sum(result.accepted for result in self.rows)

    @property
    def rejected_count(self) -> int:
        return len(self.rows) - self.accepted_count


class BrsApiError(Exception):
    """Base class for expected, secret-safe BrsApi failures."""


class BrsApiConfigurationError(BrsApiError):
    """Live BrsApi client configuration is missing or unsafe."""


class BrsApiTransportError(BrsApiError):
    """A synchronous transport failure occurred before a usable response."""


class BrsApiTimeoutError(BrsApiTransportError):
    """The configured connect or read timeout elapsed."""


class BrsApiHttpError(BrsApiError):
    """BrsApi returned a non-successful HTTP status."""

    def __init__(self, status_code: int, *, response: BrsApiRawResponse) -> None:
        self.status_code = status_code
        self.response = response
        super().__init__(f"BrsApi returned HTTP status {status_code}.")


class BrsApiFixtureError(BrsApiError):
    """A deterministic fixture could not be read as UTF-8 bytes."""


class BrsApiContractError(BrsApiError):
    """A JSON response does not match the explicit project contract."""


class BrsApiMalformedResponseError(BrsApiContractError):
    """Response bytes are not valid UTF-8 JSON."""


class BrsApiProviderError(BrsApiError):
    """A JSON error envelope explicitly reported failure."""

    def __init__(
        self,
        *,
        code_http: int | None,
        status: str | None,
        message_error: str | None,
        raw_payload: JsonObject,
    ) -> None:
        self.code_http = code_http
        self.status = status
        self.message_error = message_error
        self.raw_payload = raw_payload
        detail = message_error or status or "unspecified provider error"
        super().__init__(f"BrsApi reported an error: {detail}")


__all__ = [
    "BrsApiCandlestickResponse",
    "BrsApiCandlestickRow",
    "BrsApiConfigurationError",
    "BrsApiContractError",
    "BrsApiError",
    "BrsApiFixtureError",
    "BrsApiHttpError",
    "BrsApiMalformedResponseError",
    "BrsApiNoDataResponse",
    "BrsApiProviderError",
    "BrsApiRawResponse",
    "BrsApiTimeoutError",
    "BrsApiTransportError",
    "DailyBarParseResult",
    "JsonObject",
    "JsonValue",
    "ParsedDailyBarCandidate",
    "RowValidationCode",
    "RowValidationIssue",
    "RowValidationResult",
]
