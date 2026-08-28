"""Strict parsing for the documented per-symbol BrsApi metadata response."""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal

from bisfin.db.errors import redact_secrets
from bisfin.integrations.brsapi.contracts import (
    BrsApiContractError,
    BrsApiMalformedResponseError,
    BrsApiProviderError,
    BrsApiRawResponse,
    BrsApiSymbolMetadata,
    JsonObject,
)
from bisfin.integrations.brsapi.normalization import normalize_brsapi_symbol

_ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


class _DuplicateKey(ValueError):
    pass


def _pairs_to_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(key)
        result[key] = value
    return result


def _forbid_non_finite(value: str) -> object:
    raise ValueError(value)


def _normalized_isin(value: object) -> str:
    if not isinstance(value, str) or isinstance(value, bool):
        raise BrsApiContractError("Symbol response ISIN must be a string.")
    normalized = value.strip().upper()
    if not normalized or _ISIN.fullmatch(normalized) is None:
        raise BrsApiContractError("Symbol response ISIN is invalid.")
    return normalized


def _required_string(payload: JsonObject, field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or isinstance(value, bool) or not value.strip():
        raise BrsApiContractError(f"Symbol response {field} must be a non-empty string.")
    return value


def _optional_string(payload: JsonObject, field: str) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or isinstance(value, bool):
        raise BrsApiContractError(f"Symbol response {field} must be a string when present.")
    return value


def parse_symbol_metadata(response: BrsApiRawResponse) -> BrsApiSymbolMetadata:
    """Parse one explicit top-level object and preserve every unknown JSON field."""

    try:
        decoded = response.body_bytes.decode("utf-8", errors="strict")
        payload = json.loads(
            decoded,
            parse_float=Decimal,
            parse_int=int,
            parse_constant=_forbid_non_finite,
            object_pairs_hook=_pairs_to_object,
        )
    except UnicodeDecodeError as error:
        raise BrsApiMalformedResponseError("BrsApi Symbol response is not UTF-8 JSON.") from error
    except (json.JSONDecodeError, _DuplicateKey, ValueError) as error:
        raise BrsApiMalformedResponseError(
            "BrsApi Symbol response is malformed or ambiguous."
        ) from error
    if not isinstance(payload, dict) or not payload:
        raise BrsApiContractError("BrsApi Symbol success response must be a non-empty object.")
    raw_payload: JsonObject = payload
    if raw_payload.get("successful") is False or raw_payload.get("message_error") is not None:
        message = raw_payload.get("message_error")
        status = raw_payload.get("status")
        code_http = raw_payload.get("code_http")
        raise BrsApiProviderError(
            code_http=code_http if type(code_http) is int else None,
            status=status if isinstance(status, str) else None,
            message_error=redact_secrets(message) if isinstance(message, str) else None,
            raw_payload=raw_payload,
        )
    original_symbol = _required_string(raw_payload, "l18")
    normalized_symbol = normalize_brsapi_symbol(original_symbol)
    if not normalized_symbol:
        raise BrsApiContractError("Symbol response l18 must not normalize to empty.")
    market = _required_string(raw_payload, "m").strip()
    return BrsApiSymbolMetadata(
        original_symbol=original_symbol,
        normalized_symbol=normalized_symbol,
        isin=_normalized_isin(raw_payload.get("isin")),
        market=market,
        name_fa=_optional_string(raw_payload, "l30"),
        name_en=_optional_string(raw_payload, "l30_en"),
        market_board=_optional_string(raw_payload, "m_board"),
        industry=_optional_string(raw_payload, "cs"),
        date_update=_optional_string(raw_payload, "date_update"),
        source_time=_optional_string(raw_payload, "time"),
        state=_optional_string(raw_payload, "state"),
        response_sha256=hashlib.sha256(response.body_bytes).hexdigest(),
        raw_payload=raw_payload,
    )


__all__ = ["parse_symbol_metadata"]
