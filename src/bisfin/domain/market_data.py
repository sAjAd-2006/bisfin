"""Decimal-safe market-bar DTOs and the exact database replay modes."""

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import Field

from bisfin.domain.common import AwareDateTime, ImmutableDTO, JsonObject


class ReplayMode(StrEnum):
    PUBLIC_REPLAY = "PUBLIC_REPLAY"
    ACTUAL_SYSTEM_REPLAY = "ACTUAL_SYSTEM_REPLAY"


class BarSeries(ImmutableDTO):
    bar_series_id: int
    feed_id: int
    instrument_id: int
    timeframe_id: int
    price_basis: str
    adjustment_set_id: int | None = None
    close_semantics: str
    session_code: str
    metadata: JsonObject = Field(default_factory=dict)
    created_at: AwareDateTime


class BarRevision(ImmutableDTO):
    bar_open_ts: AwareDateTime
    bar_series_id: int
    revision_no: int
    available_at: AwareDateTime
    system_available_at: AwareDateTime
    bar_close_ts: AwareDateTime
    trading_date: date
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    official_close_price: Decimal | None = None
    settlement_price: Decimal | None = None
    volume: Decimal | None = None
    quote_volume: Decimal | None = None
    trade_count: int | None = None
    vwap: Decimal | None = None
    open_interest: Decimal | None = None
    is_final: bool
    quality_flags: int
    ingestion_batch_id: int
    recorded_at: AwareDateTime
    previous_close_price: Decimal | None = None


class PointInTimeBar(BarRevision):
    effective_available_at: AwareDateTime
