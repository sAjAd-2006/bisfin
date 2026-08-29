"""PostgreSQL acceptance coverage for the frozen-artifact reference engine."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine
from tests.fixtures import unique_code
from tests.integration.snapshot_support import (
    component,
    insert_revision,
    manifest_bytes,
    seed_snapshot_series,
)

from bisfin.backtest.manifest import parse_backtest_manifest_bytes
from bisfin.backtest.service import ReferenceBacktestService
from bisfin.snapshots.builder import SnapshotBuilder
from bisfin.snapshots.manifest import parse_snapshot_manifest_bytes


def test_reference_run_persists_artifact_lineage_and_is_idempotent(
    db_engine: Engine,
    snapshot_artifact_dir: Path,
) -> None:
    artifact_dir = snapshot_artifact_dir
    series = seed_snapshot_series(db_engine)
    origin = datetime(2033, 1, 1, tzinfo=UTC)
    for day, close in enumerate(("10", "11", "13", "14", "15")):
        insert_revision(
            db_engine,
            series,
            bar_open_ts=origin + timedelta(days=day),
            revision_no=1,
            available_at=origin + timedelta(days=day + 1),
            close_price=int(close),
        )
    snapshot_code = unique_code("BT_SNAPSHOT")
    SnapshotBuilder(db_engine, clock=lambda: origin + timedelta(days=20)).build(
        parse_snapshot_manifest_bytes(
            manifest_bytes(
                snapshot_code=snapshot_code,
                cutoff=origin + timedelta(days=20),
                components=[
                    component(
                        "raw",
                        series,
                        event_from=origin,
                        event_to=origin + timedelta(days=6),
                    )
                ],
            )
        ),
        output_dir=artifact_dir,
    )
    universe_code = unique_code("BT_UNIVERSE")
    with db_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO catalog.universe (universe_code, display_name)
                VALUES (:universe_code, :display_name)
                """
            ),
            {"universe_code": universe_code, "display_name": universe_code},
        )
    document = parse_backtest_manifest_bytes(
        json.dumps(
            {
                "schema_version": 1,
                "run_code": unique_code("BT_RUN"),
                "snapshot_code": snapshot_code,
                "universe_code": universe_code,
                "base_currency_code": "IRR",
                "event_from": origin.isoformat().replace("+00:00", "Z"),
                "event_to": (origin + timedelta(days=10)).isoformat().replace("+00:00", "Z"),
                "initial_capital": "100000",
                "random_seed": 1,
                "strategy": {
                    "kind": "SMA_CROSS_LONG_FLAT_V1",
                    "parameters": {
                        "fast_window": 2,
                        "slow_window": 3,
                        "target_quantity": "100",
                    },
                },
                "execution_model": {"kind": "NEXT_BAR_CLOSE_AT_AVAILABILITY_V1"},
                "transaction_cost_model": {
                    "commission_bps": "0",
                    "slippage_bps": "0",
                    "sell_tax_bps": "0",
                },
                "instruments": [
                    {
                        "instrument_id": series.instrument_id,
                        "signal_component_key": "raw",
                        "execution_component_key": "raw",
                        "valuation_component_key": "raw",
                        "execution_lag_seconds": 0,
                    }
                ],
            }
        ).encode("utf-8")
    )
    service = ReferenceBacktestService(db_engine, clock=lambda: origin + timedelta(days=30))

    first = service.run(document)
    second = service.run(document)

    assert first.status == "SUCCEEDED"
    assert second.run_id == first.run_id
    assert second.result_sha256 == first.result_sha256
    with db_engine.connect() as connection:
        run = (
            connection.execute(
                text("SELECT status, metadata FROM backtest.run WHERE run_id = :run_id"),
                {"run_id": first.run_id},
            )
            .mappings()
            .one()
        )
        input_count = connection.execute(
            text(
                """
                SELECT count(*) FROM backtest.decision_bar_input input
                JOIN backtest.decision_context context
                  ON context.decision_context_id = input.decision_context_id
                WHERE context.run_id = :run_id
                """
            ),
            {"run_id": first.run_id},
        ).scalar_one()
        fill_count = connection.execute(
            text("SELECT count(*) FROM backtest.fill WHERE run_id = :run_id"),
            {"run_id": first.run_id},
        ).scalar_one()
        reference_count = connection.execute(
            text("SELECT count(*) FROM backtest.fill_market_reference WHERE run_id = :run_id"),
            {"run_id": first.run_id},
        ).scalar_one()

    assert run["status"] == "SUCCEEDED"
    assert run["metadata"]["result_sha256"] == first.result_sha256
    assert input_count > 0
    assert fill_count == 1
    assert reference_count == fill_count
