"""Fail-closed parsing and validation for BrsApi Candlestick ``type=2``.

The provider documentation lists row fields but no successful JSON nesting.
Bisfin therefore makes one narrow, testable assumption: successful deterministic
fixtures are a non-empty top-level JSON array of row objects.  No other success
shape is guessed or silently adapted.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from typing import NoReturn, TypeGuard

from bisfin.integrations.brsapi.contracts import (
    BrsApiCandlestickResponse,
    BrsApiCandlestickRow,
    BrsApiContractError,
    BrsApiMalformedResponseError,
    BrsApiNoDataResponse,
    BrsApiProviderError,
    BrsApiRawResponse,
    DailyBarParseResult,
    JsonObject,
    JsonValue,
    ParsedDailyBarCandidate,
    RowValidationCode,
    RowValidationIssue,
    RowValidationResult,
)
from bisfin.integrations.brsapi.normalization import (
    normalize_brsapi_symbol,
    normalize_digits,
    parse_jalali_date,
    parse_source_time,
)

_REQUIRED_ROW_FIELDS = (
    "l18",
    "type",
    "count",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
)


def response_payload_sha256(body_bytes: bytes) -> str:
    """Hash the exact provider/fixture bytes without decoding or reformatting."""

    return hashlib.sha256(body_bytes).hexdigest()


def canonical_json_bytes(value: JsonValue) -> bytes:
    """Serialize JSON deterministically without converting Decimal through float."""

    return _canonical_json_text(value).encode("utf-8")


def row_payload_sha256(raw_row: JsonObject) -> str:
    """Hash sorted, compact, UTF-8 canonical JSON for one parsed raw row."""

    return hashlib.sha256(canonical_json_bytes(raw_row)).hexdigest()


def parse_candlestick_envelope(
    response: BrsApiRawResponse,
) -> BrsApiCandlestickResponse | BrsApiNoDataResponse:
    """Decode the only project-supported success/no-data shapes.

    A provider-declared error is raised as :class:`BrsApiProviderError`; malformed
    or undocumented shapes fail closed as a contract error.
    """

    payload = _load_json(response.body_bytes)
    if isinstance(payload, list):
        if not payload:
            raise BrsApiContractError(
                "Empty arrays are ambiguous; BrsApi no-data must use status=no_data."
            )
        rows: list[JsonObject] = []
        for item in payload:
            if not isinstance(item, dict):
                raise BrsApiContractError(
                    "Candlestick success arrays must contain only JSON objects."
                )
            rows.append(item)
        return BrsApiCandlestickResponse(rows=tuple(rows))

    if not isinstance(payload, dict):
        raise BrsApiContractError(
            "BrsApi response must be a candle array or an explicit status object."
        )

    status_value = payload.get("status")
    successful_value = payload.get("successful")
    code_value = payload.get("code_http")
    message_value = payload.get("message_error")

    if status_value == "no_data":
        if successful_value is not True or not _is_plain_int(code_value) or code_value != 200:
            raise BrsApiContractError("The no-data envelope has invalid success metadata.")
        if message_value is not None:
            raise BrsApiContractError("The no-data envelope must have a null error message.")
        return BrsApiNoDataResponse(
            code_http=code_value,
            successful=True,
            status="no_data",
            message_error=None,
            raw_payload=payload,
        )

    if (
        successful_value is False
        or message_value is not None
        or status_value in {"error", "failed", "failure"}
    ):
        code_http = code_value if _is_plain_int(code_value) else None
        status = status_value if isinstance(status_value, str) else None
        message_error = message_value if isinstance(message_value, str) else None
        raise BrsApiProviderError(
            code_http=code_http,
            status=status,
            message_error=_redact_provider_text(message_error),
            raw_payload=payload,
        )

    raise BrsApiContractError(
        "The object response is neither documented no-data nor an explicit provider error."
    )


def parse_unadjusted_daily_candles(
    response: BrsApiRawResponse,
    *,
    requested_symbol: str,
) -> DailyBarParseResult:
    """Parse, validate, normalize, and de-duplicate one type=2 acquisition."""

    normalized_request_symbol = normalize_brsapi_symbol(requested_symbol)
    if not normalized_request_symbol:
        raise ValueError("requested_symbol must not be empty")

    digest = response_payload_sha256(response.body_bytes)
    envelope = parse_candlestick_envelope(response)
    if isinstance(envelope, BrsApiNoDataResponse):
        return DailyBarParseResult(response_sha256=digest, no_data=envelope, rows=())

    row_count = len(envelope.rows)
    results = tuple(
        _validate_row(
            raw_row,
            source_sequence=sequence,
            requested_symbol=normalized_request_symbol,
            actual_row_count=row_count,
        )
        for sequence, raw_row in enumerate(envelope.rows, start=1)
    )
    return DailyBarParseResult(
        response_sha256=digest,
        no_data=None,
        rows=_apply_duplicate_policy(results),
    )


def _load_json(body_bytes: bytes) -> JsonValue:
    try:
        text = body_bytes.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as error:
        raise BrsApiMalformedResponseError("BrsApi response is not valid UTF-8.") from error

    try:
        parsed: object = json.loads(
            text,
            parse_float=Decimal,
            parse_int=int,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, ValueError) as error:
        raise BrsApiMalformedResponseError("BrsApi response is not valid strict JSON.") from error
    return _coerce_json_value(parsed)


def _reject_json_constant(value: str) -> NoReturn:
    raise ValueError(f"non-standard JSON numeric constant {value!r} is not accepted")


def _coerce_json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (bool, int, Decimal, str)):
        return value
    if isinstance(value, list):
        return [_coerce_json_value(item) for item in value]
    if isinstance(value, dict):
        result: JsonObject = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise BrsApiContractError("JSON object keys must be strings.")
            result[key] = _coerce_json_value(item)
        return result
    raise BrsApiContractError("Response contains an unsupported JSON value type.")


def _canonical_json_text(value: JsonValue) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal):
        return _canonical_decimal_text(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, list):
        return "[" + ",".join(_canonical_json_text(item) for item in value) + "]"
    if isinstance(value, dict):
        fields = (
            json.dumps(key, ensure_ascii=False, separators=(",", ":"))
            + ":"
            + _canonical_json_text(value[key])
            for key in sorted(value)
        )
        return "{" + ",".join(fields) + "}"
    raise TypeError("canonical JSON accepts only validated JSON values")


def _canonical_decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("canonical JSON cannot contain non-finite Decimal values")
    if value.is_zero():
        return "0"
    rendered = format(value.normalize(), "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _validate_row(
    raw_row: JsonObject,
    *,
    source_sequence: int,
    requested_symbol: str,
    actual_row_count: int,
) -> RowValidationResult:
    errors: list[RowValidationIssue] = []
    warnings: list[RowValidationIssue] = []

    for field in _REQUIRED_ROW_FIELDS:
        if field not in raw_row:
            errors.append(
                _issue(RowValidationCode.MISSING_FIELD, field, "Required field is absent.")
            )

    original_symbol = _string_field(raw_row, "l18", errors)
    normalized_symbol = normalize_brsapi_symbol(original_symbol) if original_symbol else ""
    if original_symbol is not None and not normalized_symbol:
        errors.append(_issue(RowValidationCode.EMPTY_SYMBOL, "l18", "Symbol is empty."))
    elif normalized_symbol and normalized_symbol != requested_symbol:
        errors.append(
            _issue(
                RowValidationCode.SYMBOL_MISMATCH,
                "l18",
                "Response symbol does not match the requested normalized symbol.",
            )
        )

    response_type = _integer_field(raw_row, "type", errors)
    if response_type is not None and response_type != 2:
        warnings.append(
            _issue(
                RowValidationCode.RESPONSE_TYPE_IGNORED,
                "type",
                "Response type is informational; request type=2 remains authoritative.",
            )
        )

    declared_count = _integer_field(raw_row, "count", errors)
    if declared_count is not None:
        if declared_count < 0:
            errors.append(
                _issue(
                    RowValidationCode.INVALID_NUMERIC_VALUE, "count", "Count cannot be negative."
                )
            )
        elif declared_count != actual_row_count:
            warnings.append(
                _issue(
                    RowValidationCode.COUNT_MISMATCH,
                    "count",
                    "Declared count differs from the actual response row count.",
                )
            )

    source_date_text = _string_field(raw_row, "date", errors)
    trading_date = None
    if source_date_text is not None:
        try:
            trading_date = parse_jalali_date(source_date_text)
        except (TypeError, ValueError):
            errors.append(
                _issue(RowValidationCode.INVALID_JALALI_DATE, "date", "Jalali date is invalid.")
            )

    source_time_text: str | None = None
    source_time = None
    if "time" in raw_row and raw_row["time"] is not None:
        source_time_text = _string_field(raw_row, "time", errors)
        if source_time_text is not None:
            try:
                source_time = parse_source_time(source_time_text)
            except (TypeError, ValueError):
                errors.append(
                    _issue(
                        RowValidationCode.INVALID_SOURCE_TIME,
                        "time",
                        "Optional source time is invalid.",
                    )
                )

    open_price = _decimal_field(raw_row, "open", errors)
    high_price = _decimal_field(raw_row, "high", errors)
    low_price = _decimal_field(raw_row, "low", errors)
    close_price = _decimal_field(raw_row, "close", errors)
    volume = _decimal_field(raw_row, "volume", errors)

    for field, value in (
        ("open", open_price),
        ("high", high_price),
        ("low", low_price),
        ("close", close_price),
    ):
        if value is not None and value < 0:
            errors.append(
                _issue(
                    RowValidationCode.INVALID_NUMERIC_VALUE,
                    field,
                    "Price cannot be negative.",
                )
            )
    if volume is not None and volume < 0:
        errors.append(
            _issue(RowValidationCode.NEGATIVE_VOLUME, "volume", "Volume cannot be negative.")
        )

    if high_price is not None and low_price is not None and high_price < low_price:
        errors.append(
            _issue(RowValidationCode.INVALID_OHLC, None, "High price cannot be below low price.")
        )
    if (
        open_price is not None
        and high_price is not None
        and low_price is not None
        and not low_price <= open_price <= high_price
    ):
        errors.append(
            _issue(RowValidationCode.INVALID_OHLC, "open", "Open price is outside low/high.")
        )
    if (
        close_price is not None
        and high_price is not None
        and low_price is not None
        and not low_price <= close_price <= high_price
    ):
        errors.append(
            _issue(RowValidationCode.INVALID_OHLC, "close", "Close price is outside low/high.")
        )

    candidate = None
    required_values = (
        original_symbol,
        response_type,
        declared_count,
        source_date_text,
        trading_date,
        open_price,
        high_price,
        low_price,
        close_price,
        volume,
    )
    if not errors and all(value is not None for value in required_values):
        assert original_symbol is not None
        assert response_type is not None
        assert declared_count is not None
        assert source_date_text is not None
        assert trading_date is not None
        assert open_price is not None
        assert high_price is not None
        assert low_price is not None
        assert close_price is not None
        assert volume is not None
        row = BrsApiCandlestickRow(
            original_symbol=original_symbol,
            response_type=response_type,
            declared_count=declared_count,
            source_date_text=source_date_text,
            source_time_text=source_time_text,
            open=open_price,
            high=high_price,
            low=low_price,
            close=close_price,
            volume=volume,
            raw_payload=raw_row,
        )
        candidate = ParsedDailyBarCandidate(
            source_sequence=source_sequence,
            original_symbol=row.original_symbol,
            normalized_symbol=normalized_symbol,
            source_date_text=row.source_date_text,
            source_time_text=row.source_time_text,
            source_time=source_time,
            trading_date=trading_date,
            open=row.open,
            high=row.high,
            low=row.low,
            close=row.close,
            volume=row.volume,
            raw_payload=row.raw_payload,
            row_payload_sha256=row_payload_sha256(row.raw_payload),
        )

    return RowValidationResult(
        source_sequence=source_sequence,
        raw_payload=raw_row,
        candidate=candidate,
        errors=tuple(errors),
        warnings=tuple(warnings),
        include_in_canonicalization=candidate is not None,
    )


def _apply_duplicate_policy(
    results: tuple[RowValidationResult, ...],
) -> tuple[RowValidationResult, ...]:
    grouped: dict[tuple[str, object], list[int]] = {}
    for index, result in enumerate(results):
        if not result.accepted or result.candidate is None:
            continue
        key = (result.candidate.normalized_symbol, result.candidate.trading_date)
        grouped.setdefault(key, []).append(index)

    mutable = list(results)
    for indexes in grouped.values():
        if len(indexes) < 2:
            continue
        hashes: set[str] = set()
        for index in indexes:
            candidate = mutable[index].candidate
            if candidate is not None:
                hashes.add(candidate.row_payload_sha256)
        if len(hashes) == 1:
            warning = _issue(
                RowValidationCode.DUPLICATE_IDENTICAL,
                "date",
                "Equivalent duplicate rows are preserved; only the first is canonicalized.",
            )
            for offset, index in enumerate(indexes):
                result = mutable[index]
                mutable[index] = replace(
                    result,
                    warnings=(*result.warnings, warning),
                    include_in_canonicalization=offset == 0,
                )
            continue

        error = _issue(
            RowValidationCode.DUPLICATE_CONFLICT,
            "date",
            "Conflicting duplicate dates are rejected as one ambiguous group.",
        )
        for index in indexes:
            result = mutable[index]
            mutable[index] = replace(
                result,
                errors=(*result.errors, error),
                include_in_canonicalization=False,
            )
    return tuple(mutable)


def _string_field(
    raw_row: JsonObject,
    field: str,
    errors: list[RowValidationIssue],
) -> str | None:
    if field not in raw_row:
        return None
    value = raw_row[field]
    if not isinstance(value, str):
        errors.append(
            _issue(RowValidationCode.INVALID_FIELD_TYPE, field, "Field must be a string.")
        )
        return None
    return value


def _integer_field(
    raw_row: JsonObject,
    field: str,
    errors: list[RowValidationIssue],
) -> int | None:
    if field not in raw_row:
        return None
    value = raw_row[field]
    if _is_plain_int(value):
        return value
    if isinstance(value, str):
        normalized = normalize_digits(value).strip()
        if normalized and normalized.removeprefix("+").isdigit():
            return int(normalized)
    errors.append(_issue(RowValidationCode.INVALID_FIELD_TYPE, field, "Field must be an integer."))
    return None


def _decimal_field(
    raw_row: JsonObject,
    field: str,
    errors: list[RowValidationIssue],
) -> Decimal | None:
    if field not in raw_row:
        return None
    value = raw_row[field]
    if isinstance(value, bool) or isinstance(value, float) or value is None:
        errors.append(
            _issue(
                RowValidationCode.INVALID_NUMERIC_VALUE,
                field,
                "Field must be a finite decimal number.",
            )
        )
        return None
    try:
        if isinstance(value, Decimal):
            parsed = value
        elif isinstance(value, int):
            parsed = Decimal(value)
        elif isinstance(value, str):
            parsed = Decimal(normalize_digits(value).strip())
        else:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        errors.append(
            _issue(
                RowValidationCode.INVALID_NUMERIC_VALUE,
                field,
                "Field must be a finite decimal number.",
            )
        )
        return None
    if not parsed.is_finite():
        errors.append(
            _issue(
                RowValidationCode.INVALID_NUMERIC_VALUE,
                field,
                "Field must be a finite decimal number.",
            )
        )
        return None
    return parsed


def _is_plain_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _issue(
    code: RowValidationCode,
    field: str | None,
    message: str,
) -> RowValidationIssue:
    return RowValidationIssue(code=code, field=field, message=message)


def _redact_provider_text(value: str | None) -> str | None:
    if value is None:
        return None
    # Provider diagnostics occasionally contain a complete keyed URL.  Keep a
    # bounded message while removing any query-token value before it reaches an
    # exception, log, or CLI surface.
    parts = value.split("?")
    if len(parts) == 1:
        return value[:512]
    query_parts = parts[1].split("&")
    sanitized = ["key=***" if item.lower().startswith("key=") else item for item in query_parts]
    return (parts[0] + "?" + "&".join(sanitized))[:512]


__all__ = [
    "canonical_json_bytes",
    "parse_candlestick_envelope",
    "parse_unadjusted_daily_candles",
    "response_payload_sha256",
    "row_payload_sha256",
]
