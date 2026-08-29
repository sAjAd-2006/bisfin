"""Focused errors for deterministic reference-backtest operations."""

from bisfin.db.errors import BisfinError


class BacktestManifestError(BisfinError):
    """Raised when a run manifest is malformed, unsafe, or semantically invalid."""


class BacktestValidationError(BisfinError):
    """Raised when a valid manifest cannot bind to the requested frozen inputs."""


class BacktestRunConflictError(BisfinError):
    """Raised when a completed run code has a different semantic specification."""


class BacktestRunInProgressError(BisfinError):
    """Raised when an existing queued/running run code cannot be reused."""


class SnapshotArtifactUnavailableError(BisfinError):
    """Raised when a required frozen artifact cannot be verified or loaded."""


class StrategyVersionConflictError(BisfinError):
    """Raised when an immutable strategy version has an unexpected source hash."""


class BacktestExecutionError(BisfinError):
    """Raised when deterministic reference execution cannot be completed."""


class BacktestAccountingError(BisfinError):
    """Raised when a reference cash or position invariant is violated."""


class BacktestPersistenceError(BisfinError):
    """Raised when the all-or-nothing backtest ledger cannot be persisted."""


__all__ = [
    "BacktestAccountingError",
    "BacktestExecutionError",
    "BacktestManifestError",
    "BacktestPersistenceError",
    "BacktestRunConflictError",
    "BacktestRunInProgressError",
    "BacktestValidationError",
    "SnapshotArtifactUnavailableError",
    "StrategyVersionConflictError",
]
