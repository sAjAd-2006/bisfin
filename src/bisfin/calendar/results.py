"""Bounded, non-secret outcomes for explicit calendar imports."""

from pydantic import Field

from bisfin.domain.common import AwareDateTime, ImmutableDTO
from bisfin.domain.ingestion import IngestionBatchStatus


class CalendarImportResult(ImmutableDTO):
    batch_id: int
    calendar_id: str
    venue_code: str
    status: IngestionBatchStatus
    date_from: str
    date_to: str
    sessions_received: int
    trading_days_inserted: int = 0
    closed_days_inserted: int = 0
    sessions_unchanged: int = 0
    sessions_rejected: int = 0
    started_at: AwareDateTime
    finished_at: AwareDateTime
    payload_sha256: str
    idempotent_replay: bool = False
    diagnostics: list[object] = Field(default_factory=list)


__all__ = ["CalendarImportResult"]
