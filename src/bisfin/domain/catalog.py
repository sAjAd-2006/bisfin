"""Catalog DTOs reflecting feeds, sessions, and time-versioned instruments."""

from datetime import date
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


class DataFeed(ImmutableDTO):
    feed_id: int
    provider_id: int
    feed_code: str
    display_name: str
    data_kind: str
    native_timezone: str | None = None
    parser_version: str | None = None
    active_from: AwareDateTime | None = None
    active_to: AwareDateTime | None = None
    metadata: JsonObject = Field(default_factory=dict)


class Timeframe(ImmutableDTO):
    timeframe_id: int
    timeframe_code: str
    display_name: str
    duration_seconds: int | None = None
    calendar_unit: str
    session_aligned: bool


class TradingSession(ImmutableDTO):
    venue_id: int
    trading_date: date
    session_code: str
    is_trading_day: bool
    session_open_ts: AwareDateTime | None = None
    session_close_ts: AwareDateTime | None = None
    settlement_date: date | None = None
    metadata: JsonObject = Field(default_factory=dict)


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


class SessionResolvedInstrument(ImmutableDTO):
    """A historical identifier resolved at one canonical session open."""

    instrument: Instrument
    identifier: InstrumentIdentifier
    trading_session: TradingSession
