"""Secret-free result contracts returned by ingestion operations and the CLI."""

from __future__ import annotations

from bisfin.domain.common import AwareDateTime, ImmutableDTO
from bisfin.domain.ingestion import IngestionBatchStatus


class DailyBarIngestionResult(ImmutableDTO):
    """Bounded summary of one BrsApi daily-bar acquisition and canonicalization."""

    ingestion_batch_id: int
    status: IngestionBatchStatus
    provider_code: str
    feed_code: str
    symbol: str
    received_count: int
    accepted_count: int
    rejected_count: int
    raw_inserted_count: int
    bar_inserted_count: int
    bar_corrected_count: int
    bar_unchanged_count: int
    source_watermark: str | None = None
    payload_sha256: str | None = None
    started_at: AwareDateTime
    finished_at: AwareDateTime | None = None
    idempotent_replay: bool = False


__all__ = ["DailyBarIngestionResult"]
