"""Point-in-time selection over the immutable snapshot artifact rows."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

from bisfin.backtest.contracts import DecisionBar
from bisfin.backtest.errors import BacktestValidationError


class ArtifactBarSelector:
    """Select exact frozen revisions without consulting live market tables."""

    def __init__(self, rows: Iterable[DecisionBar]) -> None:
        by_open: dict[datetime, list[DecisionBar]] = {}
        for row in rows:
            by_open.setdefault(row.bar_open_ts, []).append(row)
        if not by_open:
            raise BacktestValidationError("A reference component must contain at least one bar.")
        self._rows_by_open = {
            open_ts: tuple(
                sorted(revisions, key=lambda item: (item.effective_available_at, item.revision_no))
            )
            for open_ts, revisions in by_open.items()
        }

    def decision_schedule(self) -> tuple[tuple[datetime, datetime], ...]:
        """Return one first-visibility decision instant per logical bar."""

        return tuple(
            sorted(
                (
                    (revisions[0].effective_available_at, bar_open_ts)
                    for bar_open_ts, revisions in self._rows_by_open.items()
                ),
                key=lambda item: (item[0], item[1]),
            )
        )

    def visible_bars(self, decision_ts: datetime) -> tuple[DecisionBar, ...]:
        """Return the latest artifact revision visible for every logical bar at ``decision_ts``."""

        selected: list[DecisionBar] = []
        for bar_open_ts, revisions in self._rows_by_open.items():
            candidates = [row for row in revisions if row.effective_available_at <= decision_ts]
            if candidates:
                selected.append(max(candidates, key=lambda item: item.revision_no))
        return tuple(sorted(selected, key=lambda item: item.bar_open_ts))

    def next_execution_bar(
        self,
        *,
        signal_bar_open_ts: datetime,
        submitted_at: datetime,
        lag: timedelta,
    ) -> DecisionBar | None:
        """Find the first later logical bar whose reference becomes executable."""

        earliest = submitted_at + lag
        for bar_open_ts in sorted(self._rows_by_open):
            if bar_open_ts <= signal_bar_open_ts:
                continue
            revisions = self._rows_by_open[bar_open_ts]
            first_visibility = revisions[0].effective_available_at
            if first_visibility < earliest:
                continue
            visible = [row for row in revisions if row.effective_available_at <= first_visibility]
            return max(visible, key=lambda item: item.revision_no)
        return None


__all__ = ["ArtifactBarSelector"]
