"""Fail-closed JSON catalog-manifest parsing independent of persistence."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Self

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
_PERSIAN_LETTERS = str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک"})
_WHITESPACE = re.compile(r"\s+")
_ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
type JsonObject = dict[str, object]
type DecimalString = Decimal | None


class CatalogManifestErrorCode(StrEnum):
    INVALID_UTF8 = "INVALID_UTF8"
    MALFORMED_JSON = "MALFORMED_JSON"
    DUPLICATE_JSON_FIELD = "DUPLICATE_JSON_FIELD"
    UNSUPPORTED_SCHEMA_VERSION = "UNSUPPORTED_SCHEMA_VERSION"
    INVALID_MANIFEST = "INVALID_MANIFEST"


@dataclass(frozen=True, slots=True)
class CatalogDiagnostic:
    path: str
    message: str


class CatalogManifestError(ValueError):
    """A bounded, machine-readable catalog document failure."""

    def __init__(
        self,
        code: CatalogManifestErrorCode,
        message: str,
        diagnostics: Iterable[CatalogDiagnostic] = (),
    ) -> None:
        self.code = code
        self.diagnostics = tuple(diagnostics)
        super().__init__(message)


class _DuplicateJsonField(ValueError):
    pass


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def normalize_catalog_text(value: str) -> str:
    """Normalize identifier comparison text without changing its semantic type."""

    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.translate(_PERSIAN_LETTERS).translate(_DIGITS)
    return _WHITESPACE.sub(" ", normalized).strip()


def normalize_isin(value: object) -> str:
    """Apply only a conservative structural ISIN check; no checksum is claimed."""

    if not isinstance(value, str):
        raise ValueError("ISIN must be a non-empty string")
    normalized = normalize_catalog_text(value).upper()
    if not normalized or _ISIN.fullmatch(normalized) is None:
        raise ValueError("ISIN must match the conservative ISO-6166-like structure")
    return normalized


def _bounded_text(value: str, *, field: str, maximum: int = 2_048) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if not value.strip() or len(value) > maximum or "\r" in value or "\n" in value:
        raise ValueError(f"{field} must be a non-empty bounded single-line string")
    return value


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value


def _decimal_string(value: object) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Decimal string values are required")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as error:
        raise ValueError("Decimal string value is invalid") from error
    if not parsed.is_finite():
        raise ValueError("Decimal string value must be finite")
    return parsed


class ProviderDefinition(_StrictModel):
    provider_code: str
    display_name: str
    provider_kind: str
    base_url: str | None = None
    default_timezone: str | None = None
    metadata: JsonObject = Field(default_factory=dict)

    _codes = field_validator("provider_code", "provider_kind", "display_name")(
        lambda cls, value, info: _bounded_text(value, field=info.field_name, maximum=64)
    )


class FeedDefinition(_StrictModel):
    provider_code: str
    feed_code: str
    display_name: str
    data_kind: str
    native_timezone: str | None = None
    parser_version: str | None = None
    active_from: datetime | None = None
    active_to: datetime | None = None
    metadata: JsonObject = Field(default_factory=dict)

    _strings = field_validator("provider_code", "feed_code", "display_name", "data_kind")(
        lambda cls, value, info: _bounded_text(value, field=info.field_name, maximum=96)
    )
    _timestamps = field_validator("active_from", "active_to")(
        lambda cls, value: None if value is None else _aware(value)
    )

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if (
            self.active_from is not None
            and self.active_to is not None
            and self.active_to <= self.active_from
        ):
            raise ValueError("active_to must be after active_from")
        return self


class CurrencyDefinition(_StrictModel):
    currency_code: str
    display_name: str
    minor_unit: int
    is_fiat: bool
    metadata: JsonObject = Field(default_factory=dict)


class AssetTypeDefinition(_StrictModel):
    asset_type_code: str
    display_name: str
    description: str | None = None


class VenueDefinition(_StrictModel):
    venue_code: str
    display_name: str
    mic_code: str | None = None
    country_code: str | None = None
    timezone_name: str
    base_currency_code: str | None = None
    metadata: JsonObject = Field(default_factory=dict)


class TimeframeDefinition(_StrictModel):
    timeframe_code: str
    display_name: str
    duration_seconds: int | None = None
    calendar_unit: str
    session_aligned: bool

    @model_validator(mode="after")
    def validate_fixed_duration(self) -> Self:
        if self.calendar_unit == "FIXED" and (
            self.duration_seconds is None or self.duration_seconds <= 0
        ):
            raise ValueError("FIXED timeframe requires positive duration_seconds")
        if (
            self.calendar_unit != "FIXED"
            and self.duration_seconds is not None
            and self.duration_seconds <= 0
        ):
            raise ValueError("duration_seconds must be positive when present")
        return self


class ProviderMarketMapping(_StrictModel):
    provider_code: str
    provider_market: str
    venue_code: str


class InstrumentDefinition(_StrictModel):
    stable_key: str
    provider_code: str
    provider_symbol: str
    isin: str
    name_fa: str
    name_en: str
    asset_type_code: str
    venue_code: str
    currency_code: str
    status: str
    active_from: datetime | None = None
    active_to: datetime | None = None
    identifier_valid_from: datetime
    spec_effective_from: datetime
    previous_symbol: str | None = None
    rename_effective_from: datetime | None = None
    price_tick: DecimalString
    quantity_step: DecimalString
    lot_size: DecimalString
    contract_multiplier: DecimalString
    price_scale: int | None = None
    quantity_scale: int | None = None
    lower_price_limit: DecimalString
    upper_price_limit: DecimalString
    shares_outstanding: DecimalString
    metadata: JsonObject = Field(default_factory=dict)

    _decimal_fields = field_validator(
        "price_tick",
        "quantity_step",
        "lot_size",
        "contract_multiplier",
        "lower_price_limit",
        "upper_price_limit",
        "shares_outstanding",
        mode="before",
    )(_decimal_string)
    _timestamps = field_validator(
        "active_from",
        "active_to",
        "identifier_valid_from",
        "spec_effective_from",
        "rename_effective_from",
    )(lambda cls, value: None if value is None else _aware(value))

    @field_validator("isin", mode="before")
    @classmethod
    def validate_isin(cls, value: object) -> str:
        return normalize_isin(value)

    @field_validator("provider_symbol", "previous_symbol", mode="before")
    @classmethod
    def normalize_symbols(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("symbol must be a string")
        normalized = normalize_catalog_text(value)
        if not normalized:
            raise ValueError("symbol must not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_temporal_and_specification_fields(self) -> Self:
        if (self.previous_symbol is None) != (self.rename_effective_from is None):
            raise ValueError("previous_symbol and rename_effective_from must be supplied together")
        if (
            self.rename_effective_from is not None
            and self.rename_effective_from <= self.identifier_valid_from
        ):
            raise ValueError("rename_effective_from must be after identifier_valid_from")
        if (
            self.active_from is not None
            and self.active_to is not None
            and self.active_to <= self.active_from
        ):
            raise ValueError("active_to must be after active_from")
        if self.contract_multiplier is None or self.contract_multiplier <= 0:
            raise ValueError("contract_multiplier must be a positive Decimal string")
        if (
            self.upper_price_limit is not None
            and self.lower_price_limit is not None
            and self.upper_price_limit < self.lower_price_limit
        ):
            raise ValueError("upper_price_limit must not be lower than lower_price_limit")
        return self


class CatalogManifestV1(_StrictModel):
    schema_version: Literal[1]
    manifest_id: str
    generated_at: datetime
    source: str
    providers: tuple[ProviderDefinition, ...]
    feeds: tuple[FeedDefinition, ...]
    currencies: tuple[CurrencyDefinition, ...]
    asset_types: tuple[AssetTypeDefinition, ...]
    venues: tuple[VenueDefinition, ...]
    timeframes: tuple[TimeframeDefinition, ...]
    provider_market_mappings: tuple[ProviderMarketMapping, ...]
    instruments: tuple[InstrumentDefinition, ...]

    @field_validator("generated_at")
    @classmethod
    def validate_generated_at(cls, value: datetime) -> datetime:
        return _aware(value)

    @model_validator(mode="after")
    def validate_references_and_duplicates(self) -> Self:
        if self.schema_version != 1:
            raise ValueError("unsupported schema_version")
        if not self.instruments:
            raise ValueError("instruments must not be empty")
        _require_unique(self.providers, lambda item: item.provider_code, "provider_code")
        _require_unique(self.currencies, lambda item: item.currency_code, "currency_code")
        _require_unique(self.asset_types, lambda item: item.asset_type_code, "asset_type_code")
        _require_unique(self.venues, lambda item: item.venue_code, "venue_code")
        _require_unique(self.timeframes, lambda item: item.timeframe_code, "timeframe_code")
        _require_unique(self.instruments, lambda item: item.stable_key, "instrument stable_key")
        _require_unique(
            self.feeds,
            lambda item: (item.provider_code, item.feed_code),
            "provider/feed code",
        )
        _require_unique(
            self.provider_market_mappings,
            lambda item: (item.provider_code, normalize_catalog_text(item.provider_market)),
            "provider-market mapping",
        )
        provider_codes = {item.provider_code for item in self.providers}
        currency_codes = {item.currency_code for item in self.currencies}
        asset_type_codes = {item.asset_type_code for item in self.asset_types}
        venue_codes = {item.venue_code for item in self.venues}
        for feed in self.feeds:
            if feed.provider_code not in provider_codes:
                raise ValueError(f"unknown provider_code {feed.provider_code!r} for feed")
        for venue in self.venues:
            if (
                venue.base_currency_code is not None
                and venue.base_currency_code not in currency_codes
            ):
                raise ValueError(f"unknown base_currency_code {venue.base_currency_code!r}")
        for mapping in self.provider_market_mappings:
            if mapping.provider_code not in provider_codes:
                raise ValueError(f"unknown provider_code {mapping.provider_code!r} for mapping")
            if mapping.venue_code not in venue_codes:
                raise ValueError(f"unknown venue_code {mapping.venue_code!r} for mapping")
        for instrument in self.instruments:
            if instrument.provider_code not in provider_codes:
                raise ValueError(f"unknown provider_code {instrument.provider_code!r}")
            if instrument.venue_code not in venue_codes:
                raise ValueError(f"unknown venue_code {instrument.venue_code!r}")
            if instrument.currency_code not in currency_codes:
                raise ValueError(f"unknown currency_code {instrument.currency_code!r}")
            if instrument.asset_type_code not in asset_type_codes:
                raise ValueError(f"unknown asset_type_code {instrument.asset_type_code!r}")
        return self

    def resolve_provider_market(self, provider_code: str, provider_market: str) -> str | None:
        normalized_market = normalize_catalog_text(provider_market)
        for mapping in self.provider_market_mappings:
            if (
                mapping.provider_code == provider_code
                and normalize_catalog_text(mapping.provider_market) == normalized_market
            ):
                return mapping.venue_code
        return None


def _require_unique(items: Iterable[Any], key: Any, label: str) -> None:
    seen: set[object] = set()
    for item in items:
        value = key(item)
        if value in seen:
            raise ValueError(f"duplicate {label}: {value!r}")
        seen.add(value)


@dataclass(frozen=True, slots=True)
class CatalogManifestDocument:
    payload_bytes: bytes
    payload_sha256: str
    raw_payload: Mapping[str, object]
    manifest: CatalogManifestV1


def _pairs_to_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonField(f"duplicate JSON object field: {key}")
        result[key] = value
    return result


def _forbid_non_finite(value: str) -> object:
    raise ValueError(f"non-finite JSON number is not permitted: {value}")


def _diagnostics(error: ValidationError) -> tuple[CatalogDiagnostic, ...]:
    return tuple(
        CatalogDiagnostic(
            path=".".join(str(part) for part in item["loc"]),
            message=str(item["msg"]),
        )
        for item in error.errors()
    )


def _ordered(manifest: CatalogManifestV1) -> CatalogManifestV1:
    return manifest.model_copy(
        update={
            "providers": tuple(sorted(manifest.providers, key=lambda item: item.provider_code)),
            "feeds": tuple(
                sorted(manifest.feeds, key=lambda item: (item.provider_code, item.feed_code))
            ),
            "currencies": tuple(sorted(manifest.currencies, key=lambda item: item.currency_code)),
            "asset_types": tuple(
                sorted(manifest.asset_types, key=lambda item: item.asset_type_code)
            ),
            "venues": tuple(sorted(manifest.venues, key=lambda item: item.venue_code)),
            "timeframes": tuple(sorted(manifest.timeframes, key=lambda item: item.timeframe_code)),
            "provider_market_mappings": tuple(
                sorted(
                    manifest.provider_market_mappings,
                    key=lambda item: (
                        item.provider_code,
                        normalize_catalog_text(item.provider_market),
                    ),
                )
            ),
            "instruments": tuple(sorted(manifest.instruments, key=lambda item: item.stable_key)),
        }
    )


def parse_catalog_manifest_bytes(payload_bytes: bytes) -> CatalogManifestDocument:
    """Parse exact UTF-8 JSON once, reject ambiguity, and retain its byte hash."""

    try:
        decoded = payload_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise CatalogManifestError(
            CatalogManifestErrorCode.INVALID_UTF8, "manifest is not UTF-8"
        ) from error
    try:
        raw = json.loads(
            decoded,
            parse_float=Decimal,
            parse_int=int,
            parse_constant=_forbid_non_finite,
            object_pairs_hook=_pairs_to_object,
        )
    except _DuplicateJsonField as error:
        raise CatalogManifestError(
            CatalogManifestErrorCode.DUPLICATE_JSON_FIELD, str(error)
        ) from error
    except (json.JSONDecodeError, ValueError) as error:
        raise CatalogManifestError(
            CatalogManifestErrorCode.MALFORMED_JSON, "manifest is malformed JSON"
        ) from error
    if not isinstance(raw, dict):
        raise CatalogManifestError(
            CatalogManifestErrorCode.INVALID_MANIFEST, "manifest root must be an object"
        )
    schema_version = raw.get("schema_version")
    if type(schema_version) is not int or schema_version != 1:
        raise CatalogManifestError(
            CatalogManifestErrorCode.UNSUPPORTED_SCHEMA_VERSION,
            "unsupported schema_version",
        )
    try:
        manifest = CatalogManifestV1.model_validate(raw)
    except ValidationError as error:
        diagnostics = _diagnostics(error)
        message = "; ".join(f"{item.path}: {item.message}" for item in diagnostics[:4])
        raise CatalogManifestError(
            CatalogManifestErrorCode.INVALID_MANIFEST,
            message or "catalog manifest is invalid",
            diagnostics,
        ) from error
    return CatalogManifestDocument(
        payload_bytes=payload_bytes,
        payload_sha256=hashlib.sha256(payload_bytes).hexdigest(),
        raw_payload=raw,
        manifest=_ordered(manifest),
    )


def load_catalog_manifest(path: str | Path) -> CatalogManifestDocument:
    try:
        payload_bytes = Path(path).read_bytes()
    except OSError as error:
        raise CatalogManifestError(
            CatalogManifestErrorCode.INVALID_MANIFEST, "manifest cannot be read"
        ) from error
    return parse_catalog_manifest_bytes(payload_bytes)


def catalog_manifest_json_schema() -> dict[str, Any]:
    return CatalogManifestV1.model_json_schema()


__all__ = [
    "AssetTypeDefinition",
    "CatalogDiagnostic",
    "CatalogManifestDocument",
    "CatalogManifestError",
    "CatalogManifestErrorCode",
    "CatalogManifestV1",
    "CurrencyDefinition",
    "FeedDefinition",
    "InstrumentDefinition",
    "ProviderDefinition",
    "ProviderMarketMapping",
    "TimeframeDefinition",
    "VenueDefinition",
    "catalog_manifest_json_schema",
    "load_catalog_manifest",
    "normalize_catalog_text",
    "normalize_isin",
    "parse_catalog_manifest_bytes",
]
