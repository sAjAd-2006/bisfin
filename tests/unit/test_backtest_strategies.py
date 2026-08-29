from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bisfin.backtest.contracts import (
    DecisionBar,
    DecisionView,
    ReferenceStrategyKind,
    ReferenceStrategySpec,
)
from bisfin.backtest.strategies import SmaCrossLongFlatStrategy


def _bar(number: int, close: str) -> DecisionBar:
    timestamp = datetime(2029, 1, 1, tzinfo=UTC) + timedelta(days=number)
    return DecisionBar(
        bar_open_ts=timestamp,
        bar_series_id=1,
        revision_no=1,
        available_at=timestamp,
        system_available_at=timestamp,
        effective_available_at=timestamp,
        close_price=Decimal(close),
    )


def _view(closes: list[str]) -> DecisionView:
    decision_ts = datetime(2029, 2, 1, tzinfo=UTC)
    return DecisionView(
        instrument_id=1,
        decision_ts=decision_ts,
        visible_bars=tuple(_bar(index, close) for index, close in enumerate(closes)),
        current_quantity=Decimal("0"),
        average_cost=Decimal("0"),
        realized_pnl=Decimal("0"),
        cash=Decimal("1000000"),
        parameters=ReferenceStrategySpec(
            kind=ReferenceStrategyKind.SMA_CROSS_LONG_FLAT_V1,
            parameters={"fast_window": 2, "slow_window": 3, "target_quantity": Decimal("100")},
        ),
    )


def test_sma_strategy_is_pure_and_deterministic_across_warmup_long_and_flat() -> None:
    strategy = SmaCrossLongFlatStrategy()

    assert strategy.decide(_view(["1", "2"])).target_quantity == Decimal("0")
    long = strategy.decide(_view(["1", "2", "4"]))
    flat = strategy.decide(_view(["4", "2", "1"]))

    assert long.target_quantity == Decimal("100")
    assert long.reason_code == "SMA_CROSS_LONG"
    assert flat.target_quantity == Decimal("0")
    assert flat.reason_code == "SMA_CROSS_FLAT"
    assert strategy.decide(_view(["1", "2", "4"])) == long
