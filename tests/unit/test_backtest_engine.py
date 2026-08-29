from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bisfin.backtest.contracts import (
    BacktestRunRequest,
    DecisionBar,
    ReferenceExecutionModelKind,
    ReferenceExecutionModelSpec,
    ReferenceStrategyKind,
    ReferenceStrategySpec,
    RunInstrumentSpec,
    TransactionCostModelSpec,
)
from bisfin.backtest.engine import ReferenceBacktestSimulator
from bisfin.backtest.results import result_sha256
from bisfin.backtest.selector import ArtifactBarSelector


def _bar(number: int, close: str) -> DecisionBar:
    timestamp = datetime(2032, 1, 1, tzinfo=UTC) + timedelta(days=number)
    return DecisionBar(
        bar_open_ts=timestamp,
        bar_series_id=11,
        revision_no=1,
        available_at=timestamp,
        system_available_at=timestamp,
        effective_available_at=timestamp,
        close_price=Decimal(close),
    )


def _request() -> BacktestRunRequest:
    return BacktestRunRequest(
        schema_version=1,
        run_code="unit-reference",
        snapshot_code="fixture",
        universe_code="REFERENCE",
        base_currency_code="IRR",
        event_from=datetime(2032, 1, 1, tzinfo=UTC),
        event_to=datetime(2032, 2, 1, tzinfo=UTC),
        initial_capital=Decimal("1000"),
        random_seed=1,
        strategy=ReferenceStrategySpec(
            kind=ReferenceStrategyKind.SMA_CROSS_LONG_FLAT_V1,
            parameters={"fast_window": 2, "slow_window": 3, "target_quantity": Decimal("10")},
        ),
        execution_model=ReferenceExecutionModelSpec(
            kind=ReferenceExecutionModelKind.NEXT_BAR_CLOSE_AT_AVAILABILITY_V1
        ),
        transaction_cost_model=TransactionCostModelSpec(),
        instruments=(
            RunInstrumentSpec(
                instrument_id=1,
                signal_component_key="signal",
                execution_component_key="execution",
                valuation_component_key="valuation",
                execution_lag_seconds=0,
            ),
        ),
    )


def test_reference_simulator_emits_one_decision_per_bar_and_next_bar_full_fill() -> None:
    bars = [_bar(0, "1"), _bar(1, "2"), _bar(2, "4"), _bar(3, "5")]
    result = ReferenceBacktestSimulator().simulate(
        _request(),
        signal_selectors={1: ArtifactBarSelector(bars)},
        execution_selectors={1: ArtifactBarSelector(bars)},
    )

    assert len(result.decisions) == 4
    assert len(result.signals) == 1
    assert result.signals[0].target_quantity == Decimal("10")
    assert len(result.fills) == 1
    assert result.fills[0].fill_ts == _bar(3, "5").effective_available_at
    assert result.fills[0].reference_price == Decimal("5")
    assert result.cash == Decimal("950")
    assert result.positions[1].quantity == Decimal("10")
    assert result.valuations[0].market_value == Decimal("50")
    assert result.equity_base == Decimal("1000")


def test_reference_simulator_preserves_the_revision_visible_at_each_decision() -> None:
    first = _bar(0, "1")
    correction = first.model_copy(
        update={
            "revision_no": 2,
            "available_at": first.available_at + timedelta(days=2),
            "system_available_at": first.system_available_at + timedelta(days=2),
            "effective_available_at": first.effective_available_at + timedelta(days=2),
            "close_price": Decimal("9"),
        }
    )
    bars = [first, correction, _bar(1, "2"), _bar(2, "4"), _bar(3, "5")]

    result = ReferenceBacktestSimulator().simulate(
        _request(),
        signal_selectors={1: ArtifactBarSelector(bars)},
        execution_selectors={1: ArtifactBarSelector(bars)},
    )

    assert result.decisions[0].trigger_bar.revision_no == 1
    assert result.decisions[2].inputs[0].revision_no == 2


def test_equivalent_requests_with_different_run_codes_produce_the_same_hash() -> None:
    bars = [_bar(0, "1"), _bar(1, "2"), _bar(2, "4"), _bar(3, "5")]
    simulator = ReferenceBacktestSimulator()
    first = simulator.simulate(
        _request(),
        signal_selectors={1: ArtifactBarSelector(bars)},
        execution_selectors={1: ArtifactBarSelector(bars)},
    )
    second = simulator.simulate(
        _request().model_copy(update={"run_code": "other-reference-run"}),
        signal_selectors={1: ArtifactBarSelector(bars)},
        execution_selectors={1: ArtifactBarSelector(bars)},
    )

    assert result_sha256(first, summary={}) == result_sha256(second, summary={})
