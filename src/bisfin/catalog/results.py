"""Bounded, non-secret outcomes for deterministic catalog bootstrap."""

from pydantic import Field

from bisfin.domain.common import AwareDateTime, ImmutableDTO
from bisfin.domain.ingestion import IngestionBatchStatus


class CatalogBootstrapResult(ImmutableDTO):
    batch_id: int
    manifest_id: str
    status: IngestionBatchStatus
    providers_created: int = 0
    feeds_created: int = 0
    venues_created: int = 0
    timeframes_created: int = 0
    currencies_created: int = 0
    asset_types_created: int = 0
    instruments_created: int = 0
    instruments_unchanged: int = 0
    identifiers_created: int = 0
    identifiers_closed: int = 0
    spec_versions_created: int = 0
    entries_rejected: int = 0
    started_at: AwareDateTime
    finished_at: AwareDateTime
    payload_sha256: str
    idempotent_replay: bool = False
    validation_mode: str
    diagnostics: list[object] = Field(default_factory=list)


__all__ = ["CatalogBootstrapResult"]
