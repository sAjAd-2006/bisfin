"""Pure deterministic reference strategies; they receive no database boundary."""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from bisfin.backtest.contracts import DecisionView, TargetPositionIntent


class ReferenceStrategy(Protocol):
    def decide(self, context: DecisionView) -> TargetPositionIntent: ...


class SmaCrossLongFlatStrategy:
    """Long/flat close-price SMA crossover used as the v1 correctness oracle."""

    def decide(self, context: DecisionView) -> TargetPositionIntent:
        parameters = context.parameters.parameters
        fast = parameters["fast_window"]
        slow = parameters["slow_window"]
        target = parameters["target_quantity"]
        assert type(fast) is int and type(slow) is int and isinstance(target, Decimal)
        bars = context.visible_bars
        if len(bars) < slow:
            quantity = Decimal("0")
            reason = "SMA_WARMUP"
        else:
            closes = [bar.close_price for bar in bars[-slow:]]
            fast_sma = sum(closes[-fast:]) / Decimal(fast)
            slow_sma = sum(closes) / Decimal(slow)
            quantity = target if fast_sma > slow_sma else Decimal("0")
            reason = "SMA_CROSS_LONG" if quantity else "SMA_CROSS_FLAT"
        return TargetPositionIntent(
            instrument_id=context.instrument_id,
            decision_ts=context.decision_ts,
            target_quantity=quantity,
            reason_code=reason,
        )


__all__ = ["ReferenceStrategy", "SmaCrossLongFlatStrategy"]
