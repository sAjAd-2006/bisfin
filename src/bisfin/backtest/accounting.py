"""Exact Decimal accounting for the deliberately narrow reference engine."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from bisfin.backtest.contracts import TransactionCostModelSpec
from bisfin.backtest.errors import BacktestValidationError

_BPS_DIVISOR = Decimal("10000")
type OrderSide = Literal["BUY", "SELL"]


@dataclass(frozen=True, slots=True)
class PricedFill:
    side: OrderSide
    quantity: Decimal
    reference_price: Decimal
    execution_price: Decimal
    notional: Decimal
    commission_amount: Decimal
    slippage_amount: Decimal
    tax_amount: Decimal

    @property
    def cash_delta(self) -> Decimal:
        if self.side == "BUY":
            return -(self.notional + self.commission_amount + self.tax_amount)
        return self.notional - self.commission_amount - self.tax_amount


@dataclass(frozen=True, slots=True)
class PositionState:
    quantity: Decimal = Decimal("0")
    average_cost: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")


def price_fill(
    *,
    side: OrderSide,
    quantity: Decimal,
    reference_price: Decimal,
    costs: TransactionCostModelSpec,
) -> PricedFill:
    """Apply deterministic bps costs once, with slippage embedded in execution price."""

    if quantity <= 0 or reference_price <= 0:
        raise BacktestValidationError("Reference fills require positive quantity and price.")
    direction = Decimal("1") if side == "BUY" else Decimal("-1")
    execution_price = reference_price * (
        Decimal("1") + direction * costs.slippage_bps / _BPS_DIVISOR
    )
    notional = execution_price * quantity
    commission = notional * costs.commission_bps / _BPS_DIVISOR
    tax = notional * costs.sell_tax_bps / _BPS_DIVISOR if side == "SELL" else Decimal("0")
    slippage = abs(execution_price - reference_price) * quantity
    return PricedFill(
        side=side,
        quantity=quantity,
        reference_price=reference_price,
        execution_price=execution_price,
        notional=notional,
        commission_amount=commission,
        slippage_amount=slippage,
        tax_amount=tax,
    )


class LongOnlyPortfolio:
    """In-memory portfolio state for a single base currency and no leverage."""

    def __init__(self, *, cash: Decimal) -> None:
        if cash < 0:
            raise BacktestValidationError("Initial cash must not be negative.")
        self.cash = cash
        self._positions: dict[int, PositionState] = {}

    def position(self, instrument_id: int) -> PositionState:
        return self._positions.get(instrument_id, PositionState())

    def apply_fill(self, *, instrument_id: int, fill: PricedFill) -> PositionState:
        state = self.position(instrument_id)
        if fill.side == "BUY":
            required_cash = -fill.cash_delta
            if required_cash > self.cash:
                raise BacktestValidationError("Insufficient cash for deterministic reference buy.")
            total_cost = state.average_cost * state.quantity + required_cash
            quantity = state.quantity + fill.quantity
            next_state = PositionState(
                quantity=quantity,
                average_cost=total_cost / quantity,
                realized_pnl=state.realized_pnl,
            )
        else:
            if fill.quantity > state.quantity:
                raise BacktestValidationError("Long-only reference sell exceeds current position.")
            realized_delta = (
                (fill.execution_price - state.average_cost) * fill.quantity
                - fill.commission_amount
                - fill.tax_amount
            )
            quantity = state.quantity - fill.quantity
            next_state = PositionState(
                quantity=quantity,
                average_cost=state.average_cost if quantity else Decimal("0"),
                realized_pnl=state.realized_pnl + realized_delta,
            )
        self.cash += fill.cash_delta
        self._positions[instrument_id] = next_state
        return next_state


__all__ = ["LongOnlyPortfolio", "OrderSide", "PositionState", "PricedFill", "price_fill"]
