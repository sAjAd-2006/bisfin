from __future__ import annotations

import json
from decimal import Decimal

import pytest

from bisfin.backtest.errors import BacktestManifestError
from bisfin.backtest.manifest import parse_backtest_manifest_bytes


def _payload(*, run_code: str = "reference-sma-001", fast: int = 2) -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "run_code": run_code,
            "snapshot_code": "frozen-raw-001",
            "universe_code": "REFERENCE_TEST",
            "base_currency_code": "IRR",
            "event_from": "2029-01-01T00:00:00Z",
            "event_to": "2029-02-01T00:00:00Z",
            "initial_capital": "1000000.00",
            "random_seed": 1,
            "strategy": {
                "kind": "SMA_CROSS_LONG_FLAT_V1",
                "parameters": {"fast_window": fast, "slow_window": 3, "target_quantity": "100"},
            },
            "execution_model": {"kind": "NEXT_BAR_CLOSE_AT_AVAILABILITY_V1"},
            "transaction_cost_model": {
                "commission_bps": "1.25",
                "slippage_bps": "2",
                "sell_tax_bps": "0.5",
            },
            "instruments": [
                {
                    "instrument_id": 42,
                    "signal_component_key": "signal",
                    "execution_component_key": "execution",
                    "valuation_component_key": "valuation",
                    "execution_lag_seconds": 0,
                }
            ],
        }
    ).encode()


def test_manifest_is_strict_and_uses_exact_decimal_values() -> None:
    document = parse_backtest_manifest_bytes(_payload())

    assert document.request.initial_capital == Decimal("1000000.00")
    assert document.request.transaction_cost_model.commission_bps == Decimal("1.25")
    assert len(document.run_spec_sha256) == len(document.parameter_sha256) == 64


def test_semantic_hash_excludes_run_code_but_parameter_hash_changes_with_parameters() -> None:
    first = parse_backtest_manifest_bytes(_payload(run_code="reference-a"))
    equivalent = parse_backtest_manifest_bytes(_payload(run_code="reference-b"))
    changed = parse_backtest_manifest_bytes(_payload(run_code="reference-c", fast=1))

    assert first.run_spec_sha256 == equivalent.run_spec_sha256
    assert first.parameter_sha256 == equivalent.parameter_sha256
    assert first.run_spec_sha256 != changed.run_spec_sha256
    assert first.parameter_sha256 != changed.parameter_sha256


@pytest.mark.parametrize("run_code", ["../unsafe", ".hidden", "has space", "run/child"])
def test_manifest_rejects_unsafe_run_codes(run_code: str) -> None:
    with pytest.raises(BacktestManifestError, match="safe"):
        parse_backtest_manifest_bytes(_payload(run_code=run_code))
