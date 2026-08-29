"""Lifecycle orchestration for the artifact-backed reference backtest engine."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from importlib.resources import files

from pydantic import ValidationError
from sqlalchemy.engine import Engine

from bisfin.backtest.engine import (
    ReferenceBacktestSimulator,
    SimulatedFill,
    SimulationResult,
)
from bisfin.backtest.errors import (
    BacktestPersistenceError,
    BacktestValidationError,
    SnapshotArtifactUnavailableError,
)
from bisfin.backtest.manifest import BacktestManifestDocument
from bisfin.backtest.results import result_sha256
from bisfin.backtest.selector import ArtifactBarSelector
from bisfin.backtest.snapshot_data import load_artifact_bars
from bisfin.db.errors import BisfinError
from bisfin.db.transaction import TransactionManager
from bisfin.repositories.backtest_ledger_repository import SqlAlchemyBacktestLedgerRepository
from bisfin.repositories.backtest_run_repository import SqlAlchemyBacktestRunRepository
from bisfin.repositories.snapshot_repository import SnapshotRecord, SqlAlchemySnapshotRepository
from bisfin.snapshots.contracts import SnapshotComponentResult, SnapshotStatus
from bisfin.snapshots.errors import SnapshotVerificationError
from bisfin.snapshots.serialization import canonical_json_bytes
from bisfin.snapshots.verifier import SnapshotVerifier


class BacktestRunResult:
    """Small non-secret result suitable for CLI output and idempotent replay."""

    def __init__(
        self, *, run_id: int, run_code: str, status: str, result_sha256: str | None
    ) -> None:
        self.run_id = run_id
        self.run_code = run_code
        self.status = status
        self.result_sha256 = result_sha256

    def model_dump(self, *, mode: str = "json") -> dict[str, object]:
        del mode
        return {
            "run_id": self.run_id,
            "run_code": self.run_code,
            "status": self.status,
            "result_sha256": self.result_sha256,
        }


class ReferenceBacktestService:
    """Use frozen artifacts for values and PostgreSQL only for metadata and lineage."""

    def __init__(self, engine: Engine, *, clock: Callable[[], datetime] | None = None) -> None:
        self._engine = engine
        self._transactions = TransactionManager(engine)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._simulator = ReferenceBacktestSimulator()

    def run(self, document: BacktestManifestDocument) -> BacktestRunResult:
        """Execute Phases A–E and finalize failed post-RUNNING work safely."""

        snapshot, components = self._preflight(document)
        selectors, component_by_key = self._load_selectors(document, components)
        run_id: int | None = None
        try:
            with self._transactions.begin() as connection:
                runs = SqlAlchemyBacktestRunRepository(connection)
                existing = runs.require_runnable(
                    document.request.run_code, document.run_spec_sha256
                )
                if existing is not None:
                    metadata = (
                        existing["metadata"] if isinstance(existing["metadata"], dict) else {}
                    )
                    value = metadata.get("result_sha256")
                    existing_run_id = existing["run_id"]
                    if not isinstance(existing_run_id, int):
                        raise BacktestPersistenceError("Stored backtest run identity is invalid.")
                    return BacktestRunResult(
                        run_id=existing_run_id,
                        run_code=str(existing["run_code"]),
                        status=str(existing["status"]),
                        result_sha256=value if isinstance(value, str) else None,
                    )
                strategy_version_id = runs.ensure_strategy_version(
                    class_path="bisfin.backtest.strategies:SmaCrossLongFlatStrategy",
                    code_sha256=_strategy_source_sha256(),
                )
                universe_id = runs.resolve_universe_id(document.request.universe_code)
                bindings, timeframe_id = _series_bindings(document, component_by_key, runs)
                run_id = runs.create_queued(
                    run_code=document.request.run_code,
                    strategy_version_id=strategy_version_id,
                    data_snapshot_id=snapshot.data_snapshot_id,
                    universe_id=universe_id,
                    timeframe_id=timeframe_id,
                    base_currency_code=document.request.base_currency_code,
                    event_from=document.request.event_from,
                    event_to=document.request.event_to,
                    knowledge_cutoff_ts=snapshot.knowledge_cutoff_ts,
                    availability_mode=snapshot.availability_mode,
                    initial_capital=document.request.initial_capital,
                    parameters_json=_json(document.request.strategy.parameters),
                    parameter_sha256=document.parameter_sha256,
                    execution_model_json=_json(document.request.execution_model.model_dump()),
                    transaction_cost_model_json=_json(
                        document.request.transaction_cost_model.model_dump()
                    ),
                    random_seed=document.request.random_seed,
                    metadata_json=_json(
                        {
                            "run_spec_sha256": document.run_spec_sha256,
                            "source_manifest_sha256": document.source_manifest_sha256,
                            "snapshot_code": document.request.snapshot_code,
                        }
                    ),
                )
                runs.insert_bindings(
                    run_id=run_id,
                    instruments=[item.instrument_id for item in document.request.instruments],
                    series_bindings=bindings,
                )
            with self._transactions.begin() as connection:
                SqlAlchemyBacktestRunRepository(connection).mark_running(run_id, now=self._clock())

            simulation = self._simulator.simulate(
                document.request,
                signal_selectors=selectors["SIGNAL"],
                execution_selectors=selectors["EXECUTION"],
                valuation_selectors=selectors["VALUATION"],
                run_spec_sha256=document.run_spec_sha256,
            )
            summary = _summary(document, simulation.equity_base, simulation)
            result_hash = result_sha256(simulation, summary=summary)
            metadata = {
                "run_spec_sha256": document.run_spec_sha256,
                "source_manifest_sha256": document.source_manifest_sha256,
                "snapshot_code": document.request.snapshot_code,
                "result_sha256": result_hash,
            }
            with self._transactions.begin() as connection:
                SqlAlchemyBacktestLedgerRepository(connection).persist(
                    run_id=run_id,
                    result=simulation,
                    initial_capital=document.request.initial_capital,
                    base_currency_code=document.request.base_currency_code,
                    summary=summary,
                )
                SqlAlchemyBacktestRunRepository(connection).mark_succeeded(
                    run_id, now=self._clock(), metadata_json=_json(metadata)
                )
            return BacktestRunResult(
                run_id=run_id,
                run_code=document.request.run_code,
                status="SUCCEEDED",
                result_sha256=result_hash,
            )
        except Exception as error:
            if run_id is not None:
                self._mark_failed(run_id, error, document)
            if isinstance(error, BisfinError):
                raise
            raise BacktestPersistenceError("Reference backtest execution failed.") from error

    def show(self, run_code: str) -> dict[str, object] | None:
        with self._transactions.begin(read_only=True) as connection:
            return SqlAlchemyBacktestRunRepository(connection).get_by_code(run_code)

    def _preflight(
        self, document: BacktestManifestDocument
    ) -> tuple[SnapshotRecord, tuple[SnapshotComponentResult, ...]]:
        try:
            verification = SnapshotVerifier(self._engine).verify(document.request.snapshot_code)
        except SnapshotVerificationError as error:
            raise SnapshotArtifactUnavailableError(
                "Frozen snapshot artifact verification failed."
            ) from error
        if not verification.verified or not verification.artifact_verified:
            raise SnapshotArtifactUnavailableError("Frozen snapshot artifact verification failed.")
        with self._transactions.begin(read_only=True) as connection:
            snapshots = SqlAlchemySnapshotRepository(connection)
            snapshot = snapshots.get_by_code(document.request.snapshot_code)
        if snapshot is None or snapshot.status is not SnapshotStatus.FROZEN:
            raise BacktestValidationError("Reference backtests require a FROZEN snapshot.")
        raw_components = snapshot.metadata.get("components")
        if not isinstance(raw_components, list):
            raise SnapshotArtifactUnavailableError(
                "Frozen snapshot component metadata is unavailable."
            )
        try:
            components = tuple(
                SnapshotComponentResult.model_validate(item) for item in raw_components
            )
        except ValidationError as error:
            raise SnapshotArtifactUnavailableError(
                "Frozen snapshot component metadata is invalid."
            ) from error
        return snapshot, components

    def _load_selectors(
        self,
        document: BacktestManifestDocument,
        components: tuple[SnapshotComponentResult, ...],
    ) -> tuple[dict[str, dict[int, ArtifactBarSelector]], dict[str, SnapshotComponentResult]]:
        component_by_key = {component.component_key: component for component in components}
        selectors: dict[str, dict[int, ArtifactBarSelector]] = {
            "SIGNAL": {},
            "EXECUTION": {},
            "VALUATION": {},
        }
        for instrument in document.request.instruments:
            bindings = {
                "SIGNAL": instrument.signal_component_key,
                "EXECUTION": instrument.execution_component_key,
                "VALUATION": instrument.valuation_component_key,
            }
            for role, key in bindings.items():
                component = component_by_key.get(key)
                if component is None:
                    raise BacktestValidationError(
                        "Run manifest references an unknown frozen component key."
                    )
                rows = load_artifact_bars(
                    component.storage_uri, expected_bar_series_id=component.bar_series_id
                )
                if not any(
                    document.request.event_from <= row.bar_open_ts < document.request.event_to
                    for row in rows
                ):
                    raise BacktestValidationError(
                        "Frozen component does not cover the requested event range."
                    )
                selectors[role][instrument.instrument_id] = ArtifactBarSelector(rows)
        return selectors, component_by_key

    def _mark_failed(
        self,
        run_id: int,
        error: Exception,
        document: BacktestManifestDocument,
    ) -> None:
        try:
            with self._transactions.begin() as connection:
                SqlAlchemyBacktestRunRepository(connection).mark_failed(
                    run_id,
                    now=self._clock(),
                    error_summary=type(error).__name__,
                    metadata_json=_json(
                        {
                            "run_spec_sha256": document.run_spec_sha256,
                            "failure": {"code": type(error).__name__},
                        }
                    ),
                )
        except Exception:
            return


def _series_bindings(
    document: BacktestManifestDocument,
    components: Mapping[str, SnapshotComponentResult],
    runs: SqlAlchemyBacktestRunRepository,
) -> tuple[list[tuple[int, int, str, timedelta, bool]], int | None]:
    bindings: list[tuple[int, int, str, timedelta, bool]] = []
    timeframe_id: int | None = None
    seen_primary: set[str] = set()
    for instrument in document.request.instruments:
        keys = {
            "SIGNAL": instrument.signal_component_key,
            "EXECUTION": instrument.execution_component_key,
            "VALUATION": instrument.valuation_component_key,
        }
        selected = [components[key].bar_series_id for key in keys.values()]
        candidate_timeframe = runs.validate_instrument_series(
            instrument_id=instrument.instrument_id,
            bar_series_ids=selected,
            base_currency_code=document.request.base_currency_code,
        )
        if timeframe_id is None:
            timeframe_id = candidate_timeframe
        for role, key in keys.items():
            bindings.append(
                (
                    instrument.instrument_id,
                    components[key].bar_series_id,
                    role,
                    timedelta(seconds=instrument.execution_lag_seconds)
                    if role == "EXECUTION"
                    else timedelta(),
                    role not in seen_primary,
                )
            )
            seen_primary.add(role)
    return bindings, timeframe_id


def _summary(
    document: BacktestManifestDocument,
    equity_base: Decimal,
    simulation: SimulationResult,
) -> dict[str, object]:
    final_equity = equity_base
    initial = document.request.initial_capital
    net_pnl = final_equity - initial
    fills = simulation.fills
    total_cost = sum(
        (
            fill.economics.commission_amount
            + fill.economics.slippage_amount
            + fill.economics.tax_amount
            for fill in fills
        ),
        initial * 0,
    )
    realized = sum((item.realized_pnl for item in simulation.positions.values()), initial * 0)
    closed_trade_pnls = _closed_trade_pnls(simulation)
    winning_trades = sum(1 for value in closed_trade_pnls if value > 0)
    losing_trades = sum(1 for value in closed_trade_pnls if value < 0)
    trade_count = len(closed_trade_pnls)
    return {
        "total_return": net_pnl / initial,
        "max_drawdown": Decimal("0"),
        "trade_count": trade_count,
        "winning_trade_count": winning_trades,
        "losing_trade_count": losing_trades,
        "win_rate": Decimal(winning_trades) / Decimal(trade_count) if trade_count else None,
        "gross_pnl_base": net_pnl + total_cost,
        "net_pnl_base": net_pnl,
        "total_cost_base": total_cost,
        "realized_pnl_base": realized,
    }


def _closed_trade_pnls(simulation: SimulationResult) -> tuple[Decimal, ...]:
    entries: dict[int, SimulatedFill] = {}
    completed: list[Decimal] = []
    for fill in simulation.fills:
        instrument_id = fill.order.signal.instrument_id
        if fill.economics.side == "BUY":
            entries[instrument_id] = fill
            continue
        entry = entries.pop(instrument_id, None)
        if entry is None:
            raise BacktestPersistenceError("Reference sell has no matching entry fill.")
        entry_cost = (
            entry.economics.commission_amount
            + entry.economics.slippage_amount
            + entry.economics.tax_amount
        )
        exit_cost = (
            fill.economics.commission_amount
            + fill.economics.slippage_amount
            + fill.economics.tax_amount
        )
        completed.append(
            (fill.economics.execution_price - entry.economics.execution_price)
            * fill.economics.quantity
            - entry_cost
            - exit_cost
        )
    return tuple(completed)


def _strategy_source_sha256() -> str:
    return hashlib.sha256(
        files("bisfin.backtest").joinpath("strategies.py").read_bytes()
    ).hexdigest()


def _json(value: object) -> str:
    return canonical_json_bytes(value).decode("utf-8")


__all__ = ["BacktestRunResult", "ReferenceBacktestService"]
