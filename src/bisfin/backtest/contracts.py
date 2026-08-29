"""Strict, persistence-agnostic contracts for reference backtest runs."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import ConfigDict, Field, field_validator, model_validator

from bisfin.domain.common import AwareDateTime, ImmutableDTO


class ReferenceStrategyKind(StrEnum):
    SMA_CROSS_LONG_FLAT_V1 = "SMA_CROSS_LONG_FLAT_V1"


class ReferenceExecutionModelKind(StrEnum):
    NEXT_BAR_CLOSE_AT_AVAILABILITY_V1 = "NEXT_BAR_CLOSE_AT_AVAILABILITY_V1"


class ReferenceStrategySpec(ImmutableDTO):
    kind: ReferenceStrategyKind
    parameters: dict[str, Decimal | int]

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_sma_parameters(self) -> ReferenceStrategySpec:
        if self.kind is not ReferenceStrategyKind.SMA_CROSS_LONG_FLAT_V1:
            raise ValueError("unsupported strategy kind")
        expected = {"fast_window", "slow_window", "target_quantity"}
        if set(self.parameters) != expected:
            raise ValueError(
                "SMA strategy parameters must be fast_window, slow_window, target_quantity"
            )
        fast = self.parameters["fast_window"]
        slow = self.parameters["slow_window"]
        quantity = self.parameters["target_quantity"]
        if type(fast) is not int or type(slow) is not int:
            raise ValueError("SMA windows must be integers")
        if fast < 1 or slow < 1 or fast >= slow:
            raise ValueError("SMA windows must satisfy 1 <= fast_window < slow_window")
        if not isinstance(quantity, Decimal) or quantity <= 0:
            raise ValueError("target_quantity must be a positive Decimal")
        return self


class ReferenceExecutionModelSpec(ImmutableDTO):
    kind: ReferenceExecutionModelKind

    model_config = ConfigDict(extra="forbid", frozen=True)


class TransactionCostModelSpec(ImmutableDTO):
    commission_bps: Decimal = Decimal("0")
    slippage_bps: Decimal = Decimal("0")
    sell_tax_bps: Decimal = Decimal("0")

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("commission_bps", "slippage_bps", "sell_tax_bps")
    @classmethod
    def validate_non_negative_bps(cls, value: Decimal) -> Decimal:
        if value < 0:
            raise ValueError("basis points must not be negative")
        return value


class RunInstrumentSpec(ImmutableDTO):
    instrument_id: int = Field(gt=0)
    signal_component_key: str = Field(min_length=1, max_length=160)
    execution_component_key: str = Field(min_length=1, max_length=160)
    valuation_component_key: str = Field(min_length=1, max_length=160)
    execution_lag_seconds: int = Field(ge=0)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("signal_component_key", "execution_component_key", "valuation_component_key")
    @classmethod
    def validate_component_key(cls, value: str) -> str:
        if value.strip() != value or "\x00" in value or "\r" in value or "\n" in value:
            raise ValueError("component key must be a bounded single-line value")
        return value


class BacktestRunRequest(ImmutableDTO):
    schema_version: int
    run_code: str = Field(min_length=1, max_length=128)
    snapshot_code: str = Field(min_length=1, max_length=128)
    universe_code: str = Field(min_length=1, max_length=160)
    base_currency_code: str = Field(min_length=1, max_length=12)
    event_from: AwareDateTime
    event_to: AwareDateTime
    initial_capital: Decimal
    random_seed: int
    strategy: ReferenceStrategySpec
    execution_model: ReferenceExecutionModelSpec
    transaction_cost_model: TransactionCostModelSpec
    instruments: tuple[RunInstrumentSpec, ...]

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("initial_capital")
    @classmethod
    def validate_initial_capital(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("initial_capital must be positive")
        return value

    @model_validator(mode="after")
    def validate_request(self) -> BacktestRunRequest:
        if self.schema_version != 1:
            raise ValueError("unsupported schema_version")
        if self.event_from >= self.event_to:
            raise ValueError("event_from must be before event_to")
        if not self.instruments:
            raise ValueError("instruments must not be empty")
        if len({item.instrument_id for item in self.instruments}) != len(self.instruments):
            raise ValueError("instrument_id values must be unique")
        return self


class TargetPositionIntent(ImmutableDTO):
    instrument_id: int
    decision_ts: AwareDateTime
    target_quantity: Decimal = Field(ge=0)
    reason_code: str


class DecisionBar(ImmutableDTO):
    bar_open_ts: AwareDateTime
    bar_series_id: int
    revision_no: int
    available_at: AwareDateTime
    system_available_at: AwareDateTime
    effective_available_at: AwareDateTime
    close_price: Decimal


class DecisionView(ImmutableDTO):
    instrument_id: int
    decision_ts: AwareDateTime
    visible_bars: tuple[DecisionBar, ...]
    current_quantity: Decimal
    average_cost: Decimal
    realized_pnl: Decimal
    cash: Decimal
    parameters: ReferenceStrategySpec


__all__ = [
    "BacktestRunRequest",
    "DecisionBar",
    "DecisionView",
    "ReferenceExecutionModelKind",
    "ReferenceExecutionModelSpec",
    "ReferenceStrategyKind",
    "ReferenceStrategySpec",
    "RunInstrumentSpec",
    "TargetPositionIntent",
    "TransactionCostModelSpec",
]
