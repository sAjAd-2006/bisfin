"""Deterministic in-memory reference backtest simulation over frozen artifact selectors."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from bisfin.backtest.accounting import (
    LongOnlyPortfolio,
    OrderSide,
    PositionState,
    PricedFill,
    price_fill,
)
from bisfin.backtest.contracts import BacktestRunRequest, DecisionBar, DecisionView
from bisfin.backtest.errors import BacktestValidationError
from bisfin.backtest.selector import ArtifactBarSelector
from bisfin.backtest.strategies import SmaCrossLongFlatStrategy
from bisfin.snapshots.serialization import canonical_json_bytes


@dataclass(frozen=True, slots=True)
class SimulatedDecision:
    sequence: int
    instrument_id: int
    decision_ts: datetime
    trigger_bar: DecisionBar
    inputs: tuple[DecisionBar, ...]
    state_sha256: str
    input_manifest_sha256: str


@dataclass(frozen=True, slots=True)
class SimulatedSignal:
    decision_sequence: int
    instrument_id: int
    signal_ts: datetime
    target_quantity: Decimal
    reason_code: str
    trigger_bar: DecisionBar


@dataclass(frozen=True, slots=True)
class SimulatedOrder:
    semantic_key: str
    signal: SimulatedSignal
    side: OrderSide
    quantity: Decimal
    submitted_at: datetime
    execution_bar: DecisionBar | None
    rejected_reason: str | None = None


@dataclass(frozen=True, slots=True)
class SimulatedFill:
    order: SimulatedOrder
    fill_ts: datetime
    reference_bar: DecisionBar
    reference_price: Decimal
    economics: PricedFill


@dataclass(frozen=True, slots=True)
class SimulatedValuation:
    instrument_id: int
    snapshot_ts: datetime
    reference_bar: DecisionBar
    quantity: Decimal
    average_cost: Decimal
    realized_pnl: Decimal
    market_value: Decimal
    unrealized_pnl: Decimal


@dataclass(frozen=True, slots=True)
class SimulationResult:
    decisions: tuple[SimulatedDecision, ...]
    signals: tuple[SimulatedSignal, ...]
    orders: tuple[SimulatedOrder, ...]
    fills: tuple[SimulatedFill, ...]
    cash: Decimal
    positions: Mapping[int, PositionState]
    valuations: tuple[SimulatedValuation, ...] = ()
    equity_base: Decimal = Decimal("0")


class ReferenceBacktestSimulator:
    """Pure deterministic scheduler; all callers supply artifact-backed selectors."""

    def __init__(self) -> None:
        self._strategy = SmaCrossLongFlatStrategy()

    def simulate(
        self,
        request: BacktestRunRequest,
        *,
        signal_selectors: Mapping[int, ArtifactBarSelector],
        execution_selectors: Mapping[int, ArtifactBarSelector],
        valuation_selectors: Mapping[int, ArtifactBarSelector] | None = None,
        run_spec_sha256: str | None = None,
    ) -> SimulationResult:
        self._validate_bindings(request, signal_selectors, execution_selectors)
        valuation_data = valuation_selectors or execution_selectors
        if set(valuation_data) != {item.instrument_id for item in request.instruments}:
            raise BacktestValidationError("Every explicit run instrument requires valuation data.")
        portfolio = LongOnlyPortfolio(cash=request.initial_capital)
        pending: dict[datetime, list[SimulatedOrder]] = {}
        decisions: list[SimulatedDecision] = []
        signals: list[SimulatedSignal] = []
        orders: list[SimulatedOrder] = []
        fills: list[SimulatedFill] = []
        decision_events = self._decision_events(request, signal_selectors)
        all_times = sorted({item[0] for item in decision_events})
        index = 0
        while index < len(all_times):
            event_ts = all_times[index]
            for order in sorted(pending.pop(event_ts, []), key=lambda item: item.semantic_key):
                if order.execution_bar is None:
                    continue
                priced = price_fill(
                    side=order.side,
                    quantity=order.quantity,
                    reference_price=order.execution_bar.close_price,
                    costs=request.transaction_cost_model,
                )
                try:
                    portfolio.apply_fill(instrument_id=order.signal.instrument_id, fill=priced)
                except BacktestValidationError:
                    continue
                fills.append(
                    SimulatedFill(
                        order=order,
                        fill_ts=order.execution_bar.effective_available_at,
                        reference_bar=order.execution_bar,
                        reference_price=order.execution_bar.close_price,
                        economics=priced,
                    )
                )
            for _, instrument_id, trigger_bar in (
                item for item in decision_events if item[0] == event_ts
            ):
                selector = signal_selectors[instrument_id]
                visible = selector.visible_bars(event_ts)
                slow_window = request.strategy.parameters["slow_window"]
                assert type(slow_window) is int
                inputs = visible[-slow_window:]
                state = portfolio.position(instrument_id)
                decision = SimulatedDecision(
                    sequence=len(decisions) + 1,
                    instrument_id=instrument_id,
                    decision_ts=event_ts,
                    trigger_bar=trigger_bar,
                    inputs=inputs,
                    state_sha256=_state_hash(request, instrument_id, portfolio.cash, state),
                    input_manifest_sha256=_input_hash(inputs),
                )
                decisions.append(decision)
                intent = self._strategy.decide(
                    DecisionView(
                        instrument_id=instrument_id,
                        decision_ts=event_ts,
                        visible_bars=inputs,
                        current_quantity=state.quantity,
                        average_cost=state.average_cost,
                        realized_pnl=state.realized_pnl,
                        cash=portfolio.cash,
                        parameters=request.strategy,
                    )
                )
                if intent.target_quantity == state.quantity:
                    continue
                signal = SimulatedSignal(
                    decision_sequence=decision.sequence,
                    instrument_id=instrument_id,
                    signal_ts=event_ts,
                    target_quantity=intent.target_quantity,
                    reason_code=intent.reason_code,
                    trigger_bar=trigger_bar,
                )
                signals.append(signal)
                quantity = abs(intent.target_quantity - state.quantity)
                side: OrderSide = "BUY" if intent.target_quantity > state.quantity else "SELL"
                instrument = next(
                    item for item in request.instruments if item.instrument_id == instrument_id
                )
                execution_bar = execution_selectors[instrument_id].next_execution_bar(
                    signal_bar_open_ts=trigger_bar.bar_open_ts,
                    submitted_at=event_ts,
                    lag=timedelta(seconds=instrument.execution_lag_seconds),
                )
                semantic_key = "|".join(
                    (
                        run_spec_sha256 or _run_semantic_identity(request),
                        str(decision.sequence),
                        str(instrument_id),
                        str(intent.target_quantity),
                    )
                )
                order = SimulatedOrder(
                    semantic_key=semantic_key,
                    signal=signal,
                    side=side,
                    quantity=quantity,
                    submitted_at=event_ts,
                    execution_bar=execution_bar,
                    rejected_reason=None
                    if execution_bar is not None
                    else "NO_ELIGIBLE_EXECUTION_BAR",
                )
                orders.append(order)
                if execution_bar is not None:
                    pending.setdefault(execution_bar.effective_available_at, []).append(order)
                    if execution_bar.effective_available_at not in all_times:
                        all_times.append(execution_bar.effective_available_at)
                        all_times.sort()
            index += 1
        valuations = self._final_valuations(request, portfolio, valuation_data)
        return SimulationResult(
            decisions=tuple(decisions),
            signals=tuple(signals),
            orders=tuple(orders),
            fills=tuple(fills),
            cash=portfolio.cash,
            positions={
                item.instrument_id: portfolio.position(item.instrument_id)
                for item in request.instruments
            },
            valuations=valuations,
            equity_base=portfolio.cash
            + sum((item.market_value for item in valuations), Decimal("0")),
        )

    @staticmethod
    def _validate_bindings(
        request: BacktestRunRequest,
        signal_selectors: Mapping[int, ArtifactBarSelector],
        execution_selectors: Mapping[int, ArtifactBarSelector],
    ) -> None:
        expected = {item.instrument_id for item in request.instruments}
        if set(signal_selectors) != expected or set(execution_selectors) != expected:
            raise BacktestValidationError(
                "Every explicit run instrument requires signal and execution data."
            )

    @staticmethod
    def _decision_events(
        request: BacktestRunRequest,
        selectors: Mapping[int, ArtifactBarSelector],
    ) -> list[tuple[datetime, int, DecisionBar]]:
        events: list[tuple[datetime, int, DecisionBar]] = []
        for instrument in request.instruments:
            selector = selectors[instrument.instrument_id]
            for decision_ts, bar_open_ts in selector.decision_schedule():
                if request.event_from <= decision_ts < request.event_to:
                    triggers = {bar.bar_open_ts: bar for bar in selector.visible_bars(decision_ts)}
                    events.append((decision_ts, instrument.instrument_id, triggers[bar_open_ts]))
        return sorted(
            events, key=lambda item: (item[0], item[1], item[2].bar_series_id, item[2].bar_open_ts)
        )

    @staticmethod
    def _final_valuations(
        request: BacktestRunRequest,
        portfolio: LongOnlyPortfolio,
        selectors: Mapping[int, ArtifactBarSelector],
    ) -> tuple[SimulatedValuation, ...]:
        valuations: list[SimulatedValuation] = []
        for instrument in request.instruments:
            state = portfolio.position(instrument.instrument_id)
            visible = selectors[instrument.instrument_id].visible_bars(request.event_to)
            if not visible:
                raise BacktestValidationError(
                    "Reference valuation component has no bar visible at event_to."
                )
            reference = visible[-1]
            market_value = state.quantity * reference.close_price
            unrealized = (reference.close_price - state.average_cost) * state.quantity
            valuations.append(
                SimulatedValuation(
                    instrument_id=instrument.instrument_id,
                    snapshot_ts=request.event_to,
                    reference_bar=reference,
                    quantity=state.quantity,
                    average_cost=state.average_cost,
                    realized_pnl=state.realized_pnl,
                    market_value=market_value,
                    unrealized_pnl=unrealized,
                )
            )
        return tuple(valuations)


def _state_hash(
    request: BacktestRunRequest,
    instrument_id: int,
    cash: Decimal,
    state: PositionState,
) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "instrument_id": instrument_id,
                "cash": cash,
                "current_quantity": state.quantity,
                "average_cost": state.average_cost,
                "realized_pnl": state.realized_pnl,
                "strategy_parameters": request.strategy.parameters,
            }
        )
    ).hexdigest()


def _run_semantic_identity(request: BacktestRunRequest) -> str:
    """Stable fallback for direct simulator callers that have no manifest document."""

    payload = request.model_dump(mode="json")
    payload.pop("run_code", None)
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _input_hash(inputs: tuple[DecisionBar, ...]) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            [
                {
                    "input_role": "SIGNAL",
                    "bar_open_ts": item.bar_open_ts,
                    "bar_series_id": item.bar_series_id,
                    "revision_no": item.revision_no,
                    "available_at": item.available_at,
                    "effective_available_at": item.effective_available_at,
                    "close_price": item.close_price,
                }
                for item in inputs
            ]
        )
    ).hexdigest()


__all__ = [
    "ReferenceBacktestSimulator",
    "SimulatedDecision",
    "SimulatedFill",
    "SimulatedOrder",
    "SimulatedSignal",
    "SimulatedValuation",
    "SimulationResult",
]
