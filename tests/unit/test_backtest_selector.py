from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bisfin.backtest.contracts import DecisionBar
from bisfin.backtest.selector import ArtifactBarSelector


def _bar(
    *,
    open_offset: int,
    revision: int,
    effective_offset: int,
    close: str,
) -> DecisionBar:
    origin = datetime(2030, 1, 1, tzinfo=UTC)
    return DecisionBar(
        bar_open_ts=origin + timedelta(days=open_offset),
        bar_series_id=7,
        revision_no=revision,
        available_at=origin + timedelta(days=effective_offset),
        system_available_at=origin + timedelta(days=effective_offset),
        effective_available_at=origin + timedelta(days=effective_offset),
        close_price=Decimal(close),
    )


def test_selector_keeps_one_schedule_item_per_logical_bar_and_applies_corrections_forward() -> None:
    selector = ArtifactBarSelector(
        [
            _bar(open_offset=0, revision=1, effective_offset=1, close="10"),
            _bar(open_offset=0, revision=2, effective_offset=3, close="12"),
            _bar(open_offset=1, revision=1, effective_offset=2, close="11"),
        ]
    )
    origin = datetime(2030, 1, 1, tzinfo=UTC)

    assert selector.decision_schedule() == (
        (origin + timedelta(days=1), origin),
        (origin + timedelta(days=2), origin + timedelta(days=1)),
    )
    assert selector.visible_bars(origin + timedelta(days=2)) == (
        _bar(open_offset=0, revision=1, effective_offset=1, close="10"),
        _bar(open_offset=1, revision=1, effective_offset=2, close="11"),
    )
    assert selector.visible_bars(origin + timedelta(days=3))[0] == _bar(
        open_offset=0, revision=2, effective_offset=3, close="12"
    )


def test_selector_execution_bar_is_strictly_after_signal_bar_and_available_after_submission() -> (
    None
):
    selector = ArtifactBarSelector(
        [
            _bar(open_offset=0, revision=1, effective_offset=1, close="10"),
            _bar(open_offset=1, revision=1, effective_offset=2, close="11"),
            _bar(open_offset=2, revision=1, effective_offset=4, close="12"),
        ]
    )
    origin = datetime(2030, 1, 1, tzinfo=UTC)

    assert selector.next_execution_bar(
        signal_bar_open_ts=origin,
        submitted_at=origin + timedelta(days=1),
        lag=timedelta(days=2),
    ) == _bar(open_offset=2, revision=1, effective_offset=4, close="12")
