"""Pure helpers shared by the BrsApi daily-bar application service."""

from __future__ import annotations

from enum import StrEnum

from bisfin.domain.ingestion import IngestionBatchStatus
from bisfin.integrations.brsapi.contracts import JsonObject, RowValidationIssue


class CanonicalizationCode(StrEnum):
    """Stable database-aware row validation codes."""

    INSTRUMENT_NOT_FOUND = "INSTRUMENT_NOT_FOUND"
    INSTRUMENT_VENUE_MISSING = "INSTRUMENT_VENUE_MISSING"
    TRADING_SESSION_NOT_FOUND = "TRADING_SESSION_NOT_FOUND"
    INVALID_TRADING_SESSION = "INVALID_TRADING_SESSION"
    RESPONSE_BEFORE_SESSION_CLOSE = "RESPONSE_BEFORE_SESSION_CLOSE"
    SYSTEM_AVAILABILITY_BEFORE_PUBLIC = "SYSTEM_AVAILABILITY_BEFORE_PUBLIC"


def source_record_key(
    *,
    normalized_symbol: str,
    source_date_text: str | None,
) -> str:
    """Return the documented deterministic BrsApi row key without numeric coercion."""

    date_component = source_date_text.strip() if source_date_text else "unknown-date"
    return f"brsapi|candlestick|type=2|{normalized_symbol}|{date_component}"


def raw_string(raw_payload: JsonObject, field: str) -> str | None:
    """Return a provider string unchanged; non-strings are deliberately absent."""

    value = raw_payload.get(field)
    return value if isinstance(value, str) else None


def issue_payloads(
    *,
    errors: tuple[RowValidationIssue, ...] = (),
    warnings: tuple[RowValidationIssue, ...] = (),
) -> list[object]:
    """Convert bounded typed diagnostics to JSON-compatible audit objects."""

    payloads: list[object] = []
    for severity, issues in (("ERROR", errors), ("WARNING", warnings)):
        payloads.extend(
            {
                "code": issue.code.value,
                "field": issue.field,
                "message": issue.message,
                "severity": severity,
            }
            for issue in issues
        )
    return payloads


def canonicalization_issue(
    code: CanonicalizationCode,
    *,
    field: str | None,
    message: str,
) -> dict[str, object]:
    """Build one bounded, secret-free database validation diagnostic."""

    return {
        "code": code.value,
        "field": field,
        "message": message,
        "severity": "ERROR",
    }


def terminal_status(*, accepted_count: int, rejected_count: int) -> IngestionBatchStatus:
    """Map row outcomes to the explicitly supported batch statuses."""

    if accepted_count < 0 or rejected_count < 0:
        raise ValueError("batch counts cannot be negative")
    if accepted_count and rejected_count:
        return IngestionBatchStatus.PARTIAL
    if accepted_count:
        return IngestionBatchStatus.SUCCEEDED
    if rejected_count:
        return IngestionBatchStatus.QUARANTINED
    return IngestionBatchStatus.SUCCEEDED


__all__ = [
    "CanonicalizationCode",
    "canonicalization_issue",
    "issue_payloads",
    "raw_string",
    "source_record_key",
    "terminal_status",
]
