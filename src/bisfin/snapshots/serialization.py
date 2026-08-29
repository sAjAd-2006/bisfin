"""Canonical byte serialization used by immutable snapshot artifacts."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any


def canonical_datetime(value: datetime) -> str:
    """Render one aware timestamp as a compact UTC RFC-3339 value."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    normalized = value.astimezone(UTC)
    if normalized.microsecond == 0:
        return normalized.strftime("%Y-%m-%dT%H:%M:%SZ")
    fraction = f"{normalized.microsecond:06d}".rstrip("0")
    return normalized.strftime("%Y-%m-%dT%H:%M:%S") + f".{fraction}Z"


def canonical_decimal(value: Decimal) -> str:
    """Render an exact finite Decimal without exponent or insignificant zeros."""

    if not value.is_finite():
        raise ValueError("Decimal values must be finite")
    normalized = value.normalize()
    result = format(normalized, "f")
    if result in {"-0", ""}:
        return "0"
    return result


def canonicalize_json(value: object) -> object:
    """Convert supported values to deterministic JSON primitives recursively."""

    if isinstance(value, datetime):
        return canonical_datetime(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return canonical_decimal(value)
    if isinstance(value, Mapping):
        return {str(key): canonicalize_json(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [canonicalize_json(item) for item in value]
    if value is None or isinstance(value, str | int | bool):
        return value
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        canonicalize_json(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_jsonl_bytes(rows: Iterable[Mapping[str, Any]]) -> bytes:
    """Return deterministically sorted compact JSON Lines with exactly one final LF."""

    ordered = sorted(
        rows,
        key=lambda row: (row["bar_open_ts"], row["bar_series_id"], row["revision_no"]),
    )
    return b"".join(canonical_json_bytes(row) + b"\n" for row in ordered)


__all__ = [
    "canonical_datetime",
    "canonical_decimal",
    "canonical_json_bytes",
    "canonical_jsonl_bytes",
    "canonicalize_json",
]
