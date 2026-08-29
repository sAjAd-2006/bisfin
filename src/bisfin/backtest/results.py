"""Canonical semantic result journal hashing for reference backtest replay."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

from bisfin.backtest.contracts import DecisionBar
from bisfin.backtest.engine import SimulationResult
from bisfin.snapshots.serialization import canonical_json_bytes


def result_sha256(result: SimulationResult, *, summary: Mapping[str, object]) -> str:
    """Hash market/accounting semantics only, excluding all storage surrogate IDs."""

    payload = {
        "decisions": [
            {
                "sequence": item.sequence,
                "instrument_id": item.instrument_id,
                "decision_ts": item.decision_ts,
                "trigger_bar": _bar_identity(item.trigger_bar),
                "inputs": [_bar_input(bar) for bar in item.inputs],
                "strategy_state_sha256": item.state_sha256,
                "input_manifest_sha256": item.input_manifest_sha256,
            }
            for item in result.decisions
        ],
        "signals": [
            {
                "decision_sequence": item.decision_sequence,
                "instrument_id": item.instrument_id,
                "signal_ts": item.signal_ts,
                "target_quantity": item.target_quantity,
                "reason_code": item.reason_code,
                "trigger_bar": _bar_identity(item.trigger_bar),
            }
            for item in result.signals
        ],
        "orders": [
            {
                "semantic_key": item.semantic_key,
                "decision_sequence": item.signal.decision_sequence,
                "instrument_id": item.signal.instrument_id,
                "side": item.side,
                "quantity": item.quantity,
                "submitted_at": item.submitted_at,
                "execution_bar": _bar_identity(item.execution_bar)
                if item.execution_bar is not None
                else None,
                "rejected_reason": item.rejected_reason,
            }
            for item in result.orders
        ],
        "fills": [
            {
                "order_key": item.order.semantic_key,
                "fill_ts": item.fill_ts,
                "reference_bar": _bar_identity(item.reference_bar),
                "reference_price": item.reference_price,
                "execution_price": item.economics.execution_price,
                "quantity": item.economics.quantity,
                "commission_amount": item.economics.commission_amount,
                "slippage_amount": item.economics.slippage_amount,
                "tax_amount": item.economics.tax_amount,
                "cash_delta": item.economics.cash_delta,
            }
            for item in result.fills
        ],
        "cash": result.cash,
        "positions": {
            str(instrument_id): {
                "quantity": state.quantity,
                "average_cost": state.average_cost,
                "realized_pnl": state.realized_pnl,
            }
            for instrument_id, state in sorted(result.positions.items())
        },
        "valuations": [
            {
                "instrument_id": item.instrument_id,
                "snapshot_ts": item.snapshot_ts,
                "reference_bar": _bar_identity(item.reference_bar),
                "quantity": item.quantity,
                "average_cost": item.average_cost,
                "realized_pnl": item.realized_pnl,
                "market_value": item.market_value,
                "unrealized_pnl": item.unrealized_pnl,
            }
            for item in result.valuations
        ],
        "equity_base": result.equity_base,
        "summary": dict(summary),
    }
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _bar_identity(bar: DecisionBar) -> dict[str, object]:
    return {
        "bar_open_ts": bar.bar_open_ts,
        "bar_series_id": bar.bar_series_id,
        "revision_no": bar.revision_no,
        "available_at": bar.available_at,
        "effective_available_at": bar.effective_available_at,
    }


def _bar_input(bar: DecisionBar) -> dict[str, object]:
    return {**_bar_identity(bar), "close_price": bar.close_price}


__all__ = ["result_sha256"]
