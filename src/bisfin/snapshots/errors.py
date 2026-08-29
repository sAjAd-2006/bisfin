"""Bounded snapshot-builder failures suitable for CLI presentation."""

from bisfin.db.errors import BisfinError


class SnapshotManifestError(BisfinError, ValueError):
    """The source snapshot manifest is malformed or unsafe."""


class SnapshotConflictError(BisfinError):
    """A snapshot code already names a different immutable specification."""


class SnapshotInProgressError(SnapshotConflictError):
    """A caller attempted to reuse a BUILDING snapshot code."""


class SnapshotBuildError(BisfinError):
    """Snapshot construction failed after static manifest validation."""


class SnapshotArtifactError(SnapshotBuildError):
    """A local immutable artifact could not be safely written or read."""


class SnapshotVerificationError(BisfinError):
    """A frozen snapshot does not match its persisted artifact."""


class SnapshotDatabaseDriftError(SnapshotVerificationError):
    """Live PIT candidates differ from the frozen component artifact."""


__all__ = [
    "SnapshotArtifactError",
    "SnapshotBuildError",
    "SnapshotConflictError",
    "SnapshotDatabaseDriftError",
    "SnapshotInProgressError",
    "SnapshotManifestError",
    "SnapshotVerificationError",
]
