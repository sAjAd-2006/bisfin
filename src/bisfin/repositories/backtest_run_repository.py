"""Focused SQLAlchemy Core persistence for reference-backtest lifecycle records."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.engine import Connection

from bisfin.backtest.errors import (
    BacktestRunConflictError,
    BacktestRunInProgressError,
    StrategyVersionConflictError,
)
from bisfin.db.errors import translate_database_errors
from bisfin.domain.market_data import ReplayMode


class SqlAlchemyBacktestRunRepository:
    """Run/strategy lifecycle operations; the caller owns the transaction."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def get_by_code(self, run_code: str, *, for_update: bool = False) -> dict[str, object] | None:
        statement = "SELECT * FROM backtest.run WHERE run_code = :run_code"
        if for_update:
            statement += " FOR UPDATE"
        with translate_database_errors(operation="get backtest run by code"):
            row = (
                self._connection.execute(text(statement), {"run_code": run_code})
                .mappings()
                .one_or_none()
            )
        return None if row is None else dict(row)

    def ensure_strategy_version(self, *, class_path: str, code_sha256: str) -> int:
        strategy_code = "BISFIN_SMA_CROSS_LONG_FLAT"
        with translate_database_errors(operation="register reference strategy"):
            self._connection.execute(
                text(
                    """
                    INSERT INTO backtest.strategy (strategy_code, display_name, description)
                    VALUES (:strategy_code, :display_name, :description)
                    ON CONFLICT (strategy_code) DO NOTHING
                    """
                ),
                {
                    "strategy_code": strategy_code,
                    "display_name": "Bisfin SMA cross long/flat reference",
                    "description": "Deterministic artifact-backed reference strategy.",
                },
            )
            strategy_id = self._connection.execute(
                text(
                    "SELECT strategy_id FROM backtest.strategy WHERE strategy_code = :strategy_code"
                ),
                {"strategy_code": strategy_code},
            ).scalar_one()
            existing = (
                self._connection.execute(
                    text(
                        """
                    SELECT strategy_version_id, class_path, code_sha256
                    FROM backtest.strategy_version
                    WHERE strategy_id = :strategy_id AND version_no = 1
                    """
                    ),
                    {"strategy_id": strategy_id},
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                if existing["class_path"] != class_path or existing["code_sha256"] != code_sha256:
                    raise StrategyVersionConflictError(
                        "Reference strategy version 1 has a different immutable source hash."
                    )
                return int(existing["strategy_version_id"])
            return int(
                self._connection.execute(
                    text(
                        """
                        INSERT INTO backtest.strategy_version
                            (strategy_id, version_no, class_path, code_sha256, parameter_schema,
                             default_parameters)
                        VALUES
                            (:strategy_id, 1, :class_path, :code_sha256,
                             CAST(:parameter_schema AS jsonb), CAST(:default_parameters AS jsonb))
                        RETURNING strategy_version_id
                        """
                    ),
                    {
                        "strategy_id": strategy_id,
                        "class_path": class_path,
                        "code_sha256": code_sha256,
                        "parameter_schema": "{}",
                        "default_parameters": "{}",
                    },
                ).scalar_one()
            )

    def resolve_universe_id(self, universe_code: str) -> int:
        with translate_database_errors(operation="resolve backtest universe"):
            universe_id = self._connection.execute(
                text(
                    "SELECT universe_id FROM catalog.universe WHERE universe_code = :universe_code"
                ),
                {"universe_code": universe_code},
            ).scalar_one_or_none()
        if universe_id is None:
            raise ValueError("Backtest universe code was not found.")
        return int(universe_id)

    def validate_instrument_series(
        self,
        *,
        instrument_id: int,
        bar_series_ids: Sequence[int],
        base_currency_code: str,
    ) -> int:
        if not bar_series_ids:
            raise ValueError("Backtest instrument has no artifact bar-series binding.")
        with translate_database_errors(operation="validate backtest instrument and bar series"):
            quote_currency = self._connection.execute(
                text(
                    "SELECT quote_currency_code FROM catalog.instrument "
                    "WHERE instrument_id = :instrument_id"
                ),
                {"instrument_id": instrument_id},
            ).scalar_one_or_none()
            rows = (
                self._connection.execute(
                    text(
                        """
                    SELECT bar_series_id, timeframe_id, price_basis
                    FROM market.bar_series
                    WHERE instrument_id = :instrument_id
                      AND bar_series_id = ANY(CAST(:bar_series_ids AS bigint[]))
                    """
                    ),
                    {"instrument_id": instrument_id, "bar_series_ids": list(bar_series_ids)},
                )
                .mappings()
                .all()
            )
        if quote_currency != base_currency_code:
            raise ValueError(
                "Instrument quote currency must equal the reference run base currency."
            )
        if len(rows) != len(set(bar_series_ids)) or any(
            row["price_basis"] != "RAW" for row in rows
        ):
            raise ValueError(
                "All reference run series must exist for the instrument and use RAW prices."
            )
        return int(rows[0]["timeframe_id"])

    def create_queued(
        self,
        *,
        run_code: str,
        strategy_version_id: int,
        data_snapshot_id: int,
        universe_id: int,
        timeframe_id: int | None,
        base_currency_code: str,
        event_from: datetime,
        event_to: datetime,
        knowledge_cutoff_ts: datetime,
        availability_mode: ReplayMode,
        initial_capital: Decimal,
        parameters_json: str,
        parameter_sha256: str,
        execution_model_json: str,
        transaction_cost_model_json: str,
        random_seed: int,
        metadata_json: str,
    ) -> int:
        with translate_database_errors(operation="create queued backtest run"):
            return int(
                self._connection.execute(
                    text(
                        """
                        INSERT INTO backtest.run
                            (run_code, strategy_version_id, data_snapshot_id,
                             universe_id, timeframe_id, base_currency_code, event_from,
                             event_to, knowledge_cutoff_ts, availability_mode,
                             initial_capital, parameters, parameter_sha256, execution_model,
                             transaction_cost_model, engine_version, random_seed, metadata)
                        VALUES
                            (:run_code, :strategy_version_id, :data_snapshot_id, :universe_id,
                             :timeframe_id, :base_currency_code, :event_from, :event_to,
                             :knowledge_cutoff_ts, :availability_mode, :initial_capital,
                             CAST(:parameters AS jsonb), :parameter_sha256,
                             CAST(:execution_model AS jsonb),
                             CAST(:transaction_cost_model AS jsonb),
                             'reference-bar-v1', :random_seed, CAST(:metadata AS jsonb))
                        RETURNING run_id
                        """
                    ),
                    {
                        "run_code": run_code,
                        "strategy_version_id": strategy_version_id,
                        "data_snapshot_id": data_snapshot_id,
                        "universe_id": universe_id,
                        "timeframe_id": timeframe_id,
                        "base_currency_code": base_currency_code,
                        "event_from": event_from,
                        "event_to": event_to,
                        "knowledge_cutoff_ts": knowledge_cutoff_ts,
                        "availability_mode": availability_mode.value,
                        "initial_capital": initial_capital,
                        "parameters": parameters_json,
                        "parameter_sha256": parameter_sha256,
                        "execution_model": execution_model_json,
                        "transaction_cost_model": transaction_cost_model_json,
                        "random_seed": random_seed,
                        "metadata": metadata_json,
                    },
                ).scalar_one()
            )

    def insert_bindings(
        self,
        *,
        run_id: int,
        instruments: Sequence[int],
        series_bindings: Sequence[tuple[int, int, str, timedelta, bool]],
    ) -> None:
        with translate_database_errors(operation="insert backtest run bindings"):
            for instrument_id in instruments:
                self._connection.execute(
                    text(
                        """
                        INSERT INTO backtest.run_instrument (run_id, instrument_id)
                        VALUES (:run_id, :instrument_id)
                        """
                    ),
                    {"run_id": run_id, "instrument_id": instrument_id},
                )
            for instrument_id, series_id, role, lag, is_primary in series_bindings:
                self._connection.execute(
                    text(
                        """
                        INSERT INTO backtest.run_market_series
                            (run_id, bar_series_id, series_role, is_primary,
                             execution_lag, metadata)
                        VALUES
                            (:run_id, :bar_series_id, :series_role, :is_primary, :execution_lag,
                             CAST(:metadata AS jsonb))
                        """
                    ),
                    {
                        "run_id": run_id,
                        "bar_series_id": series_id,
                        "series_role": role,
                        "is_primary": is_primary,
                        "execution_lag": lag,
                        "metadata": '{"instrument_id":' + str(instrument_id) + "}",
                    },
                )

    def mark_running(self, run_id: int, *, now: datetime) -> None:
        self._transition(run_id, expected="QUEUED", status="RUNNING", now=now)

    def mark_failed(
        self, run_id: int, *, now: datetime, error_summary: str, metadata_json: str
    ) -> None:
        with translate_database_errors(operation="mark backtest failed"):
            self._connection.execute(
                text(
                    """
                    UPDATE backtest.run
                    SET status = 'FAILED', finished_at = :now, error_summary = :error_summary,
                        metadata = CAST(:metadata AS jsonb)
                    WHERE run_id = :run_id AND status = 'RUNNING'
                    """
                ),
                {
                    "run_id": run_id,
                    "now": now,
                    "error_summary": error_summary[:512],
                    "metadata": metadata_json,
                },
            )

    def mark_succeeded(self, run_id: int, *, now: datetime, metadata_json: str) -> None:
        self._transition(
            run_id, expected="RUNNING", status="SUCCEEDED", now=now, metadata_json=metadata_json
        )

    def require_runnable(self, run_code: str, run_spec_sha256: str) -> dict[str, object] | None:
        existing = self.get_by_code(run_code, for_update=True)
        if existing is None:
            return None
        status = str(existing["status"])
        existing_hash = (
            existing["metadata"].get("run_spec_sha256")
            if isinstance(existing["metadata"], dict)
            else None
        )
        if status == "SUCCEEDED" and existing_hash == run_spec_sha256:
            return existing
        if status in {"QUEUED", "RUNNING"}:
            raise BacktestRunInProgressError("Backtest run code is already queued or running.")
        raise BacktestRunConflictError("Backtest run code cannot be reused for another run.")

    def _transition(
        self,
        run_id: int,
        *,
        expected: str,
        status: str,
        now: datetime,
        metadata_json: str | None = None,
    ) -> None:
        values = (
            "status = :status, started_at = :now"
            if status == "RUNNING"
            else "status = :status, finished_at = :now"
        )
        if metadata_json is not None:
            values += ", metadata = CAST(:metadata AS jsonb)"
        with translate_database_errors(operation=f"mark backtest {status.lower()}"):
            result = self._connection.execute(
                text(
                    f"UPDATE backtest.run SET {values} "
                    "WHERE run_id = :run_id AND status = :expected"
                ),
                {
                    "run_id": run_id,
                    "status": status,
                    "now": now,
                    "expected": expected,
                    "metadata": metadata_json,
                },
            )
        if result.rowcount != 1:
            raise BacktestRunInProgressError("Backtest lifecycle state changed concurrently.")


__all__ = ["SqlAlchemyBacktestRunRepository"]
