from __future__ import annotations

from decimal import Decimal

from bisfin.backtest.accounting import PositionState
from bisfin.backtest.engine import SimulationResult
from bisfin.backtest.results import result_sha256


def test_result_hash_is_stable_and_excludes_storage_identity() -> None:
    result = SimulationResult(
        decisions=(),
        signals=(),
        orders=(),
        fills=(),
        cash=Decimal("100"),
        positions={1: PositionState()},
    )

    first = result_sha256(result, summary={"net_pnl_base": Decimal("0")})
    second = result_sha256(result, summary={"net_pnl_base": Decimal("0")})

    assert first == second
    assert len(first) == 64
