"""Catalog DTOs reflecting canonical and time-versioned instrument semantics."""

from decimal import Decimal

from pydantic import Field

from bisfin.domain.common import AwareDateTime, ImmutableDTO, JsonObject


class Provider(ImmutableDTO):
    provider_id: int
    provider_code: str
    display_name: str
    provider_kind: str
    base_url: str | None = None
    default_timezone: str | None = None
    metadata: JsonObject = Field(default_factory=dict)
    created_at: AwareDateTime


class Instrument(ImmutableDTO):
    instrument_id: int
    asset_type_code: str
    venue_id: int | None = None
    quote_currency_code: str
    canonical_symbol: str
    display_name: str
    status: str
    active_from: AwareDateTime | None = None
    active_to: AwareDateTime | None = None
    metadata: JsonObject = Field(default_factory=dict)
    created_at: AwareDateTime


class InstrumentIdentifier(ImmutableDTO):
    """A provider identifier valid on a half-open interval.

    ``valid_from=None`` is the application representation of PostgreSQL
    ``-infinity`` because psycopg intentionally cannot decode non-finite
    timestamps into a standard-library ``datetime``.
    """

    provider_id: int
    identifier_type: str
    identifier_value: str
    valid_from: AwareDateTime | None
    valid_to: AwareDateTime | None = None
    instrument_id: int
    is_primary: bool = False
    metadata: JsonObject = Field(default_factory=dict)


class InstrumentSpecification(ImmutableDTO):
    instrument_id: int
    effective_from: AwareDateTime
    effective_to: AwareDateTime | None = None
    price_tick: Decimal | None = None
    quantity_step: Decimal | None = None
    lot_size: Decimal | None = None
    contract_multiplier: Decimal
    price_scale: int | None = None
    quantity_scale: int | None = None
    lower_price_limit: Decimal | None = None
    upper_price_limit: Decimal | None = None
    shares_outstanding: Decimal | None = None
    metadata: JsonObject = Field(default_factory=dict)


class ResolvedInstrument(ImmutableDTO):
    """An instrument paired with the exact historical identifier that resolved it."""

    instrument: Instrument
    identifier: InstrumentIdentifier
