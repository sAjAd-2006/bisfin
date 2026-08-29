from __future__ import annotations

from decimal import Decimal

import pytest

from bisfin.backtest.accounting import LongOnlyPortfolio, price_fill
from bisfin.backtest.contracts import TransactionCostModelSpec
from bisfin.backtest.errors import BacktestValidationError


def test_buy_and_sell_reconcile_cash_costs_and_realized_pnl() -> None:
    costs = TransactionCostModelSpec(
        commission_bps=Decimal("10"),
        slippage_bps=Decimal("20"),
        sell_tax_bps=Decimal("50"),
    )
    portfolio = LongOnlyPortfolio(cash=Decimal("10000"))
    buy = price_fill(
        side="BUY", quantity=Decimal("10"), reference_price=Decimal("100"), costs=costs
    )

    portfolio.apply_fill(instrument_id=1, fill=buy)
    sell = price_fill(
        side="SELL", quantity=Decimal("10"), reference_price=Decimal("120"), costs=costs
    )
    portfolio.apply_fill(instrument_id=1, fill=sell)

    assert buy.execution_price == Decimal("100.20")
    assert buy.commission_amount == Decimal("1.00200")
    assert sell.execution_price == Decimal("119.760")
    assert portfolio.cash == Decimal("10187.412400")
    assert portfolio.position(1).quantity == Decimal("0")
    assert portfolio.position(1).realized_pnl == Decimal("187.412400")


def test_long_only_portfolio_rejects_insufficient_cash_and_oversell() -> None:
    portfolio = LongOnlyPortfolio(cash=Decimal("1"))
    fill = price_fill(
        side="BUY",
        quantity=Decimal("1"),
        reference_price=Decimal("2"),
        costs=TransactionCostModelSpec(),
    )
    with pytest.raises(BacktestValidationError, match="Insufficient cash"):
        portfolio.apply_fill(instrument_id=1, fill=fill)

    with pytest.raises(BacktestValidationError, match="sell exceeds"):
        portfolio.apply_fill(
            instrument_id=1,
            fill=price_fill(
                side="SELL",
                quantity=Decimal("1"),
                reference_price=Decimal("2"),
                costs=TransactionCostModelSpec(),
            ),
        )
