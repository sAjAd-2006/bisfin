"""Strict persistence-agnostic contracts for snapshot requests and results."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import ConfigDict, Field, field_validator, model_validator

from bisfin.domain.common import AwareDateTime, ImmutableDTO
from bisfin.domain.market_data import ReplayMode


class SnapshotStatus(StrEnum):
    BUILDING = "BUILDING"
    FROZEN = "FROZEN"
    FAILED = "FAILED"
    DEPRECATED = "DEPRECATED"


class SnapshotComponentKind(StrEnum):
    BAR_REVISION = "BAR_REVISION"


# The database contract calls this exact enum ReplayMode; an alias prevents a
# second incompatible snapshot-only representation.
SnapshotAvailabilityMode = ReplayMode


class SnapshotComponentSpec(ImmutableDTO):
    component_key: str = Field(min_length=1, max_length=160)
    kind: SnapshotComponentKind
    bar_series_id: int = Field(gt=0)
    event_from: AwareDateTime
    event_to: AwareDateTime
    allow_empty: bool = False

    @field_validator("component_key")
    @classmethod
    def validate_component_key(cls, value: str) -> str:
        if value.strip() != value or "\x00" in value or "\r" in value or "\n" in value:
            raise ValueError("component_key must be a bounded single-line value")
        return value

    @model_validator(mode="after")
    def validate_event_interval(self) -> SnapshotComponentSpec:
        if self.event_from >= self.event_to:
            raise ValueError("event_from must be before event_to")
        return self


class SnapshotBuildRequest(ImmutableDTO):
    schema_version: int
    snapshot_code: str = Field(min_length=1, max_length=128)
    knowledge_cutoff_ts: AwareDateTime
    availability_mode: ReplayMode
    components: tuple[SnapshotComponentSpec, ...]

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_request(self) -> SnapshotBuildRequest:
        if self.schema_version != 1:
            raise ValueError("unsupported schema_version")
        if not self.components:
            raise ValueError("components must not be empty")
        if len({item.component_key for item in self.components}) != len(self.components):
            raise ValueError("component_key values must be unique")
        if any(item.event_to > self.knowledge_cutoff_ts for item in self.components):
            raise ValueError("event_to must not be after knowledge_cutoff_ts")
        return self


class SnapshotComponentResult(ImmutableDTO):
    component_key: str
    kind: SnapshotComponentKind
    bar_series_id: int
    feed_id: int
    event_from: AwareDateTime
    event_to: AwareDateTime
    row_count: int = Field(ge=0)
    component_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    storage_uri: str
    relative_storage_path: str
    max_available_at: AwareDateTime | None = None
    max_system_available_at: AwareDateTime | None = None


class SnapshotBuildResult(ImmutableDTO):
    data_snapshot_id: int
    snapshot_code: str
    status: SnapshotStatus
    knowledge_cutoff_ts: AwareDateTime
    availability_mode: ReplayMode
    source_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    specification_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    components: tuple[SnapshotComponentResult, ...] = ()
    created_at: AwareDateTime
    frozen_at: AwareDateTime | None = None
    idempotent_replay: bool = False


class SnapshotVerificationIssue(ImmutableDTO):
    code: str
    component_key: str | None = None
    message: str


class SnapshotVerificationResult(ImmutableDTO):
    snapshot_code: str
    verified: bool
    artifact_verified: bool
    database_verified: bool | None = None
    database_drift: bool = False
    issues: tuple[SnapshotVerificationIssue, ...] = ()


def utc_now() -> datetime:
    """Indirection retained only for CLI composition; builders inject a clock."""

    from datetime import UTC

    return datetime.now(UTC)


__all__ = [
    "SnapshotAvailabilityMode",
    "SnapshotBuildRequest",
    "SnapshotBuildResult",
    "SnapshotComponentKind",
    "SnapshotComponentResult",
    "SnapshotComponentSpec",
    "SnapshotStatus",
    "SnapshotVerificationIssue",
    "SnapshotVerificationResult",
]
